import json
from dataclasses import dataclass

from preview_cli.registry import ServiceSpec
from preview_cli.resolver import PreviewPlan

Command = list[str]


@dataclass(frozen=True)
class Target:
    chart: str = "charts/services"
    context: str | None = None
    registry: str | None = None
    timeout: str = "5m"


def _helm_context(target: Target) -> list[str]:
    return ["--kube-context", target.context] if target.context else []


def _kubectl_context(target: Target) -> list[str]:
    return ["--context", target.context] if target.context else []


PREVIEW_VALUES = (
    "environment.kind=preview",
    "priorityClassName=preview-environment",
    "routeLabels.prismatic\\.dev/exposure=preview",
)


def image_tag(sha: str) -> str:
    return f"sha-{sha[:7]}"


@dataclass(frozen=True)
class DeployedRelease:
    status: str
    revision: int
    service_tags: dict[str, str]


def up_commands(
    plan: PreviewPlan,
    services: tuple[ServiceSpec, ...],
    tags: dict[str, str],
    dataset_version: str,
    target: Target,
    *,
    domain: str | None = None,
    golden_tag: str | None = None,
) -> list[Command]:
    namespace = plan.release
    strings = [
        f"environment.name={plan.environment}",
        f"datasetVersion={dataset_version}",
        *PREVIEW_VALUES,
    ]
    if domain:
        strings.append(f"domain={domain}")
    if golden_tag:
        strings.append(f"postgres.image.tag={golden_tag}")
    numbers = []
    for service in services:
        strings += [
            f"services.{service.name}.image.repository={service.image}",
            f"services.{service.name}.image.tag={tags[service.name]}",
        ]
        numbers.append(f"services.{service.name}.port={service.port}")
    if target.registry:
        strings.append(f"image.registry={target.registry}")
    command = [
        "helm",
        "upgrade",
        "--install",
        plan.release,
        target.chart,
        "--namespace",
        namespace,
        "--create-namespace",
        "--wait",
        "--timeout",
        target.timeout,
    ]
    for value in strings:
        command += ["--set-string", value]
    for value in numbers:
        command += ["--set", value]
    return [["helm", "dependency", "build", target.chart], command + _helm_context(target)]


def list_environments_command(target: Target) -> Command:
    return [
        "helm",
        "list",
        "--all-namespaces",
        "--short",
        "--filter",
        "^preview-",
    ] + _helm_context(target)


def down_commands(environment: str, target: Target) -> list[Command]:
    release = f"preview-{environment}"
    return [
        ["helm", "uninstall", release, "--namespace", release, "--wait", "--ignore-not-found"]
        + _helm_context(target),
        ["kubectl", "delete", "namespace", release, "--wait=true", "--ignore-not-found"]
        + _kubectl_context(target),
    ]


def reset_commands(environment: str, target: Target) -> list[Command]:
    namespace = f"preview-{environment}"
    return [
        ["kubectl", "rollout", "restart", "deployment/postgres", "--namespace", namespace]
        + _kubectl_context(target),
        [
            "kubectl",
            "rollout",
            "status",
            "deployment/postgres",
            "--namespace",
            namespace,
            "--timeout",
            "60s",
        ]
        + _kubectl_context(target),
    ]


def helm_test_commands(environment: str, target: Target) -> list[Command]:
    release = f"preview-{environment}"
    return [
        ["helm", "test", release, "--namespace", release, "--logs", "--timeout", target.timeout]
        + _helm_context(target)
    ]


def status_command(release: str, target: Target) -> Command:
    return [
        "helm",
        "status",
        release,
        "--namespace",
        release,
        "--output",
        "json",
    ] + _helm_context(target)


def parse_release(status_json: str) -> DeployedRelease:
    release = json.loads(status_json)
    services = (release.get("config") or {}).get("services") or {}
    tags = {
        name: values["image"]["tag"]
        for name, values in services.items()
        if values.get("image", {}).get("tag")
    }
    return DeployedRelease(release["info"]["status"], int(release["version"]), tags)


def recover_commands(release: str, deployed: DeployedRelease, target: Target) -> list[Command]:
    """A cancelled deploy leaves the release pending, which blocks every later upgrade."""
    if not deployed.status.startswith("pending-"):
        return []
    verb = "uninstall" if deployed.revision == 1 else "rollback"
    return [["helm", verb, release, "--namespace", release, "--wait"] + _helm_context(target)]
