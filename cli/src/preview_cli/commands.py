from dataclasses import dataclass

from preview_cli.registry import ServiceSpec
from preview_cli.resolver import PreviewPlan

Command = list[str]


@dataclass(frozen=True)
class Target:
    chart: str = "charts/preview-environment"
    context: str | None = None
    registry: str | None = None
    timeout: str = "5m"


def _helm_context(target: Target) -> list[str]:
    return ["--kube-context", target.context] if target.context else []


def _kubectl_context(target: Target) -> list[str]:
    return ["--context", target.context] if target.context else []


def image_tag(sha: str) -> str:
    return f"sha-{sha[:7]}"


def up_commands(
    plan: PreviewPlan,
    services: tuple[ServiceSpec, ...],
    shas: dict[str, str],
    dataset_version: str,
    target: Target,
) -> list[Command]:
    namespace = plan.release
    strings = [f"environment.name={plan.environment}", f"datasetVersion={dataset_version}"]
    numbers = []
    for service in services:
        strings += [
            f"services.{service.name}.image.repository={service.image}",
            f"services.{service.name}.image.tag={image_tag(shas[service.name])}",
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
    return [command + _helm_context(target)]


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
