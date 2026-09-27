import json
import subprocess
from pathlib import Path

import pytest
from typer.testing import CliRunner

from preview_cli import main
from preview_cli.aws import Image

runner = CliRunner()
REPO_ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture(autouse=True)
def no_external_calls(monkeypatch: pytest.MonkeyPatch) -> None:
    def fail(*args: object, **kwargs: object) -> None:
        raise AssertionError("unexpected external call")

    monkeypatch.setattr(main.subprocess, "run", fail)
    monkeypatch.setattr(main, "_github", fail)
    monkeypatch.setattr(main, "_ecr", fail)
    monkeypatch.setattr(main, "_parameters", fail)
    monkeypatch.chdir(REPO_ROOT)
    monkeypatch.delenv("GITHUB_OUTPUT", raising=False)


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


def test_resolve_writes_the_environment_to_the_github_output(tmp_path: Path) -> None:
    github_output = tmp_path / "output"
    github_output.write_text("previous=1\n")

    result = runner.invoke(
        main.app,
        ["env", "resolve", "--repo", "steward-api", "--branch", "fix-crew-sync"],
        env={"GITHUB_OUTPUT": str(github_output)},
    )

    assert result.exit_code == 0
    assert github_output.read_text() == "previous=1\nenvironment=steward-fix-crew-sync\n"


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
        "--dataset-version",
        "ds-1",
        "--dry-run",
    )

    assert code == 0
    build, upgrade, _summary = output.splitlines()
    assert build == "helm dependency build charts/services"
    assert upgrade.startswith("helm upgrade --install preview-quake-alerts charts/services")
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


def test_down_dry_run_tears_down_when_no_branch_remains() -> None:
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
        "--dataset-version",
        "ds-1",
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


DIGEST = "sha256:" + "d" * 64
REGISTRY = "123456789012.dkr.ecr.us-east-1.amazonaws.com"
UP_ARGS = (
    "env",
    "up",
    "--repo",
    "tremor-api",
    "--branch",
    "feature/quake-alerts",
    "--sha",
    "a1b2c3d4",
    "--branch-in",
    "steward",
    "--sha-for",
    "steward=0f9e8d7c",
)


class FakeEcr:
    def __init__(self, tags: set[tuple[str, str]]) -> None:
        self.tags = tags
        self.registry: str | None = None

    def find(self, repository: str, tag: str) -> Image | None:
        if (repository, tag) not in self.tags:
            return None
        self.registry = REGISTRY
        return Image(tag, DIGEST)


class FakeParameters:
    def __init__(self, values: dict[str, str]) -> None:
        self.values = values

    def get(self, name: str) -> str | None:
        return self.values.get(name)


PARAMETERS = {
    "/prismatic/golden-db/dataset-version": "ds-42",
    "/prismatic/preview/domain": "preview.example.com",
}
ALL_IMAGES = {
    ("tremor-api", "sha-a1b2c3d"),
    ("steward-api", "main"),
    ("golden-db", "ds-42"),
}


@pytest.fixture
def aws(monkeypatch: pytest.MonkeyPatch):
    def install(images: set[tuple[str, str]], parameters: dict[str, str]) -> None:
        monkeypatch.setattr(main, "_ecr", lambda: FakeEcr(images))
        monkeypatch.setattr(main, "_parameters", lambda: FakeParameters(parameters))

    return install


def test_offline_up_requires_an_explicit_dataset_version() -> None:
    code, output = invoke(*UP_ARGS, "--offline", "--dry-run")

    assert code == 2
    assert "--offline needs --dataset-version" in output


def test_up_pins_digests_dataset_and_domain_from_aws(aws, tmp_path: Path) -> None:
    aws(ALL_IMAGES, PARAMETERS)
    github_output = tmp_path / "output"

    result = runner.invoke(
        main.app, [*UP_ARGS, "--dry-run"], env={"GITHUB_OUTPUT": str(github_output)}
    )

    assert result.exit_code == 0, result.output
    assert f"services.tremor.image.tag=sha-a1b2c3d@{DIGEST}" in result.output
    assert f"services.steward.image.tag=main@{DIGEST}" in result.output
    assert f"postgres.image.tag=ds-42@{DIGEST}" in result.output
    assert "datasetVersion=ds-42" in result.output
    assert "domain=preview.example.com" in result.output
    assert f"image.registry={REGISTRY}" in result.output
    summary = json.loads(result.output.splitlines()[-1])
    assert summary["images"]["tremor"] == {"tag": f"sha-a1b2c3d@{DIGEST}", "source": "sha"}
    assert summary["images"]["steward"]["source"] == "main"
    assert summary["urls"] == {
        "tremor": "https://tremor-quake-alerts.preview.example.com",
        "steward": "https://steward-quake-alerts.preview.example.com",
    }
    outputs = github_output.read_text().splitlines()
    assert "environment=quake-alerts" in outputs
    assert "exact-image=true" in outputs
    assert json.loads(next(o for o in outputs if o.startswith("result="))[7:]) == summary


def test_up_reports_an_inexact_image_while_the_push_is_still_building(aws) -> None:
    aws({("tremor-api", "main"), ("steward-api", "main"), ("golden-db", "ds-42")}, PARAMETERS)

    code, output = invoke(*UP_ARGS, "--dry-run")

    assert code == 0, output
    assert json.loads(output.splitlines()[-1])["images"]["tremor"]["source"] == "main"


def test_dataset_version_flag_overrides_ssm(aws) -> None:
    aws(ALL_IMAGES | {("golden-db", "ds-7")}, PARAMETERS)

    code, output = invoke(*UP_ARGS, "--dataset-version", "ds-7", "--dry-run")

    assert code == 0, output
    assert "datasetVersion=ds-7" in output


def test_missing_dataset_version_parameter_fails_clearly(aws) -> None:
    aws(ALL_IMAGES, {})

    code, output = invoke(*UP_ARGS, "--dry-run")

    assert code == 2
    assert "/prismatic/golden-db/dataset-version is not set" in output
    assert "golden-image" in output


def test_missing_golden_image_fails_clearly(aws) -> None:
    aws(ALL_IMAGES - {("golden-db", "ds-42")}, PARAMETERS)

    code, output = invoke(*UP_ARGS, "--dry-run")

    assert code == 2
    assert "golden-db:ds-42 is not in ECR" in output


def test_up_reuses_the_current_image_and_clears_a_pending_release(
    aws, monkeypatch: pytest.MonkeyPatch
) -> None:
    aws({("steward-api", "sha-0f9e8d7"), ("golden-db", "ds-42")}, PARAMETERS)
    status = {
        "info": {"status": "pending-upgrade"},
        "version": 4,
        "config": {"services": {"tremor": {"image": {"tag": "sha-9999999@sha256:9"}}}},
    }
    calls: list[list[str]] = []

    def run(command: list[str], **kwargs: object) -> subprocess.CompletedProcess:
        calls.append(command)
        stdout = {"list": "preview-quake-alerts\n", "status": json.dumps(status)}.get(
            command[1], ""
        )
        return subprocess.CompletedProcess(command, 0, stdout, "")

    monkeypatch.setattr(main.subprocess, "run", run)

    code, output = invoke(*UP_ARGS)

    assert code == 0, output
    assert [c[1] for c in calls] == ["list", "status", "rollback", "dependency", "upgrade"]
    assert "services.tremor.image.tag=sha-9999999@sha256:9" in calls[-1]
    assert json.loads(output.splitlines()[-1])["images"]["tremor"]["source"] == "current"


def test_test_dry_run_prints_the_helm_test_command() -> None:
    code, output = invoke("env", "test", "--name", "quake-alerts", "--dry-run")

    assert code == 0
    assert output.splitlines() == [
        "helm test preview-quake-alerts --namespace preview-quake-alerts --logs --timeout 5m"
    ]


def test_failed_command_exits_with_its_code(monkeypatch: pytest.MonkeyPatch) -> None:
    def run(command: list[str], **kwargs: object) -> None:
        raise subprocess.CalledProcessError(3, command)

    monkeypatch.setattr(main.subprocess, "run", run)

    code, output = invoke("env", "test", "--name", "quake-alerts")

    assert code == 3
    assert "error: helm test preview-quake-alerts" in output
    assert "exit code 3" in output
