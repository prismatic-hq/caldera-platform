"""Pinned Helm charts for the cluster addons (REQUIREMENTS.md Section 4)."""

from dataclasses import dataclass

from aws_cdk import Duration
from aws_cdk import aws_eks_v2 as eks
from constructs import Construct


@dataclass(frozen=True)
class Chart:
    release: str
    chart: str
    repository: str
    version: str
    namespace: str


CILIUM = Chart("cilium", "cilium", "https://helm.cilium.io", "1.20.2", "kube-system")
KARPENTER = Chart(
    "karpenter", "karpenter", "oci://public.ecr.aws/karpenter/karpenter", "1.14.1", "karpenter"
)


def install(
    scope: Construct,
    construct_id: str,
    cluster: eks.ICluster,
    chart: Chart,
    values: dict,
    *,
    release: str | None = None,
    wait: bool = True,
) -> eks.HelmChart:
    return eks.HelmChart(
        scope,
        construct_id,
        cluster=cluster,
        chart=chart.chart,
        repository=chart.repository,
        version=chart.version,
        namespace=chart.namespace,
        release=release or chart.release,
        create_namespace=True,
        values=values,
        wait=wait,
        timeout=Duration.minutes(15),
    )
