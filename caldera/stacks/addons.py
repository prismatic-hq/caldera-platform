from pathlib import Path

import yaml
from aws_cdk import Stack
from aws_cdk import aws_eks_v2 as eks
from aws_cdk import aws_iam as iam
from constructs import Construct, IConstruct

from caldera import charts
from caldera.config import REPO_ROOT, SSM_PREFIX, PlatformConfig
from caldera.constructs.cleanup import CleanupNetwork, CleanupProps, CleanupResource, vpc_arn
from caldera.constructs.pod_identity import pod_identity_role
from caldera.policies import load_balancer_controller_statements
from caldera.stacks.ci_access import RUNNER_NAMESPACE, RUNNER_SERVICE_ACCOUNT
from caldera.stacks.cluster import ClusterStack
from caldera.stacks.dns import DnsStack
from caldera.stacks.network import NetworkStack

PLATFORM_MANIFESTS = REPO_ROOT / "platform"
GATEWAY_NAMESPACE = charts.ENVOY_GATEWAY.namespace
GATEWAY_NAME = "preview"
GATEWAY_CLASS = "envoy-gateway"
WILDCARD_SECRET = "wildcard-tls"
CLUSTER_ISSUER = "letsencrypt"
GITHUB_APP_SECRET = "github-app"
RUNNER_REPOS_CONTEXT = "runnerRepos"
NLB_ANNOTATIONS = {
    f"service.beta.kubernetes.io/aws-load-balancer-{key}": value
    for key, value in {
        "scheme": "internet-facing",
        "nlb-target-type": "ip",
        "proxy-protocol": "*",
    }.items()
}


def platform_manifests(directory: Path = PLATFORM_MANIFESTS) -> dict[str, list[dict]]:
    return {
        path.stem: [doc for doc in yaml.safe_load_all(path.read_text()) if doc]
        for path in sorted(directory.glob("*.yaml"))
    }


class AddonsStack(Stack):
    """Cluster addons installed as Helm charts; Cilium and Karpenter live in ClusterStack."""

    def __init__(
        self,
        scope: Construct,
        construct_id: str,
        *,
        config: PlatformConfig,
        network: NetworkStack,
        cluster: ClusterStack,
        dns: DnsStack,
        **kwargs,
    ) -> None:
        super().__init__(scope, construct_id, **kwargs)
        self.config = config
        self.platform = cluster
        self.cluster = cluster.cluster
        self.dns = dns
        self.vpc = network.vpc
        self.installed: list[IConstruct] = []

        self._chart("MetricsServer", charts.METRICS_SERVER, {"replicas": 2})
        load_balancer_controller = self._load_balancer_controller()
        cert_manager = self._cert_manager()
        external_dns = self._external_dns()
        gateway = self._gateway(load_balancer_controller, cert_manager, external_dns)
        keda = self._chart("Keda", charts.KEDA, {})
        external_secrets = self._external_secrets()
        self._platform_manifests(keda)
        self._runners(external_secrets)
        self._drainer(network, gateway)

    def _chart(
        self,
        construct_id: str,
        chart: charts.Chart,
        values: dict,
        *,
        identity: tuple[str, iam.IRole] | None = None,
        after: list[IConstruct] | None = None,
        release: str | None = None,
    ) -> eks.HelmChart:
        installed = charts.install(self, construct_id, self.cluster, chart, values, release=release)
        if identity:
            service_account, role = identity
            installed.node.add_dependency(
                self.platform.associate(
                    self, f"{construct_id}Identity", chart.namespace, service_account, role
                )
            )
        for dependency in after or []:
            installed.node.add_dependency(dependency)
        self.installed.append(installed)
        return installed

    def _manifest(
        self, construct_id: str, *documents: dict, after: list[IConstruct]
    ) -> eks.KubernetesManifest:
        manifest = eks.KubernetesManifest(
            self, construct_id, cluster=self.cluster, manifest=list(documents), overwrite=True
        )
        for dependency in after:
            manifest.node.add_dependency(dependency)
        self.installed.append(manifest)
        return manifest

    def _load_balancer_controller(self) -> eks.HelmChart:
        name = self.config.cluster_name
        role = pod_identity_role(
            self,
            "LoadBalancerControllerRole",
            load_balancer_controller_statements(
                self, name, self.vpc.vpc_id, vpc_arn(self, self.vpc)
            ),
            cluster_name=name,
        )
        return self._chart(
            "LoadBalancerController",
            charts.LOAD_BALANCER_CONTROLLER,
            {
                "clusterName": name,
                "region": self.region,
                "vpcId": self.vpc.vpc_id,
                "replicaCount": 2,
                "enableServiceMutatorWebhook": False,
                "serviceAccount": {"name": "aws-load-balancer-controller"},
            },
            identity=("aws-load-balancer-controller", role),
        )

    def _cert_manager(self) -> eks.KubernetesManifest:
        chart = self._chart(
            "CertManager",
            charts.CERT_MANAGER,
            {
                "crds": {"enabled": True},
                "serviceAccount": {"name": "cert-manager"},
                "extraArgs": [
                    "--dns01-recursive-nameservers-only",
                    "--dns01-recursive-nameservers=1.1.1.1:53,8.8.8.8:53",
                ],
            },
            identity=("cert-manager", self.dns.cert_manager_role),
        )
        issuer = {
            "apiVersion": "cert-manager.io/v1",
            "kind": "ClusterIssuer",
            "metadata": {"name": CLUSTER_ISSUER},
            "spec": {
                "acme": {
                    "server": "https://acme-v02.api.letsencrypt.org/directory",
                    "email": self.config.acme_email,
                    "privateKeySecretRef": {"name": f"{CLUSTER_ISSUER}-account"},
                    "solvers": [
                        {
                            "dns01": {
                                "route53": {
                                    "region": self.region,
                                    "hostedZoneID": self.dns.zone.hosted_zone_id,
                                }
                            }
                        }
                    ],
                }
            },
        }
        return self._manifest("ClusterIssuer", issuer, after=[chart])

    def _external_dns(self) -> eks.HelmChart:
        return self._chart(
            "ExternalDns",
            charts.EXTERNAL_DNS,
            {
                "provider": {"name": "aws"},
                "sources": ["service"],
                "policy": "sync",
                "registry": "txt",
                "txtOwnerId": self.config.cluster_name,
                "domainFilters": [self.config.domain],
                "extraArgs": ["--aws-zone-type=public"],
                "env": [{"name": "AWS_DEFAULT_REGION", "value": self.region}],
                "serviceAccount": {"name": "external-dns"},
            },
            identity=("external-dns", self.dns.external_dns_role),
        )

    def _gateway(self, *ready: IConstruct) -> eks.KubernetesManifest:
        """One shared Gateway behind one NLB; every preview HTTPRoute attaches to it."""
        envoy_gateway = self._chart("EnvoyGateway", charts.ENVOY_GATEWAY, {}, after=list(ready))
        hostnames = [f"*.{self.config.preview_domain}", f"*.{self.config.dev_domain}"]
        certificate = {
            "apiVersion": "cert-manager.io/v1",
            "kind": "Certificate",
            "metadata": {"name": "wildcard", "namespace": GATEWAY_NAMESPACE},
            "spec": {
                "secretName": WILDCARD_SECRET,
                "dnsNames": hostnames,
                "issuerRef": {"kind": "ClusterIssuer", "name": CLUSTER_ISSUER},
            },
        }
        proxy = {
            "apiVersion": "gateway.envoyproxy.io/v1alpha1",
            "kind": "EnvoyProxy",
            "metadata": {"name": "nlb", "namespace": GATEWAY_NAMESPACE},
            "spec": {
                "provider": {
                    "type": "Kubernetes",
                    "kubernetes": {
                        "envoyDeployment": {"replicas": 2},
                        "envoyService": {
                            "type": "LoadBalancer",
                            "loadBalancerClass": "service.k8s.aws/nlb",
                            "externalTrafficPolicy": "Cluster",
                            "annotations": {
                                **NLB_ANNOTATIONS,
                                "external-dns.alpha.kubernetes.io/hostname": ",".join(hostnames),
                            },
                        },
                    },
                }
            },
        }
        gateway_class = {
            "apiVersion": "gateway.networking.k8s.io/v1",
            "kind": "GatewayClass",
            "metadata": {"name": GATEWAY_CLASS},
            "spec": {
                "controllerName": "gateway.envoyproxy.io/gatewayclass-controller",
                "parametersRef": {
                    "group": "gateway.envoyproxy.io",
                    "kind": "EnvoyProxy",
                    "name": "nlb",
                    "namespace": GATEWAY_NAMESPACE,
                },
            },
        }

        def listener(name: str, hostname: str) -> dict:
            return {
                "name": name,
                "protocol": "HTTPS",
                "port": 443,
                "hostname": hostname,
                "tls": {"mode": "Terminate", "certificateRefs": [{"name": WILDCARD_SECRET}]},
                "allowedRoutes": {"namespaces": {"from": "All"}},
            }

        gateway = {
            "apiVersion": "gateway.networking.k8s.io/v1",
            "kind": "Gateway",
            "metadata": {"name": GATEWAY_NAME, "namespace": GATEWAY_NAMESPACE},
            "spec": {
                "gatewayClassName": GATEWAY_CLASS,
                "listeners": [listener("https", hostnames[0]), listener("https-dev", hostnames[1])],
            },
        }
        client_traffic = {
            "apiVersion": "gateway.envoyproxy.io/v1alpha1",
            "kind": "ClientTrafficPolicy",
            "metadata": {"name": "proxy-protocol", "namespace": GATEWAY_NAMESPACE},
            "spec": {
                "targetRefs": [
                    {"group": "gateway.networking.k8s.io", "kind": "Gateway", "name": GATEWAY_NAME}
                ],
                "enableProxyProtocol": True,
            },
        }
        documents = [certificate, proxy, gateway_class, gateway, client_traffic]
        if self.config.allowlist_cidrs:
            documents.append(self._allowlist())
        return self._manifest("Gateway", *documents, after=[envoy_gateway])

    def _allowlist(self) -> dict:
        return {
            "apiVersion": "gateway.envoyproxy.io/v1alpha1",
            "kind": "SecurityPolicy",
            "metadata": {"name": "ip-allowlist", "namespace": GATEWAY_NAMESPACE},
            "spec": {
                "targetRefs": [
                    {"group": "gateway.networking.k8s.io", "kind": "Gateway", "name": GATEWAY_NAME}
                ],
                "authorization": {
                    "defaultAction": "Deny",
                    "rules": [
                        {
                            "name": "allowlisted-cidrs",
                            "action": "Allow",
                            "principal": {"clientCIDRs": list(self.config.allowlist_cidrs)},
                        }
                    ],
                },
            },
        }

    def _external_secrets(self) -> eks.KubernetesManifest:
        prefix = SSM_PREFIX.strip("/")
        role = pod_identity_role(
            self,
            "ExternalSecretsRole",
            [
                iam.PolicyStatement(
                    actions=["ssm:GetParameter", "ssm:GetParameters", "ssm:GetParametersByPath"],
                    resources=[
                        self.format_arn(
                            service="ssm", resource="parameter", resource_name=f"{prefix}/*"
                        )
                    ],
                )
            ],
            cluster_name=self.config.cluster_name,
        )
        chart = self._chart(
            "ExternalSecrets",
            charts.EXTERNAL_SECRETS,
            {"installCRDs": True, "serviceAccount": {"name": "external-secrets"}},
            identity=("external-secrets", role),
        )
        store = {
            "apiVersion": "external-secrets.io/v1",
            "kind": "ClusterSecretStore",
            "metadata": {"name": "parameter-store"},
            "spec": {"provider": {"aws": {"service": "ParameterStore", "region": self.region}}},
        }
        return self._manifest("ParameterStore", store, after=[chart])

    def _platform_manifests(self, keda: eks.HelmChart) -> None:
        """PriorityClasses, headroom and its KEDA ScaledObject from platform/."""
        manifests = platform_manifests()
        base = self._manifest(
            "PlatformBase",
            *manifests["namespace"],
            *manifests["priorityclasses"],
            after=[self.platform.karpenter],
        )
        headroom = self._manifest("Headroom", *manifests["headroom"], after=[base])
        self._manifest(
            "HeadroomScaling", *manifests["headroom-scaledobject"], after=[headroom, keda]
        )

    def _runners(self, parameter_store: IConstruct) -> None:
        """ARC controller plus one runner scale set per repository, credentials from SSM."""
        controller = self._chart("ArcController", charts.ARC_CONTROLLER, {})
        github_app = {
            "apiVersion": "external-secrets.io/v1",
            "kind": "ExternalSecret",
            "metadata": {"name": GITHUB_APP_SECRET, "namespace": RUNNER_NAMESPACE},
            "spec": {
                "secretStoreRef": {"kind": "ClusterSecretStore", "name": "parameter-store"},
                "target": {"name": GITHUB_APP_SECRET},
                "data": [
                    {
                        "secretKey": key,
                        "remoteRef": {"key": f"{SSM_PREFIX}github-app/{key.replace('_', '-')}"},
                    }
                    for key in (
                        "github_app_id",
                        "github_app_installation_id",
                        "github_app_private_key",
                    )
                ],
            },
        }
        namespace = {
            "apiVersion": "v1",
            "kind": "Namespace",
            "metadata": {"name": RUNNER_NAMESPACE},
        }
        service_account = {
            "apiVersion": "v1",
            "kind": "ServiceAccount",
            "metadata": {"name": RUNNER_SERVICE_ACCOUNT, "namespace": RUNNER_NAMESPACE},
        }
        rbac = self._runner_rbac()
        access = self._manifest(
            "RunnerAccess", namespace, service_account, github_app, *rbac, after=[parameter_store]
        )
        repos = self.node.try_get_context(RUNNER_REPOS_CONTEXT) or [
            *self.config.service_repos,
            self.config.platform_repo,
        ]
        for repo in repos:
            self._chart(
                f"Runners-{repo}",
                charts.ARC_RUNNER_SET,
                {
                    "githubConfigUrl": f"https://github.com/{self.config.github_org}/{repo}",
                    "githubConfigSecret": GITHUB_APP_SECRET,
                    "runnerScaleSetName": f"{repo}-runners",
                    "minRunners": 0,
                    "maxRunners": 10,
                    "containerMode": {"type": "dind"},
                    "template": {"spec": {"serviceAccountName": RUNNER_SERVICE_ACCOUNT}},
                },
                after=[controller, access],
                release=f"{repo}-runners",
            )

    @staticmethod
    def _runner_rbac() -> list[dict]:
        """In-cluster credentials for `preview env up|down|reset` run by the runners."""
        rules = [
            {
                "apiGroups": [""],
                "resources": ["namespaces"],
                "verbs": ["get", "list", "watch", "create", "delete", "patch"],
            },
            {
                "apiGroups": [
                    "",
                    "apps",
                    "batch",
                    "networking.k8s.io",
                    "gateway.networking.k8s.io",
                    "cilium.io",
                ],
                "resources": [
                    "pods",
                    "pods/log",
                    "services",
                    "secrets",
                    "configmaps",
                    "serviceaccounts",
                    "events",
                    "deployments",
                    "statefulsets",
                    "replicasets",
                    "jobs",
                    "networkpolicies",
                    "httproutes",
                    "ciliumnetworkpolicies",
                ],
                "verbs": [
                    "get",
                    "list",
                    "watch",
                    "create",
                    "update",
                    "patch",
                    "delete",
                    "deletecollection",
                ],
            },
        ]
        return [
            {
                "apiVersion": "rbac.authorization.k8s.io/v1",
                "kind": "ClusterRole",
                "metadata": {"name": "preview-deployer"},
                "rules": rules,
            },
            {
                "apiVersion": "rbac.authorization.k8s.io/v1",
                "kind": "ClusterRoleBinding",
                "metadata": {"name": "preview-deployer"},
                "roleRef": {
                    "apiGroup": "rbac.authorization.k8s.io",
                    "kind": "ClusterRole",
                    "name": "preview-deployer",
                },
                "subjects": [
                    {
                        "kind": "ServiceAccount",
                        "name": RUNNER_SERVICE_ACCOUNT,
                        "namespace": RUNNER_NAMESPACE,
                    }
                ],
            },
        ]

    def _drainer(self, network: NetworkStack, gateway: IConstruct) -> None:
        name = self.config.cluster_name
        drainer = CleanupResource(
            self,
            "Drainer",
            props=CleanupProps(
                function_name=f"{name}-drainer",
                handler="drainer.handler",
                resource_type="Custom::Drainer",
                properties={
                    "ClusterName": name,
                    "Region": self.region,
                    "VpcId": self.vpc.vpc_id,
                    "HostedZoneId": self.dns.zone.hosted_zone_id,
                    "RecordNames": [
                        f"*.{self.config.preview_domain}",
                        f"*.{self.config.dev_domain}",
                    ],
                },
                statements=[
                    iam.PolicyStatement(
                        actions=["eks:DescribeCluster"], resources=[self.cluster.cluster_arn]
                    ),
                    iam.PolicyStatement(
                        actions=[
                            "ec2:DescribeInstances",
                            "elasticloadbalancing:DescribeLoadBalancers",
                            "elasticloadbalancing:DescribeTags",
                        ],
                        resources=["*"],
                    ),
                    iam.PolicyStatement(
                        actions=["route53:ListResourceRecordSets"],
                        resources=[self.dns.zone.hosted_zone_arn],
                    ),
                ],
                network=CleanupNetwork(
                    vpc=self.vpc,
                    subnets=self.vpc.private_subnets,
                    security_group=self.cluster.cluster_security_group,
                ),
            ),
        )
        admin = eks.AccessEntry(
            self,
            "DrainerAccess",
            cluster=self.cluster,
            principal=drainer.role.role_arn,
            access_policies=[
                eks.AccessPolicy.from_access_policy_name(
                    "AmazonEKSClusterAdminPolicy", access_scope_type=eks.AccessScopeType.CLUSTER
                )
            ],
        )
        drainer.resource.node.add_dependency(
            admin, gateway, self.platform.karpenter, *self.installed
        )
