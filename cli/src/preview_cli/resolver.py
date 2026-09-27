import re
from collections.abc import Sequence
from dataclasses import dataclass, field
from enum import StrEnum

FEATURE_PREFIX = "feature/"
DNS_LABEL_MAX = 63
HELM_RELEASE_MAX = 53
DNS_LABEL = re.compile(r"^[a-z0-9]([-a-z0-9]*[a-z0-9])?$")


class Action(StrEnum):
    UP = "up"
    DOWN = "down"


class EnvironmentNameError(ValueError):
    pass


@dataclass(frozen=True)
class PreviewPlan:
    environment: str
    action: Action
    refs: dict[str, str] = field(default_factory=dict)
    joins_existing: bool = False

    @property
    def release(self) -> str:
        return f"preview-{self.environment}"


def slug(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", value.lower()).strip("-")


def feature_name(branch: str) -> str | None:
    return slug(branch.removeprefix(FEATURE_PREFIX)) if branch.startswith(FEATURE_PREFIX) else None


def validate_environment_name(environment: str, services: Sequence[str]) -> str:
    if not DNS_LABEL.match(environment):
        raise EnvironmentNameError(
            f"preview environment name {environment!r} is not a valid DNS label"
        )
    for service in services:
        label = f"{service}-{environment}"
        if len(label) > DNS_LABEL_MAX:
            raise EnvironmentNameError(
                f"preview environment name {environment!r} is too long: hostname label "
                f"{label!r} has {len(label)} characters, DNS allows {DNS_LABEL_MAX}; "
                "shorten the branch name"
            )
    release = f"preview-{environment}"
    if len(release) > HELM_RELEASE_MAX:
        raise EnvironmentNameError(
            f"preview environment name {environment!r} is too long: release {release!r} has "
            f"{len(release)} characters, Helm allows {HELM_RELEASE_MAX}; shorten the branch name"
        )
    return environment


def _require_service(services: Sequence[str], service: str) -> None:
    if service not in services:
        raise ValueError(f"unknown service {service!r}; registry has {', '.join(services)}")


def environment_name(services: Sequence[str], service: str, branch: str) -> str:
    _require_service(services, service)
    if branch == "main":
        raise EnvironmentNameError("main is the baseline and never gets a preview environment")
    name = feature_name(branch)
    return validate_environment_name(
        name if name is not None else f"{service}-{slug(branch)}", services
    )


def resolve_push(
    services: Sequence[str],
    pushed: str,
    branch: str,
    sharing: frozenset[str],
    existing_environments: frozenset[str] = frozenset(),
) -> PreviewPlan:
    environment = environment_name(services, pushed, branch)
    shared = sharing if feature_name(branch) is not None else frozenset()
    return PreviewPlan(
        environment=environment,
        action=Action.UP,
        refs={name: branch if name == pushed or name in shared else "main" for name in services},
        joins_existing=environment in existing_environments,
    )


def resolve_delete(
    services: Sequence[str], deleted: str, branch: str, sharing: frozenset[str]
) -> PreviewPlan:
    environment = environment_name(services, deleted, branch)
    remaining = sharing - {deleted} if feature_name(branch) is not None else frozenset()
    if not remaining:
        return PreviewPlan(environment=environment, action=Action.DOWN)
    return PreviewPlan(
        environment=environment,
        action=Action.UP,
        refs={name: branch if name in remaining else "main" for name in services},
        joins_existing=True,
    )
