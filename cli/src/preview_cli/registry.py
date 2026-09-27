import re
from dataclasses import dataclass
from pathlib import Path

import yaml

SERVICE_NAME = re.compile(r"^[a-z][a-z0-9-]*$")
DEFAULT_PORT = 8000


@dataclass(frozen=True)
class ServiceSpec:
    name: str
    repo: str
    image: str
    port: int = DEFAULT_PORT


def _spec(entry: dict) -> ServiceSpec:
    for field in ("name", "repo", "image"):
        if not entry.get(field):
            raise ValueError(f"service registry entry {entry!r} is missing {field}")
    if not SERVICE_NAME.match(entry["name"]):
        raise ValueError(
            f"invalid service name {entry['name']!r}: use lowercase letters, digits, '-'"
        )
    return ServiceSpec(
        entry["name"], entry["repo"], entry["image"], int(entry.get("port", DEFAULT_PORT))
    )


@dataclass(frozen=True)
class ServiceRegistry:
    services: tuple[ServiceSpec, ...]

    @classmethod
    def from_mapping(cls, data: dict) -> "ServiceRegistry":
        services = tuple(_spec(entry) for entry in data.get("services") or [])
        if not services:
            raise ValueError("service registry must list at least one service")
        for field in ("name", "repo"):
            values = [getattr(service, field) for service in services]
            duplicates = sorted({value for value in values if values.count(value) > 1})
            if duplicates:
                raise ValueError(f"duplicate {field} in service registry: {', '.join(duplicates)}")
        return cls(services)

    @classmethod
    def load(cls, path: Path) -> "ServiceRegistry":
        return cls.from_mapping(yaml.safe_load(path.read_text()) or {})

    @property
    def names(self) -> tuple[str, ...]:
        return tuple(service.name for service in self.services)

    def get(self, name: str) -> ServiceSpec:
        for service in self.services:
            if service.name == name:
                return service
        raise ValueError(f"unknown service {name!r}; registry has {', '.join(self.names)}")

    def by_repo(self, repo: str) -> ServiceSpec:
        short = repo.rsplit("/", 1)[-1]
        for service in self.services:
            if short in (service.repo, service.name):
                return service
        repos = ", ".join(service.repo for service in self.services)
        raise ValueError(f"unknown service repo {repo!r}; registry has {repos}")
