from dataclasses import dataclass, field
from pathlib import Path

from aws_cdk import ArnFormat, CustomResource, Duration, RemovalPolicy, Stack
from aws_cdk import aws_ec2 as ec2
from aws_cdk import aws_iam as iam
from aws_cdk import aws_lambda as lambda_
from aws_cdk import aws_logs as logs
from constructs import Construct

HANDLERS_DIR = Path(__file__).resolve().parents[1] / "handlers"
RUNTIME = lambda_.Runtime.PYTHON_3_14


@dataclass(frozen=True)
class CleanupNetwork:
    vpc: ec2.IVpc
    subnets: list[ec2.ISubnet]
    security_group: ec2.ISecurityGroup


@dataclass(frozen=True)
class CleanupProps:
    function_name: str
    handler: str
    resource_type: str
    properties: dict[str, object]
    statements: list[iam.PolicyStatement]
    timeout: Duration = field(default_factory=lambda: Duration.minutes(20))
    network: CleanupNetwork | None = None


def vpc_arn(scope: Construct, vpc: ec2.IVpc) -> str:
    return Stack.of(scope).format_arn(service="ec2", resource="vpc", resource_name=vpc.vpc_id)


def vpc_access_statements(
    scope: Construct,
    vpc: ec2.IVpc,
    subnets: list[ec2.ISubnet],
    security_groups: list[ec2.ISecurityGroup],
) -> list[iam.PolicyStatement]:
    """Least-privilege replacement for AWSLambdaVPCAccessExecutionRole."""
    stack = Stack.of(scope)
    interfaces = stack.format_arn(service="ec2", resource="network-interface", resource_name="*")
    placement = [
        stack.format_arn(service="ec2", resource="subnet", resource_name=subnet.subnet_id)
        for subnet in subnets
    ] + [
        stack.format_arn(
            service="ec2", resource="security-group", resource_name=group.security_group_id
        )
        for group in security_groups
    ]
    return [
        iam.PolicyStatement(actions=["ec2:DescribeNetworkInterfaces"], resources=["*"]),
        iam.PolicyStatement(actions=["ec2:CreateNetworkInterface"], resources=placement),
        iam.PolicyStatement(
            actions=[
                "ec2:CreateNetworkInterface",
                "ec2:DeleteNetworkInterface",
                "ec2:AssignPrivateIpAddresses",
                "ec2:UnassignPrivateIpAddresses",
            ],
            resources=[interfaces],
            conditions={"ArnEquals": {"ec2:Vpc": vpc_arn(scope, vpc)}},
        ),
    ]


def owned_log_group(scope: Construct, construct_id: str, name: str) -> logs.LogGroup:
    return logs.LogGroup(
        scope,
        construct_id,
        log_group_name=name,
        retention=logs.RetentionDays.ONE_MONTH,
        removal_policy=RemovalPolicy.DESTROY,
    )


def log_statement(log_group: logs.ILogGroup) -> iam.PolicyStatement:
    return iam.PolicyStatement(
        actions=["logs:CreateLogStream", "logs:PutLogEvents"], resources=[log_group.log_group_arn]
    )


class CleanupResource(Construct):
    """A Lambda-backed custom resource whose Delete handler removes runtime-created resources."""

    def __init__(self, scope: Construct, construct_id: str, *, props: CleanupProps) -> None:
        super().__init__(scope, construct_id)
        stack = Stack.of(self)
        function_arn = stack.format_arn(
            service="lambda",
            resource="function",
            resource_name=props.function_name,
            arn_format=ArnFormat.COLON_RESOURCE_NAME,
        )
        log_group = owned_log_group(self, "Logs", f"/aws/lambda/{props.function_name}")
        statements = [
            log_statement(log_group),
            iam.PolicyStatement(actions=["lambda:InvokeFunction"], resources=[function_arn]),
            *props.statements,
        ]
        network = props.network
        if network:
            statements += vpc_access_statements(
                self, network.vpc, network.subnets, [network.security_group]
            )
        self.role = iam.Role(
            self,
            "Role",
            assumed_by=iam.ServicePrincipal("lambda.amazonaws.com"),
            inline_policies={"Cleanup": iam.PolicyDocument(statements=statements)},
        )
        self.function = lambda_.Function(
            self,
            "Function",
            function_name=props.function_name,
            runtime=RUNTIME,
            code=lambda_.Code.from_asset(str(HANDLERS_DIR)),
            handler=props.handler,
            timeout=Duration.minutes(15),
            memory_size=256,
            role=self.role,
            log_group=log_group,
            vpc=network.vpc if network else None,
            vpc_subnets=ec2.SubnetSelection(subnets=network.subnets) if network else None,
            security_groups=[network.security_group] if network else None,
        )
        self.resource = CustomResource(
            self,
            "Resource",
            service_token=self.function.function_arn,
            resource_type=props.resource_type,
            properties={
                **props.properties,
                "TimeoutMinutes": str(int(props.timeout.to_minutes())),
            },
            service_timeout=Duration.minutes(60),
            removal_policy=RemovalPolicy.DESTROY,
        )
        self.resource.node.add_dependency(self.role)
