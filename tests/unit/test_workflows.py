from pathlib import Path

import pytest
import yaml

from caldera.stacks.addons import RUNNER_SCALE_SET_SUFFIX, runner_scale_set

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
    up = jobs("preview-environment.yml")["up"]

    assert up["environment"]["name"] == "preview-${{ needs.resolve.outputs.environment }}"
    assert up["environment"]["url"] == "${{ steps.deploy.outputs.url }}"
