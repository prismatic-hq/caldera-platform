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
ENVOY_GATEWAY = Chart(
    "envoy-gateway",
    "gateway-helm",
    "oci://docker.io/envoyproxy/gateway-helm",
    "1.9.1",
    "envoy-gateway-system",
)
LOAD_BALANCER_CONTROLLER = Chart(
    "aws-load-balancer-controller",
    "aws-load-balancer-controller",
    "https://aws.github.io/eks-charts",
    "3.5.0",
    "kube-system",
)
CERT_MANAGER = Chart(
    "cert-manager", "cert-manager", "https://charts.jetstack.io", "v1.21.2", "cert-manager"
)
EXTERNAL_DNS = Chart(
    "external-dns",
    "external-dns",
    "https://kubernetes-sigs.github.io/external-dns",
    "1.22.0",
    "external-dns",
)
KEDA = Chart("keda", "keda", "https://kedacore.github.io/charts", "2.21.0", "keda")
METRICS_SERVER = Chart(
    "metrics-server",
    "metrics-server",
    "https://kubernetes-sigs.github.io/metrics-server",
    "3.14.0",
    "kube-system",
)
EXTERNAL_SECRETS = Chart(
    "external-secrets",
    "external-secrets",
    "https://charts.external-secrets.io",
    "2.11.0",
    "external-secrets",
)
ARC_CONTROLLER = Chart(
    "arc",
    "gha-runner-scale-set-controller",
    "oci://ghcr.io/actions/actions-runner-controller-charts/gha-runner-scale-set-controller",
    "0.14.2",
    "arc-systems",
)
ARC_RUNNER_SET = Chart(
    "",
    "gha-runner-scale-set",
    "oci://ghcr.io/actions/actions-runner-controller-charts/gha-runner-scale-set",
    "0.14.2",
    "arc-runners",
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
