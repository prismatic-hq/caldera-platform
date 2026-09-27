"""Every cdk-nag suppression in the app. Policy: REQUIREMENTS.md Section 4c.

Two explicit categories plus two derived from the policy documents themselves:
- IAM4 on the EKS cluster and node roles, for the managed policies EKS requires.
- IAM5 ``Resource::*`` on statements whose actions have no resource-level permissions.
- IAM5 ``Resource::*`` on the EC2 actions Lambda requires for VPC access, when the same
  policy denies them to function code.
- IAM5 on an ARN scoped to one resource type in this account and region, for resources
  controllers create at runtime (their IDs cannot be known at synth time).
"""

import re
from dataclasses import dataclass
from fnmatch import fnmatch

from aws_cdk import Acknowledgment, CfnResource, Stack, Validations
from aws_cdk import aws_iam as iam
from constructs import IConstruct

from caldera.constructs.cleanup import (
    DENY_FUNCTION_CODE,
    LAMBDA_VPC_ACCESS_ACTIONS,
    LAMBDA_VPC_DOCS,
)

EKS_DOCS = "https://docs.aws.amazon.com/eks/latest/userguide"
SAR_DOCS = "https://docs.aws.amazon.com/service-authorization/latest/reference/reference_policies_actions-resources-contextkeys.html"


@dataclass(frozen=True)
class Suppression:
    path: str
    finding_id: str
    reason: str


def _eks_managed_policy(role: str, policy: str, guide: str) -> Suppression:
    return Suppression(
        path=f"CalderaCluster/{role}/Resource",
        finding_id=f"AwsSolutions-IAM4[Policy::arn:<AWS::Partition>:iam::aws:policy/{policy}]",
        reason=f"EKS requires {policy} on this role: {EKS_DOCS}/{guide}",
    )


SUPPRESSIONS: list[Suppression] = [
    _eks_managed_policy("ClusterRole", "AmazonEKSClusterPolicy", "cluster-iam-role.html"),
    *(
        _eks_managed_policy(role, policy, "create-node-role.html")
        for role in ("SystemNodeRole", "KarpenterNodeRole")
        for policy in (
            "AmazonEKSWorkerNodePolicy",
            "AmazonEC2ContainerRegistryReadOnly",
            "AmazonSSMManagedInstanceCore",
        )
    ),
]

ACTIONS_WITHOUT_RESOURCE_LEVEL_PERMISSIONS = (
    "ec2:Describe*",
    "ecr:GetAuthorizationToken",
    "ecr-public:GetAuthorizationToken",
    "elasticloadbalancing:Describe*",
    "pricing:GetProducts",
    "route53:ListHostedZones",
    "route53:ListHostedZonesByName",
    "sts:GetServiceBearerToken",
)
PARTITION = r"(<AWS::Partition>|aws[a-z-]*)"
REGION = r"(<AWS::Region>|[a-z]{2}(-[a-z]+)+-\d)?"
ACCOUNT = r"(<AWS::AccountId>|\d{12})?"
SCOPED_WILDCARD_ARN = re.compile(
    rf"^arn:{PARTITION}:[a-z0-9-]+:{REGION}:{ACCOUNT}:[a-z-]+[/:][^*]*\*$"
)
FUNCTION_QUALIFIER_WILDCARD = re.compile(r"^<[A-Za-z0-9]+\.Arn>:\*$")


def flatten(reference: object) -> str:
    """Mirror cdk-nag's flattenCfnReference so findings match its IDs exactly."""
    if isinstance(reference, str):
        return reference.replace("${", "<").replace("}", ">")
    if isinstance(reference, dict):
        if "Fn::Join" in reference:
            delimiter, items = reference["Fn::Join"]
            return delimiter.join(flatten(item) for item in items)
        if "Fn::Sub" in reference:
            return flatten(reference["Fn::Sub"])
        if "Fn::GetAtt" in reference:
            resource, attribute = reference["Fn::GetAtt"]
            return f"<{flatten(resource)}.{flatten(attribute)}>"
        if "Fn::ImportValue" in reference:
            return flatten(reference["Fn::ImportValue"])
        if "Ref" in reference:
            return f"<{flatten(reference['Ref'])}>"
    return str(reference)


def _as_list(value: object) -> list:
    return value if isinstance(value, list) else [value]


def is_allowlisted_action(action: str) -> bool:
    return any(fnmatch(action, allowed) for allowed in ACTIONS_WITHOUT_RESOURCE_LEVEL_PERMISSIONS)


def is_scoped_wildcard(resource: str) -> bool:
    return bool(SCOPED_WILDCARD_ARN.match(resource) or FUNCTION_QUALIFIER_WILDCARD.match(resource))


def _statements(documents: list[dict]) -> list[dict]:
    return [statement for document in documents for statement in document.get("Statement", [])]


def denies_lambda_vpc_access_to_function_code(documents: list[dict]) -> bool:
    return any(
        statement.get("Effect") == "Deny"
        and set(LAMBDA_VPC_ACCESS_ACTIONS) <= set(_as_list(statement.get("Action", [])))
        and statement.get("Condition") == DENY_FUNCTION_CODE
        for statement in _statements(documents)
    )


def allowed_iam5_findings(documents: list[dict]) -> set[str]:
    statements = [s for s in _statements(documents) if s.get("Effect") == "Allow"]
    exempt = (
        set(LAMBDA_VPC_ACCESS_ACTIONS)
        if denies_lambda_vpc_access_to_function_code(documents)
        else set()
    )
    findings: set[str] = set()
    wildcard_actions = [
        action
        for statement in statements
        if "*" in map(flatten, _as_list(statement.get("Resource", [])))
        for action in _as_list(statement.get("Action", []))
    ]
    if wildcard_actions and all(
        is_allowlisted_action(action) or action in exempt for action in wildcard_actions
    ):
        findings.add("Resource::*")
    for statement in statements:
        for resource in map(flatten, _as_list(statement.get("Resource", []))):
            if resource != "*" and is_scoped_wildcard(resource):
                findings.add(f"Resource::{resource}")
        for action in _as_list(statement.get("Action", [])):
            if "*" in action and is_allowlisted_action(action):
                findings.add(f"Action::{action}")
    return findings


def _policy_documents(resource: CfnResource) -> list[dict]:
    stack = Stack.of(resource)
    if isinstance(resource, iam.CfnRole):
        policies = resource.policies if isinstance(resource.policies, list) else []
        return [stack.resolve(policy.policy_document) for policy in policies]
    if isinstance(resource, iam.CfnPolicy | iam.CfnManagedPolicy):
        return [stack.resolve(resource.policy_document)]
    return []


def _iam5_reason(finding: str, documents: list[dict]) -> str:
    if finding == "Resource::*" and denies_lambda_vpc_access_to_function_code(documents):
        return (
            "Lambda requires its VPC access actions on all resources; function code is denied "
            f"them, and other actions here have no resource-level permissions: {LAMBDA_VPC_DOCS}"
        )
    if finding.startswith(("Resource::*", "Action::")):
        return f"No resource-level permissions for these actions: {SAR_DOCS}"
    return (
        "Scoped to one resource type in this account and region; the IDs are "
        "created at runtime by the controller"
    )


def iam5_suppressions(root: IConstruct) -> list[Suppression]:
    return [
        Suppression(
            path=construct.node.path,
            finding_id=f"AwsSolutions-IAM5[{finding}]",
            reason=_iam5_reason(finding, documents),
        )
        for construct in root.node.find_all()
        if isinstance(construct, CfnResource)
        for documents in [_policy_documents(construct)]
        for finding in sorted(allowed_iam5_findings(documents))
    ]


def apply_suppressions(root: IConstruct, suppressions: list[Suppression] | None = None) -> None:
    suppressions = SUPPRESSIONS + iam5_suppressions(root) if suppressions is None else suppressions
    constructs = {construct.node.path: construct for construct in root.node.find_all()}
    for suppression in suppressions:
        if suppression.path not in constructs:
            raise LookupError(f"nag suppression target not found: {suppression.path}")
        Validations.of(constructs[suppression.path]).acknowledge(
            Acknowledgment(id=suppression.finding_id, reason=suppression.reason)
        )
