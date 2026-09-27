import json
from pathlib import Path

import pytest
from typer.testing import CliRunner

from preview_cli import main

runner = CliRunner()
REPO_ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture(autouse=True)
def no_external_calls(monkeypatch: pytest.MonkeyPatch) -> None:
    def fail(*args: object, **kwargs: object) -> None:
        raise AssertionError("unexpected external call")

    monkeypatch.setattr(main.subprocess, "run", fail)
    monkeypatch.setattr(main, "_github", fail)
    monkeypatch.chdir(REPO_ROOT)


def invoke(*args: str) -> tuple[int, str]:
    result = runner.invoke(main.app, list(args))
    return result.exit_code, result.output


def test_resolve_prints_plan_as_json() -> None:
    code, output = invoke(
        "env",
        "resolve",
        "--repo",
        "tremor-api",
        "--branch",
        "feature/quake-alerts",
        "--branch-in",
        "steward",
    )

    assert code == 0
    assert json.loads(output) == {
        "environment": "quake-alerts",
        "release": "preview-quake-alerts",
        "action": "up",
        "refs": {"tremor": "feature/quake-alerts", "steward": "feature/quake-alerts"},
        "joins_existing": False,
    }


def test_resolve_skips_lookup_for_non_feature_branches() -> None:
    code, output = invoke("env", "resolve", "--repo", "steward-api", "--branch", "fix-crew-sync")

    assert code == 0
    assert json.loads(output)["environment"] == "steward-fix-crew-sync"


def test_resolve_reads_a_custom_registry(tmp_path: Path) -> None:
    services = tmp_path / "services.yaml"
    services.write_text(
        "services:\n"
        "  - {name: tremor, repo: tremor-api, image: tremor-api}\n"
        "  - {name: magma, repo: magma-api, image: magma-api}\n"
    )

    code, output = invoke(
        "env",
        "resolve",
        "--repo",
        "magma-api",
        "--branch",
        "feature/x",
        "--offline",
        "--services-file",
        str(services),
    )

    assert code == 0
    assert json.loads(output)["refs"] == {"tremor": "main", "magma": "feature/x"}


def test_up_dry_run_prints_helm_command() -> None:
    code, output = invoke(
        "env",
        "up",
        "--repo",
        "tremor-api",
        "--branch",
        "feature/quake-alerts",
        "--sha",
        "a1b2c3d4",
        "--offline",
        "--sha-for",
        "steward=0f9e8d7c",
        "--dry-run",
    )

    assert code == 0
    assert output.startswith("helm upgrade --install preview-quake-alerts charts/vent")
    assert "services.steward.image.tag=sha-0f9e8d7" in output


def test_offline_up_without_every_sha_fails_clearly() -> None:
    code, output = invoke(
        "env",
        "up",
        "--repo",
        "tremor-api",
        "--branch",
        "feature/quake-alerts",
        "--sha",
        "a1b2c3d4",
        "--offline",
        "--dry-run",
    )

    assert code == 2
    assert "--offline needs --sha-for steward=<sha>" in output


def test_down_dry_run_cools_vent_when_no_branch_remains() -> None:
    code, output = invoke(
        "env",
        "down",
        "--repo",
        "tremor-api",
        "--branch",
        "feature/quake-alerts",
        "--offline",
        "--dry-run",
    )

    assert code == 0
    assert output.splitlines() == [
        "helm uninstall preview-quake-alerts --namespace preview-quake-alerts --wait "
        "--ignore-not-found",
        "kubectl delete namespace preview-quake-alerts --wait=true --ignore-not-found",
    ]


def test_down_dry_run_redeploys_on_main_when_another_repo_keeps_branch() -> None:
    code, output = invoke(
        "env",
        "down",
        "--repo",
        "steward-api",
        "--branch",
        "feature/quake-alerts",
        "--branch-in",
        "tremor",
        "--offline",
        "--sha-for",
        "steward=1111111aaa",
        "--sha-for",
        "tremor=2222222bbb",
        "--dry-run",
    )

    assert code == 0
    assert "services.steward.image.tag=sha-1111111" in output
    assert "services.tremor.image.tag=sha-2222222" in output


def test_reset_dry_run() -> None:
    code, output = invoke("env", "reset", "--name", "quake-alerts", "--dry-run")

    assert code == 0
    assert "kubectl rollout restart deployment/postgres --namespace preview-quake-alerts" in output


@pytest.mark.parametrize(
    ("args", "message"),
    [
        (("--branch", "feature/" + "a" * 60), "DNS allows 63"),
        (("--branch", "feature/x", "--branch-in", "magma"), "unknown service 'magma'"),
        (("--branch", "feature/x", "--services-file", "missing.yaml"), "missing.yaml"),
    ],
)
def test_invalid_input_exits_with_clear_error(args: tuple[str, ...], message: str) -> None:
    code, output = invoke("env", "resolve", "--repo", "tremor-api", "--offline", *args)

    assert code == 2
    assert message in output
