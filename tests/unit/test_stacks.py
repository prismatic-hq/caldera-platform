import json

import pytest
from aws_cdk.assertions import Match, Template

from caldera.platform import build_platform

LATEST_RUNTIMES = {"python3.14", "nodejs24.x"}


@pytest.fixture(scope="module")
def synth(new_app):
    def build(context: dict | None = None) -> dict[str, Template]:
        stacks = build_platform(new_app(context))
        return {name: Template.from_stack(stack) for name, stack in stacks.items()}

    return build


@pytest.fixture(scope="module")
def templates(synth) -> dict[str, Template]:
    return synth()


def resources(template: Template, resource_type: str) -> dict[str, dict]:
    return template.find_resources(resource_type)


def test_network_spans_two_azs_with_flow_logs_one_nat_and_s3_endpoint(templates) -> None:
    network = templates["Network"]

    network.resource_count_is("AWS::EC2::Subnet", 4)
    network.resource_count_is("AWS::EC2::NatGateway", 1)
    network.resource_count_is("AWS::EC2::FlowLog", 1)
    network.has_resource_properties(
        "AWS::EC2::VPCEndpoint",
        {"ServiceName": Match.object_like({"Fn::Join": Match.any_value()})},
    )
    network.has_resource_properties(
        "Custom::NetworkSweeper", {"ClusterName": "caldera", "ParameterPrefix": "/prismatic/"}
    )


def test_nat_gateways_context_flag_adds_a_second_nat(synth) -> None:
    synth({"natGateways": 2})["Network"].resource_count_is("AWS::EC2::NatGateway", 2)


def test_nat_gateways_context_flag_rejects_other_counts(synth) -> None:
    with pytest.raises(ValueError, match="natGateways must be 1 or 2"):
        synth({"natGateways": 3})


def test_private_subnets_are_tagged_for_karpenter_discovery(templates) -> None:
    templates["Network"].has_resource_properties(
        "AWS::EC2::Subnet",
        {"Tags": Match.array_with([{"Key": "karpenter.sh/discovery", "Value": "caldera"}])},
    )


def test_cluster_is_private_without_default_networking_addons(templates) -> None:
    templates["Cluster"].has_resource_properties(
        "AWS::EKS::Cluster",
        {
            "BootstrapSelfManagedAddons": False,
            "ResourcesVpcConfig": Match.object_like(
                {"EndpointPublicAccess": False, "EndpointPrivateAccess": True}
            ),
            "Logging": {
                "ClusterLogging": {
                    "EnabledTypes": [
                        {"Type": kind}
                        for kind in (
                            "api",
                            "audit",
                            "authenticator",
                            "controllerManager",
                            "scheduler",
                        )
                    ]
                }
            },
        },
    )


def test_cluster_installs_only_pod_identity_and_coredns_addons(templates) -> None:
    addons = resources(templates["Cluster"], "AWS::EKS::Addon")

    assert {a["Properties"]["AddonName"] for a in addons.values()} == {
        "eks-pod-identity-agent",
        "coredns",
    }


def test_cilium_is_installed_before_the_system_node_group(templates) -> None:
    cluster = templates["Cluster"]
    cilium_id = next(
        key
        for key, chart in resources(cluster, "Custom::AWSCDK-EKS-HelmChart").items()
        if chart["Properties"]["Chart"] == "cilium"
    )
    nodegroup = next(iter(resources(cluster, "AWS::EKS::Nodegroup").values()))

    assert cilium_id in nodegroup["DependsOn"]
    assert nodegroup["Properties"]["Taints"] == [
        {"Effect": "NO_EXECUTE", "Key": "node.cilium.io/agent-not-ready", "Value": "true"}
    ]


def test_cilium_runs_eni_ipam_with_kube_proxy_replacement(templates) -> None:
    cilium = next(
        chart
        for chart in resources(templates["Cluster"], "Custom::AWSCDK-EKS-HelmChart").values()
        if chart["Properties"]["Chart"] == "cilium"
    )
    values = json.dumps(cilium["Properties"]["Values"])

    for fragment in (
        '\\"ipam\\":{\\"mode\\":\\"eni\\"}',
        '\\"awsEnablePrefixDelegation\\":true',
        '\\"kubeProxyReplacement\\":true',
        '\\"gatewayAPI\\":{\\"enabled\\":false}',
        '\\"ui\\":{\\"enabled\\":true}',
    ):
        assert fragment in values


def test_karpenter_node_pools_carry_the_cilium_startup_taint(templates) -> None:
    manifests = [
        json.loads(r["Properties"]["Manifest"])
        for r in resources(templates["Cluster"], "Custom::AWSCDK-EKS-KubernetesResource").values()
        if isinstance(r["Properties"]["Manifest"], str)
    ]
    pools = {m["metadata"]["name"]: m for doc in manifests for m in doc if m["kind"] == "NodePool"}

    assert set(pools) == {"preview-environments", "baseline"}
    for pool in pools.values():
        assert pool["spec"]["template"]["spec"]["startupTaints"] == [
            {"key": "node.cilium.io/agent-not-ready", "value": "true", "effect": "NoExecute"}
        ]
    capacity = {
        name: next(
            r["values"]
            for r in pool["spec"]["template"]["spec"]["requirements"]
            if r["key"] == "karpenter.sh/capacity-type"
        )
        for name, pool in pools.items()
    }
    assert capacity == {"preview-environments": ["spot", "on-demand"], "baseline": ["on-demand"]}
    assert pools["preview-environments"]["spec"]["disruption"]["consolidateAfter"] == "5m"


def test_karpenter_interruption_queue_has_dlq_and_enforces_ssl(templates) -> None:
    cluster = templates["Cluster"]

    cluster.resource_count_is("AWS::SQS::Queue", 2)
    cluster.has_resource_properties(
        "AWS::SQS::Queue", {"RedrivePolicy": Match.object_like({"maxReceiveCount": 3})}
    )
    cluster.has_resource_properties(
        "AWS::SQS::QueuePolicy",
        {
            "PolicyDocument": {
                "Statement": Match.array_with(
                    [
                        Match.object_like(
                            {
                                "Effect": "Deny",
                                "Condition": {"Bool": {"aws:SecureTransport": "false"}},
                            }
                        )
                    ]
                ),
                "Version": "2012-10-17",
            }
        },
    )


def test_node_volumes_are_encrypted_and_imdsv2_is_required(templates) -> None:
    templates["Cluster"].has_resource_properties(
        "AWS::EC2::LaunchTemplate",
        {
            "LaunchTemplateData": Match.object_like(
                {
                    "MetadataOptions": Match.object_like({"HttpTokens": "required"}),
                    "BlockDeviceMappings": [
                        Match.object_like(
                            {"Ebs": Match.object_like({"Encrypted": True, "VolumeType": "gp3"})}
                        )
                    ],
                }
            )
        },
    )


def test_registry_repositories_are_immutable_and_emptied_on_delete(templates) -> None:
    repos = resources(templates["Registry"], "AWS::ECR::Repository")
    by_name = {r["Properties"]["RepositoryName"]: r["Properties"] for r in repos.values()}

    assert set(by_name) == {"tremor-api", "steward-api", "golden-db", "build-cache"}
    assert all(props["EmptyOnDelete"] for props in by_name.values())
    for name in ("tremor-api", "steward-api", "golden-db"):
        assert by_name[name]["ImageTagMutability"] == "IMMUTABLE_WITH_EXCLUSION"
        assert (
            '"tagPatternList":["sha-*"]' in by_name[name]["LifecyclePolicy"]["LifecyclePolicyText"]
        )


def test_dns_zone_is_swept_before_deletion(templates) -> None:
    dns = templates["Dns"]

    dns.has_resource_properties("AWS::Route53::HostedZone", {"Name": "prismatic.dev."})
    dns.resource_count_is("Custom::ZoneSweeper", 1)


def test_teardown_leaves_nothing_retained(templates) -> None:
    for name, template in templates.items():
        for logical_id, resource in template.to_json().get("Resources", {}).items():
            assert resource.get("DeletionPolicy", "Delete") == "Delete", f"{name}/{logical_id}"


def test_no_secrets_manager_secrets(templates) -> None:
    for template in templates.values():
        template.resource_count_is("AWS::SecretsManager::Secret", 0)


def test_every_lambda_runs_the_latest_runtime_and_logs_to_an_owned_group(templates) -> None:
    for name, template in templates.items():
        for logical_id, function in resources(template, "AWS::Lambda::Function").items():
            properties = function["Properties"]
            assert properties["Runtime"] in LATEST_RUNTIMES, f"{name}/{logical_id}"
            assert "LogGroup" in properties.get("LoggingConfig", {}), f"{name}/{logical_id}"


def statements_of(template: Template, role_prefix: str) -> list[dict]:
    return [
        statement
        for logical_id, role in resources(template, "AWS::IAM::Role").items()
        if logical_id.startswith(role_prefix)
        for policy in role["Properties"].get("Policies", [])
        for statement in policy["PolicyDocument"]["Statement"]
    ]


def change_record_conditions(template: Template, role_prefix: str) -> dict:
    return next(
        s["Condition"]
        for s in statements_of(template, role_prefix)
        if "route53:ChangeResourceRecordSets" in s["Action"]
    )


def test_cert_manager_may_only_change_acme_challenge_txt_records(templates) -> None:
    conditions = change_record_conditions(templates["Dns"], "CertManagerRole")

    assert conditions == {
        "ForAllValues:StringLike": {
            "route53:ChangeResourceRecordSetsNormalizedRecordNames": ["_acme-challenge.*"]
        },
        "ForAllValues:StringEquals": {"route53:ChangeResourceRecordSetsRecordTypes": ["TXT"]},
    }


def test_external_dns_may_only_change_the_wildcard_records(templates) -> None:
    conditions = change_record_conditions(templates["Dns"], "ExternalDnsRole")

    assert conditions == {
        "ForAllValues:StringLike": {
            "route53:ChangeResourceRecordSetsNormalizedRecordNames": [
                "*preview.prismatic.dev",
                "*dev.prismatic.dev",
            ]
        },
        "ForAllValues:StringEquals": {
            "route53:ChangeResourceRecordSetsRecordTypes": ["A", "AAAA", "TXT"]
        },
    }


def test_pod_identity_roles_trust_only_this_cluster(templates) -> None:
    trust = templates["Dns"].find_resources("AWS::IAM::Role")
    pod_roles = [
        role["Properties"]["AssumeRolePolicyDocument"]["Statement"][0]
        for role in trust.values()
        if "pods.eks.amazonaws.com" in json.dumps(role["Properties"]["AssumeRolePolicyDocument"])
    ]

    assert len(pod_roles) == 2
    for statement in pod_roles:
        assert statement["Action"] == ["sts:AssumeRole", "sts:TagSession"]
        assert set(statement["Condition"]) == {"StringEquals", "ArnEquals"}
        assert "aws:SourceAccount" in statement["Condition"]["StringEquals"]
        assert ":cluster/caldera" in json.dumps(statement["Condition"]["ArnEquals"])


def test_golden_db_keeps_only_the_last_ten_dataset_versions(templates) -> None:
    repos = resources(templates["Registry"], "AWS::ECR::Repository").values()
    golden = next(r for r in repos if r["Properties"]["RepositoryName"] == "golden-db")
    rules = json.loads(golden["Properties"]["LifecyclePolicy"]["LifecyclePolicyText"])["rules"]

    assert {
        "tagStatus": "tagged",
        "tagPatternList": ["*"],
        "countType": "imageCountMoreThan",
        "countNumber": 10,
    } in [rule["selection"] for rule in rules]


def test_budget_always_notifies(templates) -> None:
    budget = next(iter(resources(templates["Network"], "AWS::Budgets::Budget").values()))
    notifications = budget["Properties"]["NotificationsWithSubscribers"]

    assert {n["Notification"]["NotificationType"] for n in notifications} == {
        "ACTUAL",
        "FORECASTED",
    }
    assert notifications[0]["Subscribers"] == [
        {"Address": "platform@prismatic.dev", "SubscriptionType": "EMAIL"}
    ]


def test_ecr_and_sts_traffic_stays_inside_the_vpc(templates) -> None:
    endpoints = resources(templates["Network"], "AWS::EC2::VPCEndpoint").values()
    interface_services = sorted(
        json.dumps(e["Properties"]["ServiceName"]).split(".")[-1].strip('"]}')
        for e in endpoints
        if e["Properties"].get("VpcEndpointType") == "Interface"
    )

    assert interface_services == ["api", "dkr", "sts"]


def test_cleanup_functions_report_failed_async_reinvocations_to_themselves(templates) -> None:
    for name in ("Network", "Dns"):
        config = next(iter(resources(templates[name], "AWS::Lambda::EventInvokeConfig").values()))
        properties = config["Properties"]
        assert properties["MaximumRetryAttempts"] == 2
        destination = json.dumps(properties["DestinationConfig"]["OnFailure"]["Destination"])
        assert "sweeper" in destination


def test_cleanup_roles_can_check_their_own_stack_status(templates) -> None:
    statements = statements_of(templates["Network"], "Sweeper")

    assert {
        "Action": "cloudformation:DescribeStacks",
        "Effect": "Allow",
        "Resource": {"Ref": "AWS::StackId"},
    } in statements
