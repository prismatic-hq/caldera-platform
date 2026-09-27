from dataclasses import dataclass
from pathlib import Path

from constructs import Node

from preview_cli.registry import ServiceRegistry

REPO_ROOT = Path(__file__).resolve().parents[1]
SERVICES_FILE = REPO_ROOT / "services.yaml"
SSM_PREFIX = "/prismatic/"
PLATFORM_TAG = "prismatic:platform"
GITHUB_APP_KEYS = ("github_app_id", "github_app_installation_id", "github_app_private_key")


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
    def from_context(cls, node: Node) -> "PlatformConfig":
        def context(key: str, default: object) -> object:
            value = node.try_get_context(key)
            return default if value is None else value

        nat_gateways = int(context("natGateways", 1))
        if nat_gateways not in (1, 2):
            raise ValueError(f"natGateways must be 1 or 2, got {nat_gateways}")
        domain = str(context("domain", "prismatic.dev"))
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
