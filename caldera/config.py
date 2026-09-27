from dataclasses import dataclass
from pathlib import Path

import yaml
from constructs import Node

from preview_cli.registry import ServiceRegistry

REPO_ROOT = Path(__file__).resolve().parents[1]
SERVICES_FILE = REPO_ROOT / "services.yaml"
ENVIRONMENTS_DIR = REPO_ROOT / "deploy" / "environments"
SSM_PREFIX = "/prismatic/"
PLATFORM_TAG = "prismatic:platform"
GITHUB_APP_KEYS = ("github_app_id", "github_app_installation_id", "github_app_private_key")


def load_environment(name: str | None, directory: Path) -> dict[str, object]:
    """Settings from `<directory>/<name>`, keyed like CDK context; empty when no name is given."""
    if not name:
        return {}
    if Path(name).name != name:
        raise ValueError(f"env must be a file name in {directory}, got {name!r}")
    path = directory / name
    if not path.is_file():
        available = ", ".join(sorted(p.name for p in directory.glob("*.yaml"))) or "none"
        raise ValueError(f"environment file {path} not found; available: {available}")
    settings = yaml.safe_load(path.read_text()) or {}
    if not isinstance(settings, dict):
        raise ValueError(f"{path} must be a mapping of setting names to values")
    return settings


def github_app_parameter(key: str) -> str:
    """SSM parameter that holds one GitHub App credential for the ARC runners."""
    return f"{SSM_PREFIX}github-app/{key.replace('_', '-')}"


def parse_list(value: object) -> tuple[str, ...]:
    """Context from `-c key=a,b` arrives as a string; from cdk.json as a list."""
    items = value.split(",") if isinstance(value, str) else value
    if not isinstance(items, list | tuple):
        raise ValueError(f"expected a list or comma-separated string, got {value!r}")
    return tuple(str(item).strip() for item in items if str(item).strip())


@dataclass(frozen=True)
class PlatformConfig:
    cluster_name: str
    domain: str
    github_org: str
    platform_repo: str
    nat_gateways: int
    budget_limit_usd: int
    budget_email: str
    acme_email: str
    allowlist_cidrs: tuple[str, ...]
    registry: ServiceRegistry

    @classmethod
    def from_context(
        cls, node: Node, environments_dir: Path = ENVIRONMENTS_DIR
    ) -> "PlatformConfig":
        """Read each setting from CDK context (`-c key=value`), then the `-c env=<file>` file."""
        environment = load_environment(node.try_get_context("env"), environments_dir)

        def context(key: str, default: object) -> object:
            value = node.try_get_context(key)
            if value is None:
                value = environment.get(key)
            return default if value is None else value

        nat_gateways = int(context("natGateways", 1))
        if nat_gateways not in (1, 2):
            raise ValueError(f"natGateways must be 1 or 2, got {nat_gateways}")
        domain = str(context("domain", ""))
        if not domain:
            raise ValueError(
                "domain is not set: run with ENV=<name>.yaml (a file in deploy/environments) "
                "or pass -c domain=<zone>"
            )
        return cls(
            cluster_name=str(context("clusterName", "caldera")),
            domain=domain,
            github_org=str(context("githubOrg", "prismatic-hq")),
            platform_repo=str(context("platformRepo", "caldera-platform")),
            nat_gateways=nat_gateways,
            budget_limit_usd=int(context("budgetLimitUsd", 400)),
            budget_email=str(context("budgetEmail", f"platform@{domain}")),
            acme_email=str(context("acmeEmail", f"platform@{domain}")),
            allowlist_cidrs=parse_list(context("previewAllowlistCidrs", [])),
            registry=ServiceRegistry.load(SERVICES_FILE),
        )

    @property
    def preview_domain(self) -> str:
        return f"preview.{self.domain}"

    @property
    def dev_domain(self) -> str:
        return f"dev.{self.domain}"

    @property
    def service_repos(self) -> tuple[str, ...]:
        return tuple(service.repo for service in self.registry.services)

    @property
    def service_images(self) -> tuple[str, ...]:
        return tuple(service.image for service in self.registry.services)
