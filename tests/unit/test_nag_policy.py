import re
import runpy
from fnmatch import fnmatch
from pathlib import Path

import aws_cdk as cdk
import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
FINDING_ID = re.compile(r"^(?P<rule>AwsSolutions-[A-Z0-9]+)\[(?P<finding>.+)\]$")

EKS_MANAGED_POLICY_ROLES = {
    "CalderaCluster/ClusterRole/Resource",
    "CalderaCluster/SystemNodeRole/Resource",
    "CalderaCluster/KarpenterNodeRole/Resource",
}
EKS_MANAGED_POLICIES = {
    f"Policy::arn:<AWS::Partition>:iam::aws:policy/{name}"
    for name in (
        "AmazonEKSClusterPolicy",
        "AmazonEKSWorkerNodePolicy",
        "AmazonEC2ContainerRegistryReadOnly",
        "AmazonSSMManagedInstanceCore",
    )
}
ACTIONS_WITHOUT_RESOURCE_LEVEL_PERMISSIONS = (
    "ecr:GetAuthorizationToken",
    "ecr-public:GetAuthorizationToken",
    "ec2:Describe*",
    "elasticloadbalancing:Describe*",
    "pricing:GetProducts",
    "route53:ListHostedZones",
    "route53:ListHostedZonesByName",
    "sts:GetServiceBearerToken",
)
SCOPED_WILDCARD = re.compile(
    r"^Resource::(arn:(<AWS::Partition>|aws[a-z-]*):[a-z0-9-]+:"
    r"(<AWS::Region>|[a-z]{2}(-[a-z]+)+-\d)?:(<AWS::AccountId>|\d{12})?"
    r":[a-z-]+[/:][^*]*\*|<[A-Za-z0-9]+\.Arn>:\*)$"
)
IAM_POLICY_TYPES = {"AWS::IAM::Policy", "AWS::IAM::ManagedPolicy", "AWS::IAM::Role"}


def _as_list(value: object) -> list:
    return value if isinstance(value, list) else [value]


def _wildcard_resource_actions(resource: dict) -> list[str]:
    properties = resource.get("Properties", {})
    documents = [properties.get("PolicyDocument", {})]
    documents += [policy.get("PolicyDocument", {}) for policy in properties.get("Policies", [])]
    return [
        action
        for document in documents
        for statement in document.get("Statement", [])
        if "*" in _as_list(statement.get("Resource", []))
        for action in _as_list(statement.get("Action", []))
    ]


def _is_allowlisted_action(action: str) -> bool:
    return any(fnmatch(action, allowed) for allowed in ACTIONS_WITHOUT_RESOURCE_LEVEL_PERMISSIONS)


def _iam4_violations(path: str, resource: dict, finding: str) -> list[str]:
    if resource.get("Type") != "AWS::IAM::Role" or path not in EKS_MANAGED_POLICY_ROLES:
        return [f"{path}: IAM4 is allowed only on the EKS cluster and node roles"]
    if finding not in EKS_MANAGED_POLICIES:
        return [f"{path}: IAM4 on {finding} is not allowed"]
    return []


def _iam5_violations(path: str, resource: dict, finding: str) -> list[str]:
    if resource.get("Type") not in IAM_POLICY_TYPES:
        return [f"{path}: IAM5 suppression on a non-IAM resource"]
    if finding.startswith("Action::") and _is_allowlisted_action(finding.removeprefix("Action::")):
        return []
    if SCOPED_WILDCARD.match(finding):
        return []
    if finding != "Resource::*":
        return [f"{path}: IAM5 on {finding} is not allowed"]
    return [
        f"{path}: {action} supports resource-level permissions; scope it"
        for action in _wildcard_resource_actions(resource)
        if not _is_allowlisted_action(action)
    ]


def _rule_violations(path: str, resource: dict, suppression_id: str) -> list[str]:
    if not suppression_id.startswith("AwsSolutions-"):
        return []
    match = FINDING_ID.match(suppression_id)
    if match is None:
        return [f"{path}: {suppression_id} must name one exact finding as Rule[Finding]"]
    rule, finding = match["rule"], match["finding"]
    if rule == "AwsSolutions-IAM4":
        return _iam4_violations(path, resource, finding)
    if rule == "AwsSolutions-IAM5":
        return _iam5_violations(path, resource, finding)
    return [f"{path}: suppressing {rule} is not allowed"]


def suppression_violations(template: dict) -> list[str]:
    violations = []
    for logical_id, resource in template.get("Resources", {}).items():
        metadata = resource.get("Metadata", {})
        path = metadata.get("aws:cdk:path", logical_id)
        for rule in metadata.get("cdk_nag", {}).get("rules_to_suppress", []):
            violations += _rule_violations(path, resource, rule.get("id", ""))
    return violations


def _resource(resource_type: str, path: str, ids: list[str], **properties: object) -> dict:
    rules = [{"id": rule_id, "reason": "test"} for rule_id in ids]
    return {
        "Resources": {
            "R": {
                "Type": resource_type,
                "Properties": properties,
                "Metadata": {"aws:cdk:path": path, "cdk_nag": {"rules_to_suppress": rules}},
            }
        }
    }


def _policy(*actions: str, resource: str = "*") -> dict:
    return {"Statement": [{"Effect": "Allow", "Action": list(actions), "Resource": resource}]}


CLUSTER_ROLE = "CalderaCluster/ClusterRole/Resource"
EKS_CLUSTER_POLICY = (
    "AwsSolutions-IAM4[Policy::arn:<AWS::Partition>:iam::aws:policy/AmazonEKSClusterPolicy]"
)
POLICY_PATH = "CalderaAddons/Policy/Resource"


@pytest.mark.parametrize(
    ("template", "expected_count"),
    [
        pytest.param({"Resources": {}}, 0, id="no suppressions"),
        pytest.param(
            _resource("AWS::IAM::Role", CLUSTER_ROLE, [EKS_CLUSTER_POLICY]),
            0,
            id="IAM4 on EKS cluster role",
        ),
        pytest.param(
            _resource("AWS::IAM::Role", "CalderaAddons/DrainerRole/Resource", [EKS_CLUSTER_POLICY]),
            1,
            id="IAM4 on another role",
        ),
        pytest.param(
            _resource(
                "AWS::IAM::Role",
                CLUSTER_ROLE,
                ["AwsSolutions-IAM4[Policy::arn:<AWS::Partition>:iam::aws:policy/AdminAccess]"],
            ),
            1,
            id="IAM4 with a non-EKS managed policy",
        ),
        pytest.param(
            _resource("AWS::IAM::Role", CLUSTER_ROLE, ["AwsSolutions-IAM4"]),
            1,
            id="IAM4 without a finding",
        ),
        pytest.param(
            _resource(
                "AWS::IAM::Policy",
                POLICY_PATH,
                ["AwsSolutions-IAM5[Resource::*]", "AwsSolutions-IAM5[Action::ec2:Describe*]"],
                PolicyDocument=_policy("ec2:Describe*", "ecr:GetAuthorizationToken"),
            ),
            0,
            id="IAM5 on allowlisted actions",
        ),
        pytest.param(
            _resource(
                "AWS::IAM::Policy",
                POLICY_PATH,
                ["AwsSolutions-IAM5[Resource::*]"],
                PolicyDocument=_policy("ecr:GetAuthorizationToken", "s3:GetObject"),
            ),
            1,
            id="IAM5 Resource::* covering a scopable action",
        ),
        pytest.param(
            _resource(
                "AWS::IAM::Policy",
                POLICY_PATH,
                ["AwsSolutions-IAM5[Action::s3:*]"],
                PolicyDocument=_policy("s3:*", resource="arn:aws:s3:::bucket"),
            ),
            1,
            id="IAM5 on a wildcard action",
        ),
        pytest.param(
            _resource(
                "AWS::IAM::Policy",
                POLICY_PATH,
                [
                    "AwsSolutions-IAM5[Resource::arn:<AWS::Partition>:ec2:<AWS::Region>:"
                    "<AWS::AccountId>:instance/*]",
                    "AwsSolutions-IAM5[Resource::arn:<AWS::Partition>:route53:::change/*]",
                    "AwsSolutions-IAM5[Resource::<HandlerABC123.Arn>:*]",
                    "AwsSolutions-IAM5[Resource::arn:aws:ec2:us-east-2:123456789012:volume/*]",
                ],
            ),
            0,
            id="IAM5 on ARNs scoped to one resource type",
        ),
        pytest.param(
            _resource(
                "AWS::IAM::Policy",
                POLICY_PATH,
                [
                    "AwsSolutions-IAM5[Resource::arn:<AWS::Partition>:ec2:*:*:instance/*]",
                    "AwsSolutions-IAM5[Resource::arn:<AWS::Partition>:s3:::*]",
                    "AwsSolutions-IAM5[Resource::arn:<AWS::Partition>:ec2:<AWS::Region>:"
                    "<AWS::AccountId>:*]",
                ],
            ),
            3,
            id="IAM5 on ARNs with wildcard region, account or resource type",
        ),
        pytest.param(
            _resource(
                "AWS::EC2::VPC", "CalderaNetwork/Vpc/Resource", ["CloudFormation-Validate::W3010"]
            ),
            0,
            id="CDK validation acknowledgment outside cdk-nag",
        ),
        pytest.param(
            _resource("AWS::S3::Bucket", "CalderaAddons/Bucket/Resource", ["AwsSolutions-S1"]),
            1,
            id="any other rule",
        ),
    ],
)
def test_suppression_policy(template: dict, expected_count: int) -> None:
    assert len(suppression_violations(template)) == expected_count


@pytest.fixture(scope="module")
def synthesized_app(tmp_path_factory: pytest.TempPathFactory, new_app) -> cdk.App:
    build = runpy.run_path(str(REPO_ROOT / "app.py"))["build"]
    return build(
        new_app(
            {"aws:cdk:enable-path-metadata": True},
            outdir=str(tmp_path_factory.mktemp("cdk.out")),
        )
    )


def test_app_synthesizes_with_nag_checks_and_policy_compliant_suppressions(
    synthesized_app: cdk.App,
) -> None:
    assembly = synthesized_app.synth()

    violations = [v for stack in assembly.stacks for v in suppression_violations(stack.template)]

    assert violations == []
