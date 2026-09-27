import json

import pytest
from typer.testing import CliRunner

from caldera_cli import main

runner = CliRunner()


@pytest.fixture(autouse=True)
def no_external_calls(monkeypatch: pytest.MonkeyPatch) -> None:
    def fail(*args: object, **kwargs: object) -> None:
        raise AssertionError("unexpected external call")

    monkeypatch.setattr(main.subprocess, "run", fail)
    monkeypatch.setattr(main, "_github", fail)


def invoke(*args: str) -> tuple[int, str]:
    result = runner.invoke(main.app, list(args))
    return result.exit_code, result.output


def test_resolve_prints_plan_as_json() -> None:
    code, output = invoke(
        "vent",
        "resolve",
        "--repo",
        "tremor-api",
        "--branch",
        "feature/quake-alerts",
        "--other-has-branch",
    )

    assert code == 0
    assert json.loads(output) == {
        "vent": "quake-alerts",
        "release": "vent-quake-alerts",
        "action": "up",
        "refs": {"tremor": "feature/quake-alerts", "steward": "feature/quake-alerts"},
        "joins_existing": False,
    }


def test_resolve_skips_lookup_for_non_feature_branches() -> None:
    code, output = invoke("vent", "resolve", "--repo", "steward-api", "--branch", "fix-crew-sync")

    assert code == 0
    assert json.loads(output)["vent"] == "steward-fix-crew-sync"


def test_up_dry_run_prints_helm_command() -> None:
    code, output = invoke(
        "vent",
        "up",
        "--repo",
        "tremor-api",
        "--branch",
        "feature/quake-alerts",
        "--sha",
        "a1b2c3d4",
        "--other-missing-branch",
        "--other-sha",
        "0f9e8d7c",
        "--dry-run",
    )

    assert code == 0
    assert output.startswith("helm upgrade --install vent-quake-alerts charts/vent")
    assert "services.steward.image.tag=sha-0f9e8d7" in output


def test_down_dry_run_cools_vent_when_no_branch_remains() -> None:
    code, output = invoke(
        "vent",
        "down",
        "--repo",
        "tremor-api",
        "--branch",
        "feature/quake-alerts",
        "--other-missing-branch",
        "--dry-run",
    )

    assert code == 0
    assert output.splitlines() == [
        "helm uninstall vent-quake-alerts --namespace vent-quake-alerts --wait --ignore-not-found",
        "kubectl delete namespace vent-quake-alerts --wait=true --ignore-not-found",
    ]


def test_down_dry_run_redeploys_on_main_when_other_repo_keeps_branch() -> None:
    code, output = invoke(
        "vent",
        "down",
        "--repo",
        "steward-api",
        "--branch",
        "feature/quake-alerts",
        "--other-has-branch",
        "--main-sha",
        "1111111aaa",
        "--other-sha",
        "2222222bbb",
        "--dry-run",
    )

    assert code == 0
    assert "services.steward.image.tag=sha-1111111" in output
    assert "services.tremor.image.tag=sha-2222222" in output


def test_reset_dry_run() -> None:
    code, output = invoke("vent", "reset", "--vent", "quake-alerts", "--dry-run")

    assert code == 0
    assert "kubectl rollout restart deployment/postgres --namespace vent-quake-alerts" in output


def test_too_long_vent_name_exits_with_clear_error() -> None:
    code, output = invoke(
        "vent",
        "resolve",
        "--repo",
        "tremor-api",
        "--branch",
        "feature/" + "a" * 60,
        "--other-missing-branch",
    )

    assert code == 2
    assert "DNS allows 63" in output
