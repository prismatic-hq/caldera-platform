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


class VentNameError(ValueError):
    pass


@dataclass(frozen=True)
class VentPlan:
    vent: str
    action: Action
    refs: dict[str, str] = field(default_factory=dict)
    joins_existing: bool = False

    @property
    def release(self) -> str:
        return f"vent-{self.vent}"


def slug(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", value.lower()).strip("-")


def feature_name(branch: str) -> str | None:
    return slug(branch.removeprefix(FEATURE_PREFIX)) if branch.startswith(FEATURE_PREFIX) else None


def validate_vent_name(vent: str, services: Sequence[str]) -> str:
    if not DNS_LABEL.match(vent):
        raise VentNameError(f"vent name {vent!r} is not a valid DNS label")
    for service in services:
        label = f"{service}-{vent}"
        if len(label) > DNS_LABEL_MAX:
            raise VentNameError(
                f"vent name {vent!r} is too long: hostname label {label!r} has {len(label)} "
                f"characters, DNS allows {DNS_LABEL_MAX}; shorten the branch name"
            )
    release = f"vent-{vent}"
    if len(release) > HELM_RELEASE_MAX:
        raise VentNameError(
            f"vent name {vent!r} is too long: release {release!r} has {len(release)} characters, "
            f"Helm allows {HELM_RELEASE_MAX}; shorten the branch name"
        )
    return vent


def _require_service(services: Sequence[str], service: str) -> None:
    if service not in services:
        raise ValueError(f"unknown service {service!r}; registry has {', '.join(services)}")


def vent_name(services: Sequence[str], service: str, branch: str) -> str:
    _require_service(services, service)
    if branch == "main":
        raise VentNameError("main is the baseline and never gets a vent")
    name = feature_name(branch)
    return validate_vent_name(name if name is not None else f"{service}-{slug(branch)}", services)


def resolve_push(
    services: Sequence[str],
    pushed: str,
    branch: str,
    sharing: frozenset[str],
    existing_vents: frozenset[str] = frozenset(),
) -> VentPlan:
    vent = vent_name(services, pushed, branch)
    shared = sharing if feature_name(branch) is not None else frozenset()
    return VentPlan(
        vent=vent,
        action=Action.UP,
        refs={name: branch if name == pushed or name in shared else "main" for name in services},
        joins_existing=vent in existing_vents,
    )


def resolve_delete(
    services: Sequence[str], deleted: str, branch: str, sharing: frozenset[str]
) -> VentPlan:
    vent = vent_name(services, deleted, branch)
    remaining = sharing - {deleted} if feature_name(branch) is not None else frozenset()
    if not remaining:
        return VentPlan(vent=vent, action=Action.DOWN)
    return VentPlan(
        vent=vent,
        action=Action.UP,
        refs={name: branch if name in remaining else "main" for name in services},
        joins_existing=True,
    )
