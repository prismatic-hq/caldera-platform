import os

import aws_cdk as cdk
from aws_cdk import Stack

from caldera.stacks.addons import AddonsStack
from caldera.stacks.ci_access import CiAccessStack
from caldera.stacks.cluster import ClusterStack
from caldera.stacks.dns import DnsStack
from caldera.stacks.network import NetworkStack
from caldera.stacks.registry import RegistryStack

STACKS: dict[str, type[Stack]] = {
    "Network": NetworkStack,
    "Cluster": ClusterStack,
    "Registry": RegistryStack,
    "Dns": DnsStack,
    "CiAccess": CiAccessStack,
    "Addons": AddonsStack,
}

DEPENDENCIES: dict[str, list[str]] = {
    "Cluster": ["Network"],
    "CiAccess": ["Cluster", "Registry"],
    "Addons": ["Cluster", "Dns"],
}


def build_platform(app: cdk.App, prefix: str = "Caldera") -> dict[str, Stack]:
    env = cdk.Environment(
        account=os.getenv("CDK_DEFAULT_ACCOUNT"), region=os.getenv("CDK_DEFAULT_REGION")
    )
    stacks = {name: cls(app, f"{prefix}{name}", env=env) for name, cls in STACKS.items()}
    for name, upstream in DEPENDENCIES.items():
        for dependency in upstream:
            stacks[name].add_stack_dependency(stacks[dependency])
    return stacks
