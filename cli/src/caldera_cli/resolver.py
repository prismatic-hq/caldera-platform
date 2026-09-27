import re
from dataclasses import dataclass, field
from enum import StrEnum

FEATURE_PREFIX = "feature/"
DNS_LABEL_MAX = 63
HELM_RELEASE_MAX = 53
DNS_LABEL = re.compile(r"^[a-z0-9]([-a-z0-9]*[a-z0-9])?$")


class Service(StrEnum):
    TREMOR = "tremor"
    STEWARD = "steward"

    @property
    def repo(self) -> str:
        return f"{self.value}-api"

    @property
    def other(self) -> "Service":
        return Service.STEWARD if self is Service.TREMOR else Service.TREMOR

    @classmethod
    def from_repo(cls, repo: str) -> "Service":
        short = repo.rsplit("/", 1)[-1].removesuffix("-api")
        try:
            return cls(short)
        except ValueError as error:
            raise ValueError(
                f"unknown service repo {repo!r}; expected tremor-api or steward-api"
            ) from error


class Action(StrEnum):
    UP = "up"
    DOWN = "down"


class VentNameError(ValueError):
    pass


@dataclass(frozen=True)
class VentPlan:
    vent: str
    action: Action
    refs: dict[Service, str] = field(default_factory=dict)
    joins_existing: bool = False

    @property
    def release(self) -> str:
        return f"vent-{self.vent}"


def slug(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", value.lower()).strip("-")


def feature_name(branch: str) -> str | None:
    return slug(branch.removeprefix(FEATURE_PREFIX)) if branch.startswith(FEATURE_PREFIX) else None


def validate_vent_name(vent: str) -> str:
    if not DNS_LABEL.match(vent):
        raise VentNameError(f"vent name {vent!r} is not a valid DNS label")
    for service in Service:
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


def vent_name(service: Service, branch: str) -> str:
    if branch == "main":
        raise VentNameError("main is the baseline and never gets a vent")
    name = feature_name(branch)
    return validate_vent_name(name if name is not None else f"{service}-{slug(branch)}")


def resolve_push(
    service: Service,
    branch: str,
    other_has_branch: bool,
    existing_vents: frozenset[str] = frozenset(),
) -> VentPlan:
    vent = vent_name(service, branch)
    shared = feature_name(branch) is not None and other_has_branch
    return VentPlan(
        vent=vent,
        action=Action.UP,
        refs={service: branch, service.other: branch if shared else "main"},
        joins_existing=vent in existing_vents,
    )


def resolve_delete(service: Service, branch: str, other_has_branch: bool) -> VentPlan:
    vent = vent_name(service, branch)
    if feature_name(branch) is not None and other_has_branch:
        return VentPlan(
            vent=vent,
            action=Action.UP,
            refs={service: "main", service.other: branch},
            joins_existing=True,
        )
    return VentPlan(vent=vent, action=Action.DOWN)
