import json

import pytest

from preview_cli.commands import (
    DeployedRelease,
    Target,
    apply_command,
    down_commands,
    helm_test_commands,
    image_tag,
    list_environments_command,
    parse_release,
    recover_commands,
    reset_commands,
    status_command,
    up_commands,
)
from preview_cli.registry import ServiceSpec
from preview_cli.resolver import Action, PreviewPlan

SERVICES = (
    ServiceSpec("tremor", "tremor-api", "tremor-api"),
    ServiceSpec("steward", "steward-api", "steward-api", 8080),
)
PLAN = PreviewPlan("quake-alerts", Action.UP, {"tremor": "feature/quake-alerts", "steward": "main"})
TAGS = {"tremor": "sha-a1b2c3d", "steward": "sha-0f9e8d7@sha256:0f"}


def flag_values(command: list[str], flag: str) -> list[str]:
    return [command[i + 1] for i, arg in enumerate(command) if arg == flag]


def test_image_tag_uses_short_sha() -> None:
    assert image_tag("a1b2c3d4e5f6") == "sha-a1b2c3d"


def test_up_builds_chart_dependencies_first() -> None:
    build, _ = up_commands(PLAN, SERVICES, TAGS, "ds-42", Target())

    assert build == ["helm", "dependency", "build", "charts/services"]


def test_up_runs_helm_upgrade_install_into_the_environment_namespace() -> None:
    _, command = up_commands(PLAN, SERVICES, TAGS, "ds-42", Target(registry="123.dkr.ecr.aws"))

    assert command[:5] == [
        "helm",
        "upgrade",
        "--install",
        "preview-quake-alerts",
        "charts/services",
    ]
    assert "--create-namespace" in command
    assert "--wait" in command
    assert command[command.index("--namespace") + 1] == "preview-quake-alerts"
    assert flag_values(command, "--set-string") == [
        "environment.name=quake-alerts",
        "datasetVersion=ds-42",
        "environment.kind=preview",
        "priorityClassName=preview-environment",
        "routeLabels.prismatic\\.dev/exposure=preview",
        "services.tremor.image.repository=tremor-api",
        "services.tremor.image.tag=sha-a1b2c3d",
        "services.steward.image.repository=steward-api",
        "services.steward.image.tag=sha-0f9e8d7@sha256:0f",
        "image.registry=123.dkr.ecr.aws",
    ]
    assert flag_values(command, "--set") == [
        "services.tremor.port=8000",
        "services.steward.port=8080",
    ]


def test_up_sets_values_for_every_registered_service() -> None:
    services = (*SERVICES, ServiceSpec("magma", "magma-api", "magma-api"))
    plan = PreviewPlan("x", Action.UP, {"tremor": "main", "steward": "main", "magma": "feature/x"})

    _, command = up_commands(plan, services, {**TAGS, "magma": "sha-abcdef0"}, "ds", Target())

    assert "services.magma.image.tag=sha-abcdef0" in flag_values(command, "--set-string")


def test_up_passes_the_kube_context_to_helm() -> None:
    _, command = up_commands(PLAN, SERVICES, TAGS, "ds-42", Target(context="kind-caldera"))

    assert command[-2:] == ["--kube-context", "kind-caldera"]


def test_list_environments_reads_labelled_preview_namespaces() -> None:
    assert list_environments_command(Target(context="kind-caldera")) == [
        "kubectl",
        "get",
        "namespaces",
        "--selector",
        "prismatic.dev/environment-kind=preview",
        "--output",
        "jsonpath={.items[*].metadata.name}",
        "--context",
        "kind-caldera",
    ]


def test_apply_reads_manifests_from_stdin() -> None:
    assert apply_command(Target(context="kind-caldera")) == [
        "kubectl",
        "apply",
        "--filename",
        "-",
        "--context",
        "kind-caldera",
    ]


def test_down_uninstalls_release_then_deletes_namespace() -> None:
    commands = down_commands("quake-alerts", Target(context="kind-caldera"))

    assert [c[:3] for c in commands] == [
        ["helm", "uninstall", "preview-quake-alerts"],
        ["kubectl", "delete", "namespace"],
    ]
    assert commands[1][3] == "preview-quake-alerts"
    assert commands[1][-2:] == ["--context", "kind-caldera"]


def test_reset_restarts_postgres_and_waits() -> None:
    commands = reset_commands("quake-alerts", Target())

    assert commands[0][:4] == ["kubectl", "rollout", "restart", "deployment/postgres"]
    assert commands[1][:4] == ["kubectl", "rollout", "status", "deployment/postgres"]
    assert all("preview-quake-alerts" in command for command in commands)


def test_up_sets_domain_and_pins_the_golden_image_when_known() -> None:
    _, command = up_commands(
        PLAN,
        SERVICES,
        TAGS,
        "ds-42",
        Target(),
        domain="preview.example.com",
        golden_tag="ds-42@sha256:9",
    )

    values = flag_values(command, "--set-string")
    assert "domain=preview.example.com" in values
    assert "postgres.image.tag=ds-42@sha256:9" in values


def test_test_runs_the_helm_test_hooks_with_logs() -> None:
    assert helm_test_commands("quake-alerts", Target()) == [
        [
            "helm",
            "test",
            "preview-quake-alerts",
            "--namespace",
            "preview-quake-alerts",
            "--logs",
            "--timeout",
            "5m",
        ]
    ]


def test_status_reads_the_release_as_json() -> None:
    assert status_command("preview-x", Target(context="kind-caldera")) == [
        "helm",
        "status",
        "preview-x",
        "--namespace",
        "preview-x",
        "--output",
        "json",
        "--kube-context",
        "kind-caldera",
    ]


def test_parse_release_reads_status_revision_and_service_tags() -> None:
    status = {
        "info": {"status": "deployed"},
        "version": 3,
        "config": {
            "services": {
                "tremor": {"image": {"tag": "sha-1111111@sha256:1"}},
                "steward": {"port": 8000},
            }
        },
    }

    assert parse_release(json.dumps(status)) == DeployedRelease(
        "deployed", 3, {"tremor": "sha-1111111@sha256:1"}
    )


@pytest.mark.parametrize(
    ("release", "expected"),
    [
        (DeployedRelease("deployed", 2, {}), []),
        (DeployedRelease("failed", 2, {}), []),
        (
            DeployedRelease("pending-upgrade", 2, {}),
            [["helm", "rollback", "preview-x", "--namespace", "preview-x", "--wait"]],
        ),
        (
            DeployedRelease("pending-install", 1, {}),
            [["helm", "uninstall", "preview-x", "--namespace", "preview-x", "--wait"]],
        ),
    ],
)
def test_recover_clears_a_release_left_pending_by_a_cancelled_deploy(
    release: DeployedRelease, expected: list[list[str]]
) -> None:
    assert recover_commands("preview-x", release, Target()) == expected
