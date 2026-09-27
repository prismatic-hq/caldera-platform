from aws_cdk import Aws, Stack
from aws_cdk import aws_iam as iam
from constructs import Construct

POD_IDENTITY_PRINCIPAL = "pods.eks.amazonaws.com"


def pod_identity_role(
    scope: Construct,
    construct_id: str,
    statements: list[iam.PolicyStatement],
    *,
    cluster_name: str,
) -> iam.Role:
    """An IAM role that EKS Pod Identity can hand to a service account of one cluster only."""
    cluster_arn = Stack.of(scope).format_arn(
        service="eks", resource="cluster", resource_name=cluster_name
    )
    principal = iam.SessionTagsPrincipal(iam.ServicePrincipal(POD_IDENTITY_PRINCIPAL))
    return iam.Role(
        scope,
        construct_id,
        assumed_by=principal.with_conditions(
            {
                "StringEquals": {"aws:SourceAccount": Aws.ACCOUNT_ID},
                "ArnEquals": {"aws:SourceArn": cluster_arn},
            }
        ),
        inline_policies=(
            {"Controller": iam.PolicyDocument(statements=statements)} if statements else None
        ),
    )
