from aws_cdk import Stack


class CiAccessStack(Stack):
    """GitHub OIDC provider and CI roles, plus Pod Identity roles for in-cluster runners."""
