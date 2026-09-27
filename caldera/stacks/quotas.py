from aws_cdk import Duration, Stack
from aws_cdk import aws_iam as iam
from constructs import Construct

from caldera.config import PlatformConfig
from caldera.constructs.cleanup import CleanupProps, CleanupResource

LAMBDA_SERVICE_CODE = "lambda"
LAMBDA_CONCURRENCY_QUOTA_CODE = "L-B99A9384"
LAMBDA_CONCURRENT_EXECUTIONS = 50


class QuotaStack(Stack):
    """Account service quotas the platform needs, raised before any stack that depends on them."""

    def __init__(
        self, scope: Construct, construct_id: str, *, config: PlatformConfig, **kwargs
    ) -> None:
        super().__init__(scope, construct_id, **kwargs)
        quota_arn = self.format_arn(
            service="servicequotas",
            resource=LAMBDA_SERVICE_CODE,
            resource_name=LAMBDA_CONCURRENCY_QUOTA_CODE,
        )
        self.lambda_concurrency = CleanupResource(
            self,
            "LambdaConcurrency",
            props=CleanupProps(
                function_name=f"{config.cluster_name}-quotas",
                handler="quota.handler",
                resource_type="Custom::ServiceQuota",
                properties={
                    "ServiceCode": LAMBDA_SERVICE_CODE,
                    "QuotaCode": LAMBDA_CONCURRENCY_QUOTA_CODE,
                    "DesiredValue": str(LAMBDA_CONCURRENT_EXECUTIONS),
                },
                statements=[
                    iam.PolicyStatement(
                        actions=[
                            "servicequotas:GetServiceQuota",
                            "servicequotas:ListRequestedServiceQuotaChangeHistoryByQuota",
                            "servicequotas:RequestServiceQuotaIncrease",
                        ],
                        resources=[quota_arn],
                    )
                ],
                timeout=Duration.minutes(55),
            ),
        )
