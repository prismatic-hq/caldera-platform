from aws_cdk import aws_ec2 as ec2
from aws_cdk import aws_eks_v2 as eks
from aws_cdk import aws_iam as iam
from aws_cdk import aws_lambda as lambda_

from caldera.constructs.cleanup import (
    RUNTIME,
    log_statement,
    owned_log_group,
    vpc_access_statements,
)

ECR_PUBLIC_LOGIN = ["ecr-public:GetAuthorizationToken", "sts:GetServiceBearerToken"]


def _harden(
    function: lambda_.Function, vpc: ec2.IVpc, log_name: str, extra: list[iam.PolicyStatement]
) -> None:
    """Swap AWS managed policies for inline least-privilege ones and own the log group."""
    log_group = owned_log_group(function, "Logs", log_name)
    cfn_function = function.node.default_child
    role = function.role
    if not isinstance(cfn_function, lambda_.CfnFunction) or not isinstance(role, iam.Role):
        raise TypeError(f"{function.node.path}: expected a CfnFunction with a CDK-owned role")
    cfn_function.add_property_override("LoggingConfig.LogGroup", log_group.log_group_name)
    cfn_role = role.node.default_child
    if not isinstance(cfn_role, iam.CfnRole):
        raise TypeError(f"{role.node.path}: expected a CfnRole")
    cfn_role.managed_policy_arns = None
    statements = [
        log_statement(log_group),
        *vpc_access_statements(
            function, vpc, vpc.private_subnets, function.connections.security_groups
        ),
        *extra,
    ]
    role.attach_inline_policy(iam.Policy(function, "LeastPrivilege", statements=statements))


def harden_kubectl_provider(cluster: eks.Cluster, vpc: ec2.IVpc, cluster_name: str) -> None:
    """The eks_v2 kubectl provider ships AWS managed policies and an older Python runtime."""
    provider = cluster.node.find_child("KubectlProvider")
    handler = provider.node.find_child("Handler")
    on_event = provider.node.find_child("Provider").node.find_child("framework-onEvent")
    if not isinstance(handler, lambda_.Function) or not isinstance(on_event, lambda_.Function):
        raise TypeError("unexpected eks_v2 kubectl provider layout")
    handler_resource = handler.node.default_child
    if not isinstance(handler_resource, lambda_.CfnFunction):
        raise TypeError(f"{handler.node.path}: expected a CfnFunction")
    handler_resource.runtime = RUNTIME.name
    handler.node.try_remove_child("HasEcrPublic")
    _harden(
        handler,
        vpc,
        f"/aws/lambda/{cluster_name}-kubectl-handler",
        [iam.PolicyStatement(actions=ECR_PUBLIC_LOGIN, resources=["*"])],
    )
    _harden(on_event, vpc, f"/aws/lambda/{cluster_name}-kubectl-provider", [])
