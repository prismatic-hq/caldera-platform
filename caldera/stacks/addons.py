from aws_cdk import Stack
from constructs import Construct

from caldera.config import PlatformConfig
from caldera.stacks.cluster import ClusterStack
from caldera.stacks.dns import DnsStack
from caldera.stacks.network import NetworkStack


class AddonsStack(Stack):
    """Cluster addons installed as Helm charts, Cilium first."""

    def __init__(
        self,
        scope: Construct,
        construct_id: str,
        *,
        config: PlatformConfig,
        network: NetworkStack,
        cluster: ClusterStack,
        dns: DnsStack,
        **kwargs,
    ) -> None:
        super().__init__(scope, construct_id, **kwargs)
