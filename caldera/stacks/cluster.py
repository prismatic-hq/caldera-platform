from aws_cdk import Acknowledgment, Stack, Validations
from constructs import Construct

from caldera.config import PlatformConfig
from caldera.stacks.network import NetworkStack


class ClusterStack(Stack):
    """EKS without default networking addons, Cilium in ENI mode, system nodes and Karpenter."""

    def __init__(
        self,
        scope: Construct,
        construct_id: str,
        *,
        config: PlatformConfig,
        network: NetworkStack,
        **kwargs,
    ) -> None:
        super().__init__(scope, construct_id, **kwargs)
        Validations.of(self).acknowledge(
            Acknowledgment(
                id="CloudFormation-Validate::F0001", reason="resources arrive in a later PR"
            )
        )
