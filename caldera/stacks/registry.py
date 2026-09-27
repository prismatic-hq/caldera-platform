from aws_cdk import Duration, RemovalPolicy, Stack
from aws_cdk import aws_ecr as ecr
from constructs import Construct

from caldera.config import PlatformConfig

GOLDEN_DB = "golden-db"
BUILD_CACHE = "build-cache"


def _image_repository(
    scope: Construct, construct_id: str, name: str, moving_tag: str
) -> ecr.Repository:
    return ecr.Repository(
        scope,
        construct_id,
        repository_name=name,
        image_scan_on_push=True,
        image_tag_mutability=ecr.TagMutability.IMMUTABLE_WITH_EXCLUSION,
        image_tag_mutability_exclusion_filters=[
            ecr.ImageTagMutabilityExclusionFilter.wildcard(moving_tag)
        ],
        empty_on_delete=True,
        removal_policy=RemovalPolicy.DESTROY,
        lifecycle_rules=[
            ecr.LifecycleRule(
                rule_priority=1,
                description=f"keep images tagged {moving_tag}",
                tag_pattern_list=[moving_tag],
                max_image_count=9999,
            ),
            ecr.LifecycleRule(
                rule_priority=2,
                description="expire sha-* images 14 days after push",
                tag_pattern_list=["sha-*"],
                max_image_age=Duration.days(14),
            ),
            ecr.LifecycleRule(
                rule_priority=3,
                description="expire untagged images after 1 day",
                tag_status=ecr.TagStatus.UNTAGGED,
                max_image_age=Duration.days(1),
            ),
        ],
    )


class RegistryStack(Stack):
    """ECR repositories for the services, golden-db and the build cache."""

    def __init__(
        self, scope: Construct, construct_id: str, *, config: PlatformConfig, **kwargs
    ) -> None:
        super().__init__(scope, construct_id, **kwargs)
        self.service_repositories = {
            image: _image_repository(self, f"Repo-{image}", image, "main")
            for image in config.service_images
        }
        self.golden_db = _image_repository(self, "GoldenDb", GOLDEN_DB, "latest")
        self.build_cache = ecr.Repository(
            self,
            "BuildCache",
            repository_name=BUILD_CACHE,
            image_tag_mutability=ecr.TagMutability.MUTABLE,
            empty_on_delete=True,
            removal_policy=RemovalPolicy.DESTROY,
            lifecycle_rules=[
                ecr.LifecycleRule(
                    description="expire cache manifests 14 days after push",
                    tag_status=ecr.TagStatus.ANY,
                    max_image_age=Duration.days(14),
                )
            ],
        )

    @property
    def all_repositories(self) -> list[ecr.IRepository]:
        return [*self.service_repositories.values(), self.golden_db, self.build_cache]
