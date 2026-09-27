from aws_cdk import Stack


class ClusterStack(Stack):
    """EKS cluster, Pod Identity agent, Karpenter IAM and interruption queue."""
