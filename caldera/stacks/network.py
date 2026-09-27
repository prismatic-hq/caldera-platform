from aws_cdk import RemovalPolicy, Stack, Tags
from aws_cdk import aws_budgets as budgets
from aws_cdk import aws_ec2 as ec2
from aws_cdk import aws_iam as iam
from aws_cdk import aws_logs as logs
from constructs import Construct

from caldera.config import SSM_PREFIX, PlatformConfig
from caldera.constructs.cleanup import CleanupProps, CleanupResource, vpc_arn


class NetworkStack(Stack):
    """VPC across 2 AZs with flow logs, NAT gateway and S3 gateway endpoint."""

    def __init__(
        self, scope: Construct, construct_id: str, *, config: PlatformConfig, **kwargs
    ) -> None:
        super().__init__(scope, construct_id, **kwargs)
        flow_logs = logs.LogGroup(
            self,
            "FlowLogs",
            retention=logs.RetentionDays.ONE_MONTH,
            removal_policy=RemovalPolicy.DESTROY,
        )
        self.vpc = ec2.Vpc(
            self,
            "Vpc",
            ip_addresses=ec2.IpAddresses.cidr("10.40.0.0/16"),
            max_azs=2,
            nat_gateways=config.nat_gateways,
            subnet_configuration=[
                ec2.SubnetConfiguration(
                    name="Public", subnet_type=ec2.SubnetType.PUBLIC, cidr_mask=24
                ),
                ec2.SubnetConfiguration(
                    name="Private", subnet_type=ec2.SubnetType.PRIVATE_WITH_EGRESS, cidr_mask=18
                ),
            ],
            flow_logs={
                "All": ec2.FlowLogOptions(
                    destination=ec2.FlowLogDestination.to_cloud_watch_logs(flow_logs)
                )
            },
            gateway_endpoints={
                "S3": ec2.GatewayVpcEndpointOptions(service=ec2.GatewayVpcEndpointAwsService.S3)
            },
        )
        for subnet in self.vpc.public_subnets:
            Tags.of(subnet).add("kubernetes.io/role/elb", "1")
        for subnet in self.vpc.private_subnets:
            Tags.of(subnet).add("kubernetes.io/role/internal-elb", "1")
            Tags.of(subnet).add("karpenter.sh/discovery", config.cluster_name)

        self.sweeper = CleanupResource(
            self,
            "Sweeper",
            props=CleanupProps(
                function_name=f"{config.cluster_name}-network-sweeper",
                handler="network_sweeper.handler",
                resource_type="Custom::NetworkSweeper",
                properties={
                    "ClusterName": config.cluster_name,
                    "VpcId": self.vpc.vpc_id,
                    "ParameterPrefix": SSM_PREFIX,
                },
                statements=self._sweeper_statements(config),
            ),
        )
        self.sweeper.node.add_dependency(self.vpc)
        self._budget(config)

    def _sweeper_statements(self, config: PlatformConfig) -> list[iam.PolicyStatement]:
        cluster = config.cluster_name

        def arn(service: str, resource: str, name: str = "*") -> str:
            return self.format_arn(service=service, resource=resource, resource_name=name)

        in_vpc = {"ArnEquals": {"ec2:Vpc": vpc_arn(self, self.vpc)}}
        return [
            iam.PolicyStatement(
                actions=[
                    "ec2:DescribeInstances",
                    "ec2:DescribeLaunchTemplates",
                    "ec2:DescribeNetworkInterfaces",
                    "ec2:DescribeSecurityGroups",
                    "elasticloadbalancing:DescribeLoadBalancers",
                    "elasticloadbalancing:DescribeTags",
                    "elasticloadbalancing:DescribeTargetGroups",
                ],
                resources=["*"],
            ),
            iam.PolicyStatement(
                actions=[
                    "elasticloadbalancing:DeleteLoadBalancer",
                    "elasticloadbalancing:DeleteTargetGroup",
                ],
                resources=[
                    arn("elasticloadbalancing", "loadbalancer"),
                    arn("elasticloadbalancing", "targetgroup"),
                ],
                conditions={"StringEquals": {"aws:ResourceTag/elbv2.k8s.aws/cluster": cluster}},
            ),
            iam.PolicyStatement(
                actions=["ec2:TerminateInstances"],
                resources=[arn("ec2", "instance")],
                conditions={
                    "StringEquals": {f"aws:ResourceTag/kubernetes.io/cluster/{cluster}": "owned"}
                },
            ),
            iam.PolicyStatement(
                actions=["ec2:DeleteLaunchTemplate"],
                resources=[arn("ec2", "launch-template")],
                conditions={"StringEquals": {"aws:ResourceTag/karpenter.k8s.aws/cluster": cluster}},
            ),
            iam.PolicyStatement(
                actions=["ec2:DeleteSecurityGroup", "ec2:DeleteNetworkInterface"],
                resources=[arn("ec2", "security-group"), arn("ec2", "network-interface")],
                conditions=in_vpc,
            ),
            iam.PolicyStatement(
                actions=["ssm:GetParametersByPath", "ssm:DeleteParameters"],
                resources=[
                    arn("ssm", "parameter", SSM_PREFIX.strip("/")),
                    arn("ssm", "parameter", f"{SSM_PREFIX.strip('/')}/*"),
                ],
            ),
        ]

    def _budget(self, config: PlatformConfig) -> None:
        subscribers = (
            [
                budgets.CfnBudget.SubscriberProperty(
                    address=config.budget_email, subscription_type="EMAIL"
                )
            ]
            if config.budget_email
            else []
        )
        budgets.CfnBudget(
            self,
            "Budget",
            budget=budgets.CfnBudget.BudgetDataProperty(
                budget_name=f"{config.cluster_name}-monthly",
                budget_type="COST",
                time_unit="MONTHLY",
                budget_limit=budgets.CfnBudget.SpendProperty(
                    amount=config.budget_limit_usd, unit="USD"
                ),
            ),
            notifications_with_subscribers=[
                budgets.CfnBudget.NotificationWithSubscribersProperty(
                    notification=budgets.CfnBudget.NotificationProperty(
                        comparison_operator="GREATER_THAN",
                        notification_type=kind,
                        threshold=80,
                        threshold_type="PERCENTAGE",
                    ),
                    subscribers=subscribers,
                )
                for kind in ("ACTUAL", "FORECASTED")
            ]
            if subscribers
            else None,
        )
