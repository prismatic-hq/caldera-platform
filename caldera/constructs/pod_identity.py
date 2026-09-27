from aws_cdk import aws_iam as iam
from constructs import Construct

POD_IDENTITY_PRINCIPAL = "pods.eks.amazonaws.com"


def pod_identity_role(
    scope: Construct, construct_id: str, statements: list[iam.PolicyStatement]
) -> iam.Role:
    """An IAM role that EKS Pod Identity can hand to one service account."""
    return iam.Role(
        scope,
        construct_id,
        assumed_by=iam.SessionTagsPrincipal(iam.ServicePrincipal(POD_IDENTITY_PRINCIPAL)),
        inline_policies=(
            {"Controller": iam.PolicyDocument(statements=statements)} if statements else None
        ),
    )
