import os

import aws_cdk as cdk
from aws_cdk import Stack

from caldera_platform.stacks.addon_identity import AddonIdentityStack
from caldera_platform.stacks.ci_access import CiAccessStack
from caldera_platform.stacks.cluster import ClusterStack
from caldera_platform.stacks.data import DataStack
from caldera_platform.stacks.dns import DnsStack
from caldera_platform.stacks.gitops_bridge import GitOpsBridgeStack
from caldera_platform.stacks.network import NetworkStack
from caldera_platform.stacks.registry import RegistryStack

STACK_ORDER: list[tuple[str, type[Stack]]] = [
    ("Network", NetworkStack),
    ("Cluster", ClusterStack),
    ("Data", DataStack),
    ("Dns", DnsStack),
    ("Registry", RegistryStack),
    ("CiAccess", CiAccessStack),
    ("AddonIdentity", AddonIdentityStack),
    ("GitOpsBridge", GitOpsBridgeStack),
]


def build_platform(app: cdk.App, prefix: str = "Caldera") -> list[Stack]:
    env = cdk.Environment(
        account=os.getenv("CDK_DEFAULT_ACCOUNT"), region=os.getenv("CDK_DEFAULT_REGION")
    )
    stacks: list[Stack] = []
    for name, stack_class in STACK_ORDER:
        stack = stack_class(app, f"{prefix}{name}", env=env)
        if stacks:
            stack.add_stack_dependency(stacks[-1])
        stacks.append(stack)
    return stacks
