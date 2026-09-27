from pathlib import Path

import pytest
import yaml

from cdk.stacks.addons import RUNNER_SCALE_SET_SUFFIX, runner_scale_set

WORKFLOWS = Path(__file__).resolve().parents[2] / ".github" / "workflows"
PREVIEW_WORKFLOWS = ("preview-environment.yml", "preview-environment-teardown.yml")
CALLER_SCALE_SET = "${{ github.event.repository.name }}" + RUNNER_SCALE_SET_SUFFIX


def jobs(workflow: str) -> dict[str, dict]:
    return yaml.safe_load((WORKFLOWS / workflow).read_text())["jobs"]


def test_scale_set_name_is_the_repo_plus_the_suffix() -> None:
    assert runner_scale_set("tremor-api") == "tremor-api-runners"


@pytest.mark.parametrize("workflow", PREVIEW_WORKFLOWS)
def test_preview_jobs_run_on_the_callers_arc_scale_set(workflow: str) -> None:
    for name, job in jobs(workflow).items():
        assert job["runs-on"] == CALLER_SCALE_SET, f"{workflow}:{name}"


@pytest.mark.parametrize("workflow", PREVIEW_WORKFLOWS)
def test_preview_jobs_never_run_for_pull_requests(workflow: str) -> None:
    for name, job in jobs(workflow).items():
        if "needs" not in job:
            assert "github.event_name != 'pull_request'" in job["if"], f"{workflow}:{name}"
            assert "github.event_name != 'pull_request_target'" in job["if"], f"{workflow}:{name}"


@pytest.mark.parametrize("workflow", PREVIEW_WORKFLOWS)
def test_preview_workflows_deploy_for_real(workflow: str) -> None:
    assert "--dry-run" not in (WORKFLOWS / workflow).read_text()


@pytest.mark.parametrize("workflow", (*PREVIEW_WORKFLOWS, "golden-image.yml"))
def test_cross_repo_checkouts_use_a_github_app_installation_token(workflow: str) -> None:
    for name, job in jobs(workflow).items():
        steps = job["steps"]
        token_steps = [i for i, step in enumerate(steps) if step.get("id") == "app-token"]
        for i, step in enumerate(steps):
            if step.get("with", {}).get("repository"):
                assert token_steps and token_steps[0] < i, f"{workflow}:{name}"
                assert step["with"]["token"] == "${{ steps.app-token.outputs.token }}"
    assert "CALDERA_TOKEN" not in (WORKFLOWS / workflow).read_text()


@pytest.mark.parametrize("workflow", (*PREVIEW_WORKFLOWS, "golden-image.yml"))
def test_region_has_no_silent_default(workflow: str) -> None:
    text = (WORKFLOWS / workflow).read_text()
    assert "AWS_REGION: ${{ vars.AWS_REGION }}" in text
    assert "us-east-1" not in text


@pytest.mark.parametrize("workflow", (*PREVIEW_WORKFLOWS, "golden-image.yml"))
def test_jobs_check_their_configuration_before_minting_a_token(workflow: str) -> None:
    for name, job in jobs(workflow).items():
        steps = job["steps"]
        token = next(i for i, step in enumerate(steps) if step.get("id") == "app-token")
        checks = [i for i, step in enumerate(steps) if step.get("id") == "config"]
        assert checks and checks[0] < token, f"{workflow}:{name}"
        env = steps[checks[0]]["env"]
        assert env["CLIENT_ID"] == "${{ secrets.CALDERA_APP_CLIENT_ID }}"
        assert env["PRIVATE_KEY"] == "${{ secrets.CALDERA_APP_PRIVATE_KEY }}"


def steps(workflow: str, job: str) -> list[dict]:
    return jobs(workflow)[job]["steps"]


def test_e2e_runs_only_against_the_pushed_image() -> None:
    e2e = next(step for step in steps("preview-environment.yml", "up") if step.get("id") == "e2e")

    assert e2e["run"] == 'uv run preview env test --name "$ENVIRONMENT"'
    assert e2e["if"] == "steps.deploy.outputs.exact-image == 'true'"


def test_the_report_script_publishes_a_check_named_e2e() -> None:
    script = WORKFLOWS.parent / "scripts" / "preview-report.cjs"

    assert 'const CHECK_NAME = "e2e";' in script.read_text()
    assert "preview-report.cjs" in (WORKFLOWS / "preview-environment.yml").read_text()


def test_each_preview_deploy_is_a_github_deployment() -> None:
    record = next(
        step for step in steps("preview-environment.yml", "up") if step.get("id") == "deployment"
    )

    assert record["if"] == "steps.deploy.outcome == 'success'"
    assert record["env"]["ENVIRONMENT"] == "${{ steps.deploy.outputs.environment }}"
    assert record["env"]["URL"] == "${{ steps.deploy.outputs.url }}"
    assert "recordDeployment" in record["with"]["script"]


@pytest.mark.parametrize("workflow", PREVIEW_WORKFLOWS)
def test_preview_workflows_run_in_one_job(workflow: str) -> None:
    assert len(jobs(workflow)) == 1


def test_deploys_and_teardowns_of_one_branch_share_a_concurrency_group() -> None:
    up = jobs("preview-environment.yml")["up"]["concurrency"]
    down = jobs("preview-environment-teardown.yml")["down"]["concurrency"]

    assert up["group"] == down["group"] == "preview-${{ inputs.repo }}-${{ inputs.branch }}"
    assert up["cancel-in-progress"] is True
    assert down["cancel-in-progress"] is False


@pytest.mark.parametrize("workflow", PREVIEW_WORKFLOWS)
def test_preview_jobs_install_only_the_cli(workflow: str) -> None:
    for name, job in jobs(workflow).items():
        runs = [step["run"] for step in job["steps"] if "uv sync" in step.get("run", "")]

        assert runs == ["uv sync --locked --package preview-cli --no-dev"], f"{workflow}:{name}"
        assert job["env"]["UV_NO_SYNC"] == "1", f"{workflow}:{name}"
