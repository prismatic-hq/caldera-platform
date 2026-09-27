from dataclasses import dataclass, field
from pathlib import Path

from aws_cdk import ArnFormat, Aws, CustomResource, Duration, RemovalPolicy, Stack
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


LAMBDA_VPC_DOCS = "https://docs.aws.amazon.com/lambda/latest/dg/configuration-vpc.html"
LAMBDA_VPC_ACCESS_ACTIONS = [
    "ec2:CreateNetworkInterface",
    "ec2:DescribeNetworkInterfaces",
    "ec2:DescribeSubnets",
    "ec2:DeleteNetworkInterface",
    "ec2:AssignPrivateIpAddresses",
    "ec2:UnassignPrivateIpAddresses",
]
DENY_FUNCTION_CODE = {"Null": {"lambda:SourceFunctionArn": "false"}}


def vpc_access_statements() -> list[iam.PolicyStatement]:
    """Lambda requires these on all resources; function code is denied them. See LAMBDA_VPC_DOCS."""
    return [
        iam.PolicyStatement(actions=LAMBDA_VPC_ACCESS_ACTIONS, resources=["*"]),
        iam.PolicyStatement(
            effect=iam.Effect.DENY,
            actions=LAMBDA_VPC_ACCESS_ACTIONS,
            resources=["*"],
            conditions=DENY_FUNCTION_CODE,
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


def lambda_function_arn(scope: Construct, function_name: str) -> str:
    return Stack.of(scope).format_arn(
        service="lambda",
        resource="function",
        resource_name=function_name,
        arn_format=ArnFormat.COLON_RESOURCE_NAME,
    )


class FailureReporter(Construct):
    """Reports FAILED to CloudFormation when a cleanup Lambda's async re-invocation fails."""

    def __init__(self, scope: Construct, construct_id: str, function_name: str) -> None:
        super().__init__(scope, construct_id)
        self.function_arn = lambda_function_arn(self, function_name)
        log_group = owned_log_group(self, "Logs", f"/aws/lambda/{function_name}")
        role = iam.Role(
            self,
            "Role",
            assumed_by=iam.ServicePrincipal("lambda.amazonaws.com"),
            inline_policies={"Report": iam.PolicyDocument(statements=[log_statement(log_group)])},
        )
        self.function = lambda_.Function(
            self,
            "Function",
            function_name=function_name,
            runtime=RUNTIME,
            code=lambda_.Code.from_asset(str(HANDLERS_DIR), exclude=["__pycache__"]),
            handler="cfn.report_failure",
            timeout=Duration.minutes(1),
            memory_size=128,
            role=role,
            log_group=log_group,
        )


class CleanupResource(Construct):
    """A Lambda-backed custom resource whose Delete handler removes runtime-created resources."""

    def __init__(self, scope: Construct, construct_id: str, *, props: CleanupProps) -> None:
        super().__init__(scope, construct_id)
        function_arn = lambda_function_arn(self, props.function_name)
        reporter = FailureReporter(
            self, "FailureReporter", f"{props.function_name}-failure-reporter"
        )
        log_group = owned_log_group(self, "Logs", f"/aws/lambda/{props.function_name}")
        statements = [
            log_statement(log_group),
            iam.PolicyStatement(
                actions=["lambda:InvokeFunction"], resources=[function_arn, reporter.function_arn]
            ),
            iam.PolicyStatement(
                actions=["cloudformation:DescribeStacks"], resources=[Aws.STACK_ID]
            ),
            *props.statements,
        ]
        network = props.network
        if network:
            statements += vpc_access_statements()
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
            code=lambda_.Code.from_asset(str(HANDLERS_DIR), exclude=["__pycache__"]),
            handler=props.handler,
            timeout=Duration.minutes(15),
            memory_size=256,
            role=self.role,
            log_group=log_group,
            vpc=network.vpc if network else None,
            vpc_subnets=ec2.SubnetSelection(subnets=network.subnets) if network else None,
            security_groups=[network.security_group] if network else None,
        )
        invoke_config = lambda_.CfnEventInvokeConfig(
            self,
            "AsyncInvoke",
            function_name=self.function.function_name,
            qualifier="$LATEST",
            maximum_retry_attempts=2,
            maximum_event_age_in_seconds=3600,
            destination_config=lambda_.CfnEventInvokeConfig.DestinationConfigProperty(
                on_failure=lambda_.CfnEventInvokeConfig.OnFailureProperty(
                    destination=reporter.function_arn
                )
            ),
        )
        invoke_config.node.add_dependency(reporter.function)
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
