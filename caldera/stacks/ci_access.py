from aws_cdk import Stack
from constructs import Construct

from caldera.config import PlatformConfig
from caldera.stacks.cluster import ClusterStack
from caldera.stacks.registry import RegistryStack


class CiAccessStack(Stack):
    """GitHub OIDC provider and CI roles, plus Pod Identity roles for in-cluster runners."""

    def __init__(
        self,
        scope: Construct,
        construct_id: str,
        *,
        config: PlatformConfig,
        cluster: ClusterStack,
        registry: RegistryStack,
        **kwargs,
    ) -> None:
        super().__init__(scope, construct_id, **kwargs)
