from aws_cdk import Stack


class ClusterStack(Stack):
    """EKS without default networking addons, Cilium in ENI mode, system nodes and Karpenter."""
