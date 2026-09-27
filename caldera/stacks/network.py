from aws_cdk import Stack


class NetworkStack(Stack):
    """VPC across 2 AZs with flow logs, NAT gateway and S3 gateway endpoint."""
