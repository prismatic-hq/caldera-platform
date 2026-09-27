from aws_cdk import CfnOutput, Duration, Fn, RemovalPolicy, Stack
from aws_cdk import aws_ec2 as ec2
from aws_cdk import aws_eks_v2 as eks
from aws_cdk import aws_events as events
from aws_cdk import aws_events_targets as targets
from aws_cdk import aws_iam as iam
from aws_cdk import aws_logs as logs
from aws_cdk import aws_sqs as sqs
from aws_cdk.lambda_layer_kubectl_v35 import KubectlV35Layer
from constructs import Construct

from cdk.charts import CILIUM, KARPENTER, install
from cdk.config import PlatformConfig
from cdk.constructs.cleanup import vpc_arn
from cdk.constructs.kubectl_provider import harden_kubectl_provider
from cdk.constructs.pod_identity import pod_identity_role
from cdk.policies import cilium_operator_statements, karpenter_controller_statements
from cdk.stacks.network import NetworkStack

KUBERNETES_VERSION = eks.KubernetesVersion.V1_35
CILIUM_OPERATOR = "cilium-operator"
CILIUM_STARTUP_TAINT = {"key": "node.cilium.io/agent-not-ready", "value": "true"}
PREVIEW_TAINT = {"key": "prismatic.dev/preview", "value": "true", "effect": "NoSchedule"}
NODE_MANAGED_POLICIES = (
    "AmazonEKSWorkerNodePolicy",
    "AmazonEC2ContainerRegistryReadOnly",
    "AmazonSSMManagedInstanceCore",
)
ARM64_FAMILIES = ["c7g", "c8g", "m7g", "m8g", "r7g", "r8g"]
INTERRUPTION_EVENTS = {
    "SpotInterruption": ("aws.ec2", "EC2 Spot Instance Interruption Warning"),
    "Rebalance": ("aws.ec2", "EC2 Instance Rebalance Recommendation"),
    "InstanceStateChange": ("aws.ec2", "EC2 Instance State-change Notification"),
    "ScheduledChange": ("aws.health", "AWS Health Event"),
}


def _node_role(scope: Construct, construct_id: str) -> iam.Role:
    return iam.Role(
        scope,
        construct_id,
        assumed_by=iam.ServicePrincipal("ec2.amazonaws.com"),
        managed_policies=[
            iam.ManagedPolicy.from_aws_managed_policy_name(name) for name in NODE_MANAGED_POLICIES
        ],
    )


class ClusterStack(Stack):
    """EKS without default networking addons, Cilium in ENI mode, system nodes and Karpenter."""

    def __init__(
        self,
        scope: Construct,
        construct_id: str,
        *,
        config: PlatformConfig,
        network: NetworkStack,
        **kwargs,
    ) -> None:
        super().__init__(scope, construct_id, **kwargs)
        self.config = config
        self.vpc = network.vpc
        name = config.cluster_name

        control_plane_logs = logs.LogGroup(
            self,
            "ControlPlaneLogs",
            log_group_name=f"/aws/eks/{name}/cluster",
            retention=logs.RetentionDays.ONE_MONTH,
            removal_policy=RemovalPolicy.DESTROY,
        )
        cluster_role = iam.Role(
            self,
            "ClusterRole",
            assumed_by=iam.ServicePrincipal("eks.amazonaws.com"),
            managed_policies=[
                iam.ManagedPolicy.from_aws_managed_policy_name("AmazonEKSClusterPolicy")
            ],
        )
        self.cluster = eks.Cluster(
            self,
            "Cluster",
            cluster_name=name,
            version=KUBERNETES_VERSION,
            role=cluster_role,
            vpc=self.vpc,
            vpc_subnets=[ec2.SubnetSelection(subnet_type=ec2.SubnetType.PRIVATE_WITH_EGRESS)],
            endpoint_access=eks.EndpointAccess.PRIVATE,
            bootstrap_self_managed_addons=False,
            default_capacity_type=eks.DefaultCapacityType.NODEGROUP,
            default_capacity=0,
            cluster_logging=list(eks.ClusterLoggingTypes),
            kubectl_provider_options=eks.KubectlProviderOptions(
                kubectl_layer=KubectlV35Layer(self, "KubectlLayer"),
                private_subnets=self.vpc.private_subnets,
            ),
            removal_policy=RemovalPolicy.DESTROY,
        )
        self.cluster.node.add_dependency(control_plane_logs)
        harden_kubectl_provider(self.cluster, name)

        self.cilium = self._cilium()
        self.system_nodes = self._system_nodes()
        self.system_nodes.node.add_dependency(self.cilium)
        self.addons = [
            eks.Addon(self, f"Addon-{addon}", cluster=self.cluster, addon_name=addon)
            for addon in ("eks-pod-identity-agent", "coredns")
        ]
        for addon in self.addons:
            addon.node.add_dependency(self.system_nodes)
        self.karpenter = self._karpenter()
        self.karpenter.node.add_dependency(*self.addons)
        self.admin_role = self._admin_access()

    def _admin_access(self) -> iam.Role:
        principals = self.config.cluster_admin_principals
        trusted: iam.IPrincipal = (
            iam.CompositePrincipal(*(iam.ArnPrincipal(arn) for arn in principals))
            if principals
            else iam.AccountRootPrincipal()
        )
        role = iam.Role(self, "ClusterAdminRole", assumed_by=trusted)
        eks.AccessEntry(
            self,
            "ClusterAdminAccess",
            cluster=self.cluster,
            principal=role.role_arn,
            access_policies=[
                eks.AccessPolicy.from_access_policy_name(
                    "AmazonEKSClusterAdminPolicy", access_scope_type=eks.AccessScopeType.CLUSTER
                )
            ],
        )
        CfnOutput(self, "ClusterAdminRoleArn", value=role.role_arn)
        CfnOutput(self, "ClusterName", value=self.cluster.cluster_name)
        return role

    def associate(
        self,
        scope: Construct,
        construct_id: str,
        namespace: str,
        service_account: str,
        role: iam.IRole,
    ) -> eks.CfnPodIdentityAssociation:
        return eks.CfnPodIdentityAssociation(
            scope,
            construct_id,
            cluster_name=self.cluster.cluster_name,
            namespace=namespace,
            service_account=service_account,
            role_arn=role.role_arn,
        )

    @property
    def private_subnet_arns(self) -> list[str]:
        return [
            self.format_arn(service="ec2", resource="subnet", resource_name=subnet.subnet_id)
            for subnet in self.vpc.private_subnets
        ]

    def _cilium(self) -> eks.HelmChart:
        name = self.config.cluster_name
        operator_role = pod_identity_role(
            self,
            "CiliumOperatorRole",
            cilium_operator_statements(
                self, name, vpc_arn(self, self.vpc), self.private_subnet_arns
            ),
            cluster_name=name,
        )
        association = self.associate(
            self, "CiliumOperatorIdentity", CILIUM.namespace, CILIUM_OPERATOR, operator_role
        )
        endpoint_host = Fn.select(2, Fn.split("/", self.cluster.cluster_endpoint))
        chart = install(
            self,
            "Cilium",
            self.cluster,
            CILIUM,
            {
                "eni": {
                    "enabled": True,
                    "awsEnablePrefixDelegation": True,
                    "awsReleaseExcessIPs": True,
                    "updateEC2AdapterLimitViaAPI": True,
                    "eniTags": {f"kubernetes.io/cluster/{name}": "owned"},
                },
                "ipam": {"mode": "eni"},
                "routingMode": "native",
                "egressMasqueradeInterfaces": "ens+",
                "kubeProxyReplacement": True,
                "k8sServiceHost": endpoint_host,
                "k8sServicePort": 443,
                "operator": {"replicas": 2},
                "serviceAccounts": {"operator": {"name": CILIUM_OPERATOR}},
                "hubble": {"relay": {"enabled": True}, "ui": {"enabled": True}},
                "gatewayAPI": {"enabled": False},
                "ingressController": {"enabled": False},
            },
            wait=False,
        )
        chart.node.add_dependency(association)
        return chart

    def _system_nodes(self) -> eks.Nodegroup:
        template = ec2.LaunchTemplate(
            self,
            "SystemNodeTemplate",
            require_imdsv2=True,
            http_put_response_hop_limit=1,
            block_devices=[
                ec2.BlockDevice(
                    device_name="/dev/xvda",
                    volume=ec2.BlockDeviceVolume.ebs(
                        30, encrypted=True, volume_type=ec2.EbsDeviceVolumeType.GP3
                    ),
                )
            ],
        )
        return self.cluster.add_nodegroup_capacity(
            "SystemNodes",
            node_role=_node_role(self, "SystemNodeRole"),
            ami_type=eks.NodegroupAmiType.AL2023_ARM_64_STANDARD,
            instance_types=[ec2.InstanceType("m7g.large")],
            min_size=2,
            desired_size=2,
            max_size=3,
            labels={"prismatic.dev/node-role": "system"},
            taints=[eks.TaintSpec(effect=eks.TaintEffect.NO_EXECUTE, **CILIUM_STARTUP_TAINT)],
            launch_template_spec=eks.LaunchTemplateSpec(
                id=template.launch_template_id, version=template.latest_version_number
            ),
            subnets=ec2.SubnetSelection(subnet_type=ec2.SubnetType.PRIVATE_WITH_EGRESS),
        )

    def _interruption_queue(self) -> sqs.Queue:
        dead_letters = sqs.Queue(
            self,
            "KarpenterInterruptionDlq",
            retention_period=Duration.days(14),
            encryption=sqs.QueueEncryption.SQS_MANAGED,
            enforce_ssl=True,
        )
        queue = sqs.Queue(
            self,
            "KarpenterInterruptionQueue",
            retention_period=Duration.minutes(5),
            encryption=sqs.QueueEncryption.SQS_MANAGED,
            enforce_ssl=True,
            dead_letter_queue=sqs.DeadLetterQueue(queue=dead_letters, max_receive_count=3),
        )
        for rule_id, (source, detail_type) in INTERRUPTION_EVENTS.items():
            events.Rule(
                self,
                f"Karpenter{rule_id}",
                event_pattern=events.EventPattern(source=[source], detail_type=[detail_type]),
                targets=[targets.SqsQueue(queue)],
            )
        return queue

    def _karpenter(self) -> eks.HelmChart:
        name = self.config.cluster_name
        node_role = _node_role(self, "KarpenterNodeRole")
        eks.AccessEntry(
            self,
            "KarpenterNodeAccess",
            cluster=self.cluster,
            principal=node_role.role_arn,
            access_entry_type=eks.AccessEntryType.EC2_LINUX,
            access_policies=[],
        )
        queue = self._interruption_queue()
        controller_role = pod_identity_role(
            self,
            "KarpenterControllerRole",
            karpenter_controller_statements(
                self, name, self.cluster.cluster_arn, node_role.role_arn, queue.queue_arn
            ),
            cluster_name=name,
        )
        association = self.associate(
            self, "KarpenterIdentity", KARPENTER.namespace, "karpenter", controller_role
        )
        chart = install(
            self,
            "Karpenter",
            self.cluster,
            KARPENTER,
            {
                "settings": {"clusterName": name, "interruptionQueue": queue.queue_name},
                "serviceAccount": {"name": "karpenter"},
                "controller": {
                    "resources": {
                        "requests": {"cpu": "250m", "memory": "512Mi"},
                        "limits": {"memory": "512Mi"},
                    }
                },
            },
        )
        chart.node.add_dependency(association)
        node_class = self.cluster.add_manifest("KarpenterNodeClass", self._node_class(node_role))
        node_class.node.add_dependency(chart)
        for pool in (self._preview_pool(), self._baseline_pool()):
            manifest = self.cluster.add_manifest(f"NodePool-{pool['metadata']['name']}", pool)
            manifest.node.add_dependency(node_class)
        return chart

    def _node_class(self, node_role: iam.IRole) -> dict:
        name = self.config.cluster_name
        return {
            "apiVersion": "karpenter.k8s.aws/v1",
            "kind": "EC2NodeClass",
            "metadata": {"name": "default"},
            "spec": {
                "role": node_role.role_name,
                "amiSelectorTerms": [{"alias": "al2023@latest"}],
                "subnetSelectorTerms": [{"tags": {"karpenter.sh/discovery": name}}],
                "securityGroupSelectorTerms": [{"id": self.cluster.cluster_security_group_id}],
                "metadataOptions": {"httpTokens": "required", "httpPutResponseHopLimit": 1},
                "blockDeviceMappings": [
                    {
                        "deviceName": "/dev/xvda",
                        "ebs": {"volumeSize": "50Gi", "volumeType": "gp3", "encrypted": True},
                    }
                ],
                "tags": {"prismatic:platform": name},
            },
        }

    @staticmethod
    def _node_pool(
        name: str,
        capacity_types: list[str],
        consolidate_after: str,
        cpu: str,
        taints: list[dict] | None = None,
    ) -> dict:
        return {
            "apiVersion": "karpenter.sh/v1",
            "kind": "NodePool",
            "metadata": {"name": name},
            "spec": {
                "template": {
                    "spec": {
                        "nodeClassRef": {
                            "group": "karpenter.k8s.aws",
                            "kind": "EC2NodeClass",
                            "name": "default",
                        },
                        "startupTaints": [{**CILIUM_STARTUP_TAINT, "effect": "NoExecute"}],
                        **({"taints": taints} if taints else {}),
                        "requirements": [
                            {"key": "kubernetes.io/arch", "operator": "In", "values": ["arm64"]},
                            {
                                "key": "karpenter.sh/capacity-type",
                                "operator": "In",
                                "values": capacity_types,
                            },
                            {
                                "key": "karpenter.k8s.aws/instance-family",
                                "operator": "In",
                                "values": ARM64_FAMILIES,
                            },
                            {
                                "key": "karpenter.k8s.aws/instance-size",
                                "operator": "In",
                                "values": ["large", "xlarge", "2xlarge"],
                            },
                        ],
                    }
                },
                "disruption": {
                    "consolidationPolicy": "WhenEmptyOrUnderutilized",
                    "consolidateAfter": consolidate_after,
                },
                "limits": {"cpu": cpu},
            },
        }

    def _preview_pool(self) -> dict:
        return self._node_pool(
            "preview-environments", ["spot", "on-demand"], "5m", "64", [PREVIEW_TAINT]
        )

    def _baseline_pool(self) -> dict:
        return self._node_pool("baseline", ["on-demand"], "30m", "16")
