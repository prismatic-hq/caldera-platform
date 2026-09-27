"""Least-privilege IAM statements for the in-cluster controllers that call AWS APIs."""

from aws_cdk import Stack
from aws_cdk import aws_iam as iam


def _arn(stack: Stack, service: str, resource: str, name: str = "*", **kwargs: str) -> str:
    return stack.format_arn(service=service, resource=resource, resource_name=name, **kwargs)


def _ec2(stack: Stack, *resources: str) -> list[str]:
    return [_arn(stack, "ec2", resource) for resource in resources]


def _describe(*actions: str) -> iam.PolicyStatement:
    return iam.PolicyStatement(actions=list(actions), resources=["*"])


def cluster_owned(cluster: str) -> dict[str, str]:
    return {f"aws:ResourceTag/kubernetes.io/cluster/{cluster}": "owned"}


def cilium_operator_statements(
    stack: Stack, cluster: str, vpc_arn: str, subnet_arns: list[str]
) -> list[iam.PolicyStatement]:
    """ENI IPAM: https://docs.cilium.io/en/stable/network/concepts/ipam/eni/#required-privileges"""
    in_vpc = {"ArnEquals": {"ec2:Vpc": vpc_arn}}
    return [
        _describe(
            "ec2:DescribeInstances",
            "ec2:DescribeInstanceTypes",
            "ec2:DescribeNetworkInterfaces",
            "ec2:DescribeSecurityGroups",
            "ec2:DescribeSubnets",
            "ec2:DescribeTags",
            "ec2:DescribeVpcs",
        ),
        iam.PolicyStatement(actions=["ec2:CreateNetworkInterface"], resources=subnet_arns),
        iam.PolicyStatement(
            actions=["ec2:CreateNetworkInterface", "ec2:ModifyNetworkInterfaceAttribute"],
            resources=_ec2(stack, "security-group"),
            conditions=in_vpc,
        ),
        iam.PolicyStatement(
            actions=[
                "ec2:AssignPrivateIpAddresses",
                "ec2:AttachNetworkInterface",
                "ec2:CreateNetworkInterface",
                "ec2:DeleteNetworkInterface",
                "ec2:ModifyNetworkInterfaceAttribute",
                "ec2:UnassignPrivateIpAddresses",
            ],
            resources=_ec2(stack, "network-interface"),
            conditions=in_vpc,
        ),
        iam.PolicyStatement(
            actions=["ec2:AttachNetworkInterface"],
            resources=_ec2(stack, "instance"),
            conditions={"StringEquals": cluster_owned(cluster)},
        ),
        iam.PolicyStatement(
            actions=["ec2:CreateTags"],
            resources=_ec2(stack, "network-interface"),
            conditions={"StringEquals": {"ec2:CreateAction": "CreateNetworkInterface"}},
        ),
    ]


def karpenter_controller_statements(
    stack: Stack, cluster: str, cluster_arn: str, node_role_arn: str, queue_arn: str
) -> list[iam.PolicyStatement]:
    """Karpenter v1 controller policy: https://karpenter.sh/docs/reference/cloudformation/"""
    launched = ["fleet", "instance", "volume", "network-interface", "launch-template"]
    launched.append("spot-instances-request")
    request_tags = {
        "StringEquals": {
            f"aws:RequestTag/kubernetes.io/cluster/{cluster}": "owned",
            "aws:RequestTag/eks:eks-cluster-name": cluster,
        },
        "StringLike": {"aws:RequestTag/karpenter.sh/nodepool": "*"},
    }
    owned_by_nodepool = {
        "StringEquals": cluster_owned(cluster),
        "StringLike": {"aws:ResourceTag/karpenter.sh/nodepool": "*"},
    }
    region = {"StringEquals": {"aws:RequestedRegion": stack.region}}
    profiles = _arn(stack, "iam", "instance-profile", region="")
    return [
        iam.PolicyStatement(
            actions=["ec2:RunInstances", "ec2:CreateFleet"],
            resources=[
                _arn(stack, "ec2", "image", account=""),
                _arn(stack, "ec2", "snapshot", account=""),
                *_ec2(stack, "security-group", "subnet", "capacity-reservation"),
            ],
        ),
        iam.PolicyStatement(
            actions=["ec2:RunInstances", "ec2:CreateFleet"],
            resources=_ec2(stack, "launch-template"),
            conditions=owned_by_nodepool,
        ),
        iam.PolicyStatement(
            actions=["ec2:RunInstances", "ec2:CreateFleet", "ec2:CreateLaunchTemplate"],
            resources=_ec2(stack, *launched),
            conditions=request_tags,
        ),
        iam.PolicyStatement(
            actions=["ec2:CreateTags"],
            resources=_ec2(stack, *launched),
            conditions={
                **request_tags,
                "StringEquals": {
                    **request_tags["StringEquals"],
                    "ec2:CreateAction": ["RunInstances", "CreateFleet", "CreateLaunchTemplate"],
                },
            },
        ),
        iam.PolicyStatement(
            actions=["ec2:CreateTags"],
            resources=_ec2(stack, "instance"),
            conditions={
                **owned_by_nodepool,
                "StringEqualsIfExists": {"aws:RequestTag/eks:eks-cluster-name": cluster},
                "ForAllValues:StringEquals": {
                    "aws:TagKeys": ["eks:eks-cluster-name", "karpenter.sh/nodeclaim", "Name"]
                },
            },
        ),
        iam.PolicyStatement(
            actions=["ec2:TerminateInstances", "ec2:DeleteLaunchTemplate"],
            resources=_ec2(stack, "instance", "launch-template"),
            conditions=owned_by_nodepool,
        ),
        iam.PolicyStatement(
            actions=[
                "ec2:DescribeAvailabilityZones",
                "ec2:DescribeCapacityReservations",
                "ec2:DescribeImages",
                "ec2:DescribeInstances",
                "ec2:DescribeInstanceTypeOfferings",
                "ec2:DescribeInstanceTypes",
                "ec2:DescribeLaunchTemplates",
                "ec2:DescribeSecurityGroups",
                "ec2:DescribeSpotPriceHistory",
                "ec2:DescribeSubnets",
            ],
            resources=["*"],
            conditions=region,
        ),
        iam.PolicyStatement(
            actions=["ssm:GetParameter"],
            resources=[_arn(stack, "ssm", "parameter", "aws/service/*", account="")],
        ),
        _describe("pricing:GetProducts"),
        iam.PolicyStatement(
            actions=["sqs:DeleteMessage", "sqs:GetQueueUrl", "sqs:ReceiveMessage"],
            resources=[queue_arn],
        ),
        iam.PolicyStatement(
            actions=["iam:PassRole"],
            resources=[node_role_arn],
            conditions={"StringEquals": {"iam:PassedToService": "ec2.amazonaws.com"}},
        ),
        iam.PolicyStatement(
            actions=["iam:CreateInstanceProfile", "iam:TagInstanceProfile"],
            resources=[profiles],
            conditions={
                "StringEquals": {
                    f"aws:RequestTag/kubernetes.io/cluster/{cluster}": "owned",
                    "aws:RequestTag/eks:eks-cluster-name": cluster,
                    "aws:RequestTag/topology.kubernetes.io/region": stack.region,
                },
                "StringLike": {"aws:RequestTag/karpenter.k8s.aws/ec2nodeclass": "*"},
            },
        ),
        iam.PolicyStatement(
            actions=[
                "iam:AddRoleToInstanceProfile",
                "iam:DeleteInstanceProfile",
                "iam:RemoveRoleFromInstanceProfile",
                "iam:TagInstanceProfile",
            ],
            resources=[profiles],
            conditions={
                "StringEquals": {
                    **cluster_owned(cluster),
                    "aws:ResourceTag/topology.kubernetes.io/region": stack.region,
                },
                "StringLike": {"aws:ResourceTag/karpenter.k8s.aws/ec2nodeclass": "*"},
            },
        ),
        iam.PolicyStatement(
            actions=["iam:GetInstanceProfile", "iam:ListInstanceProfiles"],
            resources=[profiles],
        ),
        iam.PolicyStatement(actions=["eks:DescribeCluster"], resources=[cluster_arn]),
    ]
