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
