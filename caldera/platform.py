import os

import aws_cdk as cdk
from aws_cdk import Stack, Tags

from caldera.config import PLATFORM_TAG, PlatformConfig
from caldera.stacks.addons import AddonsStack
from caldera.stacks.ci_access import CiAccessStack
from caldera.stacks.cluster import ClusterStack
from caldera.stacks.dns import DnsStack
from caldera.stacks.network import NetworkStack
from caldera.stacks.registry import RegistryStack

DEPENDENCIES: dict[str, list[str]] = {
    "Cluster": ["Network"],
    "CiAccess": ["Cluster", "Registry"],
    "Addons": ["Network", "Cluster", "Dns"],
}


def build_platform(app: cdk.App, prefix: str = "Caldera") -> dict[str, Stack]:
    config = PlatformConfig.from_context(app.node)
    env = cdk.Environment(
        account=os.getenv("CDK_DEFAULT_ACCOUNT"), region=os.getenv("CDK_DEFAULT_REGION")
    )
    Tags.of(app).add(PLATFORM_TAG, config.cluster_name)

    network = NetworkStack(app, f"{prefix}Network", config=config, env=env)
    cluster = ClusterStack(app, f"{prefix}Cluster", config=config, network=network, env=env)
    registry = RegistryStack(app, f"{prefix}Registry", config=config, env=env)
    dns = DnsStack(app, f"{prefix}Dns", config=config, env=env)
    ci_access = CiAccessStack(
        app, f"{prefix}CiAccess", config=config, cluster=cluster, registry=registry, env=env
    )
    addons = AddonsStack(
        app, f"{prefix}Addons", config=config, network=network, cluster=cluster, dns=dns, env=env
    )
    stacks: dict[str, Stack] = {
        "Network": network,
        "Cluster": cluster,
        "Registry": registry,
        "Dns": dns,
        "CiAccess": ci_access,
        "Addons": addons,
    }
    for name, upstream in DEPENDENCIES.items():
        for dependency in upstream:
            stacks[name].add_stack_dependency(stacks[dependency])
    return stacks
