from aws_cdk import RemovalPolicy, Stack
from aws_cdk import aws_iam as iam
from aws_cdk import aws_route53 as route53
from constructs import Construct

from caldera.config import PlatformConfig
from caldera.constructs.cleanup import CleanupProps, CleanupResource
from caldera.constructs.pod_identity import pod_identity_role


def change_records(zone_arn: str, *, names: list[str], types: list[str]) -> iam.PolicyStatement:
    """ChangeResourceRecordSets limited to record names and types, per the Route 53 condition keys:
    https://docs.aws.amazon.com/Route53/latest/DeveloperGuide/specifying-rrset-conditions.html
    """
    return iam.PolicyStatement(
        actions=["route53:ChangeResourceRecordSets"],
        resources=[zone_arn],
        conditions={
            "ForAllValues:StringLike": {
                "route53:ChangeResourceRecordSetsNormalizedRecordNames": names
            },
            "ForAllValues:StringEquals": {"route53:ChangeResourceRecordSetsRecordTypes": types},
        },
    )


class DnsStack(Stack):
    """Route 53 hosted zone and Pod Identity roles for external-dns and cert-manager."""

    def __init__(
        self, scope: Construct, construct_id: str, *, config: PlatformConfig, **kwargs
    ) -> None:
        super().__init__(scope, construct_id, **kwargs)
        self.zone = route53.PublicHostedZone(self, "Zone", zone_name=config.domain)
        self.zone.apply_removal_policy(RemovalPolicy.DESTROY)
        zone_arn = self.zone.hosted_zone_arn

        self.external_dns_role = pod_identity_role(
            self,
            "ExternalDnsRole",
            [
                change_records(
                    zone_arn,
                    names=[f"*{config.preview_domain}", f"*{config.dev_domain}"],
                    types=["A", "AAAA", "TXT"],
                ),
                iam.PolicyStatement(
                    actions=["route53:ListResourceRecordSets", "route53:ListTagsForResource"],
                    resources=[zone_arn],
                ),
                iam.PolicyStatement(actions=["route53:ListHostedZones"], resources=["*"]),
            ],
            cluster_name=config.cluster_name,
        )
        self.cert_manager_role = pod_identity_role(
            self,
            "CertManagerRole",
            [
                change_records(zone_arn, names=["_acme-challenge.*"], types=["TXT"]),
                iam.PolicyStatement(
                    actions=["route53:ListResourceRecordSets"], resources=[zone_arn]
                ),
                iam.PolicyStatement(
                    actions=["route53:GetChange"],
                    resources=[
                        self.format_arn(
                            service="route53",
                            region="",
                            account="",
                            resource="change",
                            resource_name="*",
                        )
                    ],
                ),
                iam.PolicyStatement(actions=["route53:ListHostedZonesByName"], resources=["*"]),
            ],
            cluster_name=config.cluster_name,
        )

        self.sweeper = CleanupResource(
            self,
            "ZoneSweeper",
            props=CleanupProps(
                function_name=f"{config.cluster_name}-zone-sweeper",
                handler="zone_sweeper.handler",
                resource_type="Custom::ZoneSweeper",
                properties={"HostedZoneId": self.zone.hosted_zone_id},
                statements=[
                    iam.PolicyStatement(
                        actions=[
                            "route53:GetHostedZone",
                            "route53:ListResourceRecordSets",
                            "route53:ChangeResourceRecordSets",
                        ],
                        resources=[zone_arn],
                    )
                ],
            ),
        )
        self.sweeper.node.add_dependency(self.zone)
