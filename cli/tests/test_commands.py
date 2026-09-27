from caldera_cli.commands import Target, down_commands, image_tag, reset_commands, up_commands
from caldera_cli.registry import ServiceSpec
from caldera_cli.resolver import Action, VentPlan

SERVICES = (
    ServiceSpec("tremor", "tremor-api", "tremor-api"),
    ServiceSpec("steward", "steward-api", "steward-api", 8080),
)
PLAN = VentPlan("quake-alerts", Action.UP, {"tremor": "feature/quake-alerts", "steward": "main"})
SHAS = {"tremor": "a1b2c3d4e5f6", "steward": "0f9e8d7c6b5a"}


def flag_values(command: list[str], flag: str) -> list[str]:
    return [command[i + 1] for i, arg in enumerate(command) if arg == flag]


def test_image_tag_uses_short_sha() -> None:
    assert image_tag("a1b2c3d4e5f6") == "sha-a1b2c3d"


def test_up_runs_helm_upgrade_install_into_the_vent_namespace() -> None:
    [command] = up_commands(PLAN, SERVICES, SHAS, "ds-42", Target(registry="123.dkr.ecr.aws"))

    assert command[:5] == ["helm", "upgrade", "--install", "vent-quake-alerts", "charts/vent"]
    assert "--create-namespace" in command
    assert "--wait" in command
    assert command[command.index("--namespace") + 1] == "vent-quake-alerts"
    assert flag_values(command, "--set-string") == [
        "vent.name=quake-alerts",
        "datasetVersion=ds-42",
        "services.tremor.image.repository=tremor-api",
        "services.tremor.image.tag=sha-a1b2c3d",
        "services.steward.image.repository=steward-api",
        "services.steward.image.tag=sha-0f9e8d7",
        "image.registry=123.dkr.ecr.aws",
    ]
    assert flag_values(command, "--set") == [
        "services.tremor.port=8000",
        "services.steward.port=8080",
    ]


def test_up_sets_values_for_every_registered_service() -> None:
    services = (*SERVICES, ServiceSpec("magma", "magma-api", "magma-api"))
    plan = VentPlan("x", Action.UP, {"tremor": "main", "steward": "main", "magma": "feature/x"})

    [command] = up_commands(plan, services, {**SHAS, "magma": "abcdef0"}, "ds", Target())

    assert "services.magma.image.tag=sha-abcdef0" in flag_values(command, "--set-string")


def test_up_passes_the_kube_context_to_helm() -> None:
    [command] = up_commands(PLAN, SERVICES, SHAS, "ds-42", Target(context="kind-caldera"))

    assert command[-2:] == ["--kube-context", "kind-caldera"]


def test_down_uninstalls_release_then_deletes_namespace() -> None:
    commands = down_commands("quake-alerts", Target(context="kind-caldera"))

    assert [c[:3] for c in commands] == [
        ["helm", "uninstall", "vent-quake-alerts"],
        ["kubectl", "delete", "namespace"],
    ]
    assert commands[1][3] == "vent-quake-alerts"
    assert commands[1][-2:] == ["--context", "kind-caldera"]


def test_reset_restarts_postgres_and_waits() -> None:
    commands = reset_commands("quake-alerts", Target())

    assert commands[0][:4] == ["kubectl", "rollout", "restart", "deployment/postgres"]
    assert commands[1][:4] == ["kubectl", "rollout", "status", "deployment/postgres"]
    assert all("vent-quake-alerts" in command for command in commands)
