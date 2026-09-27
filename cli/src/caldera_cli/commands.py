from dataclasses import dataclass

from caldera_cli.registry import ServiceSpec
from caldera_cli.resolver import VentPlan

Command = list[str]


@dataclass(frozen=True)
class Target:
    chart: str = "charts/vent"
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
    plan: VentPlan,
    services: tuple[ServiceSpec, ...],
    shas: dict[str, str],
    dataset_version: str,
    target: Target,
) -> list[Command]:
    namespace = plan.release
    strings = [f"vent.name={plan.vent}", f"datasetVersion={dataset_version}"]
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


def down_commands(vent: str, target: Target) -> list[Command]:
    release = f"vent-{vent}"
    return [
        ["helm", "uninstall", release, "--namespace", release, "--wait", "--ignore-not-found"]
        + _helm_context(target),
        ["kubectl", "delete", "namespace", release, "--wait=true", "--ignore-not-found"]
        + _kubectl_context(target),
    ]


def reset_commands(vent: str, target: Target) -> list[Command]:
    namespace = f"vent-{vent}"
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
