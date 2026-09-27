import re

from aws_cdk import CfnOutput, Stack
from aws_cdk import aws_ecr as ecr
from aws_cdk import aws_events as events
from aws_cdk import aws_iam as iam
from aws_cdk import aws_ssm as ssm
from constructs import Construct

from cdk.config import PlatformConfig
from cdk.constructs.pod_identity import pod_identity_role
from cdk.stacks.cluster import ClusterStack
from cdk.stacks.registry import RegistryStack
from preview_cli.parameters import DATASET_VERSION_PARAMETER, PREVIEW_DOMAIN_PARAMETER

GITHUB_ISSUER = "token.actions.githubusercontent.com"
RUNNER_NAMESPACE = "arc-runners"
RUNNER_SERVICE_ACCOUNT = "arc-runner"
EVENT_BUS = "prismatic-events"


def push_role_output(repo: str) -> str:
    return "".join(part.capitalize() for part in re.split(r"[^0-9A-Za-z]+", repo)) + "PushRoleArn"


def _push_and_describe(role: iam.IGrantable, repositories: list[ecr.IRepository]) -> None:
    for repository in repositories:
        repository.grant_pull_push(role)
        repository.grant(role, "ecr:DescribeImages")


class CiAccessStack(Stack):
    """GitHub OIDC provider and CI roles, plus Pod Identity roles for in-cluster runners."""

    def __init__(
        self,
        scope: Construct,
        construct_id: str,
        *,
        config: PlatformConfig,
        cluster: ClusterStack,
        registry: RegistryStack,
        **kwargs,
    ) -> None:
        super().__init__(scope, construct_id, **kwargs)
        self.config = config
        self.github = iam.OidcProviderNative(
            self,
            "GitHubOidc",
            url=f"https://{GITHUB_ISSUER}",
            client_ids=["sts.amazonaws.com"],
        )
        self.bus = events.EventBus(self, "EventBus", event_bus_name=EVENT_BUS)

        service_repositories = list(registry.service_repositories.values())
        self.push_roles = {}
        for service in config.registry.services:
            role = self._github_role(
                f"Push-{service.repo}",
                service.repo,
                "ref:refs/heads/*",
                role_name=f"{config.cluster_name}-github-push-{service.repo}",
            )
            _push_and_describe(
                role, [registry.service_repositories[service.image], registry.build_cache]
            )
            self.bus.grant_put_events_to(role)
            self.push_roles[service.repo] = role
            CfnOutput(self, push_role_output(service.repo), value=role.role_arn)

        self.golden_image_role = self._github_role(
            "GoldenImage", config.platform_repo, "ref:refs/heads/main"
        )
        _push_and_describe(self.golden_image_role, [registry.golden_db, registry.build_cache])
        self.bus.grant_put_events_to(self.golden_image_role)

        self.runner_role = pod_identity_role(
            self, "RunnerRole", [], cluster_name=config.cluster_name
        )
        _push_and_describe(
            self.runner_role,
            [*service_repositories, registry.golden_db, registry.e2e, registry.build_cache],
        )
        self.bus.grant_put_events_to(self.runner_role)
        self._runner_parameters(config)
        cluster.associate(
            self, "RunnerIdentity", RUNNER_NAMESPACE, RUNNER_SERVICE_ACCOUNT, self.runner_role
        )

    def _runner_parameters(self, config: PlatformConfig) -> None:
        """SSM parameters the preview CLI reads; the golden-image job writes the dataset version."""
        domain = ssm.StringParameter(
            self,
            "PreviewDomain",
            parameter_name=PREVIEW_DOMAIN_PARAMETER,
            string_value=config.preview_domain,
        )
        domain.grant_read(self.runner_role)
        dataset_version = self.format_arn(
            service="ssm",
            resource="parameter",
            resource_name=DATASET_VERSION_PARAMETER.lstrip("/"),
        )
        self.runner_role.add_to_principal_policy(
            iam.PolicyStatement(
                actions=["ssm:GetParameter", "ssm:PutParameter"], resources=[dataset_version]
            )
        )

    def _github_role(
        self, construct_id: str, repo: str, subject: str, *, role_name: str | None = None
    ) -> iam.Role:
        """A role that only push events from one repository can assume (never fork PRs)."""
        return iam.Role(
            self,
            construct_id,
            role_name=role_name,
            assumed_by=iam.WebIdentityPrincipal(
                self.github.oidc_provider_arn,
                conditions={
                    "StringEquals": {f"{GITHUB_ISSUER}:aud": "sts.amazonaws.com"},
                    "StringLike": {
                        f"{GITHUB_ISSUER}:sub": f"repo:{self.config.github_org}/{repo}:{subject}"
                    },
                },
            ),
        )
