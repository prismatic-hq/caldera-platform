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


def chart_names(template: Template) -> set[str]:
    return {
        props["Properties"]["Chart"]
        for props in resources(template, "Custom::AWSCDK-EKS-HelmChart").values()
    }


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

    assert set(by_name) == {"tremor-api", "steward-api", "golden-db", "e2e", "build-cache"}
    assert all(props["EmptyOnDelete"] for props in by_name.values())
    for name in ("tremor-api", "steward-api", "golden-db", "e2e"):
        assert by_name[name]["ImageTagMutability"] == "IMMUTABLE_WITH_EXCLUSION"
        assert (
            '"tagPatternList":["sha-*"]' in by_name[name]["LifecyclePolicy"]["LifecyclePolicyText"]
        )


def test_dns_zone_is_swept_before_deletion(templates) -> None:
    dns = templates["Dns"]

    dns.has_resource_properties("AWS::Route53::HostedZone", {"Name": "example.com."})
    dns.resource_count_is("Custom::ZoneSweeper", 1)


def test_ci_roles_trust_only_push_events_of_one_repository(templates) -> None:
    roles = resources(templates["CiAccess"], "AWS::IAM::Role")
    subjects = sorted(
        statement["Condition"]["StringLike"]["token.actions.githubusercontent.com:sub"]
        for role in roles.values()
        for statement in role["Properties"]["AssumeRolePolicyDocument"]["Statement"]
        if "Federated" in statement["Principal"]
    )

    assert subjects == [
        "repo:prismatic-hq/caldera-platform:ref:refs/heads/main",
        "repo:prismatic-hq/steward-api:ref:refs/heads/*",
        "repo:prismatic-hq/tremor-api:ref:refs/heads/*",
    ]


def test_runners_get_ecr_push_through_pod_identity(templates) -> None:
    templates["CiAccess"].has_resource_properties(
        "AWS::EKS::PodIdentityAssociation",
        {"Namespace": "arc-runners", "ServiceAccount": "arc-runner"},
    )


def test_runners_may_push_the_e2e_image(templates) -> None:
    pushes = [
        json.dumps(statement["Resource"])
        for policy in resources(templates["CiAccess"], "AWS::IAM::Policy").values()
        for statement in policy["Properties"]["PolicyDocument"]["Statement"]
        if "ecr:PutImage" in statement["Action"]
    ]

    assert any("GetAttE2e" in resource for resource in pushes)


def test_addons_install_every_section_4_chart(templates) -> None:
    assert chart_names(templates["Addons"]) == {
        "aws-load-balancer-controller",
        "cert-manager",
        "external-dns",
        "gateway-helm",
        "gha-runner-scale-set",
        "gha-runner-scale-set-controller",
        "keda",
        "metrics-server",
        "external-secrets",
    }
    assert chart_names(templates["Cluster"]) == {"cilium", "karpenter"}


def test_drainer_depends_on_every_addon(templates) -> None:
    addons = templates["Addons"]
    drainer = next(iter(resources(addons, "Custom::Drainer").values()))
    installed = set(resources(addons, "Custom::AWSCDK-EKS-HelmChart")) | set(
        resources(addons, "Custom::AWSCDK-EKS-KubernetesResource")
    )

    assert installed <= set(drainer["DependsOn"])


def test_no_preview_namespace_gets_cloud_permissions(templates) -> None:
    namespaces = [
        association["Properties"]["Namespace"]
        for template in templates.values()
        for association in resources(template, "AWS::EKS::PodIdentityAssociation").values()
    ]

    assert namespaces
    assert not [ns for ns in namespaces if str(ns).startswith("preview-")]


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
                "*preview.example.com",
                "*dev.example.com",
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
        {"Address": "platform@example.com", "SubscriptionType": "EMAIL"}
    ]


def test_ecr_and_sts_traffic_stays_inside_the_vpc(templates) -> None:
    endpoints = resources(templates["Network"], "AWS::EC2::VPCEndpoint").values()
    interface_services = sorted(
        json.dumps(e["Properties"]["ServiceName"]).split(".")[-1].strip('"]}')
        for e in endpoints
        if e["Properties"].get("VpcEndpointType") == "Interface"
    )

    assert interface_services == ["api", "dkr", "sts"]


def test_cleanup_functions_report_failed_async_reinvocations_to_a_separate_reporter(
    templates,
) -> None:
    for name in ("Network", "Dns"):
        config = next(iter(resources(templates[name], "AWS::Lambda::EventInvokeConfig").values()))
        properties = config["Properties"]
        assert properties["MaximumRetryAttempts"] == 2
        destination = json.dumps(properties["DestinationConfig"]["OnFailure"]["Destination"])
        assert "sweeper-failure-reporter" in destination
        reporters = [
            f["Properties"]
            for f in resources(templates[name], "AWS::Lambda::Function").values()
            if f["Properties"]["Handler"] == "cfn.report_failure"
        ]
        assert len(reporters) == 1
        assert "VpcConfig" not in reporters[0]


def test_cleanup_roles_can_check_their_own_stack_status(templates) -> None:
    statements = statements_of(templates["Network"], "Sweeper")

    assert {
        "Action": "cloudformation:DescribeStacks",
        "Effect": "Allow",
        "Resource": {"Ref": "AWS::StackId"},
    } in statements


def grants_network_interfaces(document: dict) -> bool:
    return any(
        "ec2:CreateNetworkInterface" in statement["Action"] for statement in document["Statement"]
    )


def test_vpc_lambdas_are_created_after_their_network_interface_permissions(templates) -> None:
    for name, template in templates.items():
        roles = resources(template, "AWS::IAM::Role")
        policies = resources(template, "AWS::IAM::Policy")
        for logical_id, function in resources(template, "AWS::Lambda::Function").items():
            properties = function["Properties"]
            if "VpcConfig" not in properties:
                continue
            role_id = properties["Role"]["Fn::GetAtt"][0]
            inline = roles[role_id]["Properties"].get("Policies", [])
            if any(grants_network_interfaces(p["PolicyDocument"]) for p in inline):
                continue
            depends_on = function.get("DependsOn", [])
            assert any(
                policy_id in depends_on
                and {"Ref": role_id} in policy["Properties"]["Roles"]
                and grants_network_interfaces(policy["Properties"]["PolicyDocument"])
                for policy_id, policy in policies.items()
            ), f"{name}/{logical_id}"


LAMBDA_VPC_ACCESS_ACTIONS = [
    "ec2:CreateNetworkInterface",
    "ec2:DescribeNetworkInterfaces",
    "ec2:DescribeSubnets",
    "ec2:DeleteNetworkInterface",
    "ec2:AssignPrivateIpAddresses",
    "ec2:UnassignPrivateIpAddresses",
]


def role_statements(template: Template, role_id: str) -> list[dict]:
    inline = resources(template, "AWS::IAM::Role")[role_id]["Properties"].get("Policies", [])
    attached = [
        policy["Properties"]
        for policy in resources(template, "AWS::IAM::Policy").values()
        if {"Ref": role_id} in policy["Properties"]["Roles"]
    ]
    return [
        statement
        for policy in inline + attached
        for statement in policy["PolicyDocument"]["Statement"]
    ]


def covers_lambda_vpc_access(statement: dict, effect: str, condition: dict | None) -> bool:
    return (
        statement["Effect"] == effect
        and statement["Resource"] == "*"
        and statement.get("Condition") == condition
        and set(LAMBDA_VPC_ACCESS_ACTIONS) <= set(statement["Action"])
    )


def test_vpc_lambdas_get_the_access_lambda_requires_but_their_code_does_not(templates) -> None:
    vpc_functions = [
        (f"{name}/{logical_id}", template, function["Properties"]["Role"]["Fn::GetAtt"][0])
        for name, template in templates.items()
        for logical_id, function in resources(template, "AWS::Lambda::Function").items()
        if "VpcConfig" in function["Properties"]
    ]
    assert vpc_functions
    for function, template, role_id in vpc_functions:
        statements = role_statements(template, role_id)
        assert any(covers_lambda_vpc_access(s, "Allow", None) for s in statements), function
        assert any(
            covers_lambda_vpc_access(s, "Deny", {"Null": {"lambda:SourceFunctionArn": "false"}})
            for s in statements
        ), function


def runner_policy_statements(template: Template) -> list[dict]:
    return [
        statement
        for policy in resources(template, "AWS::IAM::Policy").values()
        if any(
            role.get("Ref", "").startswith("RunnerRole")
            for role in policy["Properties"].get("Roles", [])
        )
        for statement in policy["Properties"]["PolicyDocument"]["Statement"]
    ]


def test_runners_read_and_publish_the_dataset_version_and_read_the_preview_domain(
    templates,
) -> None:
    ssm = [
        statement
        for statement in runner_policy_statements(templates["CiAccess"])
        if any(str(action).startswith("ssm:") for action in _as_list(statement["Action"]))
    ]
    resources_by_action: dict[str, str] = {}
    for statement in ssm:
        for action in _as_list(statement["Action"]):
            resources_by_action[action] = resources_by_action.get(action, "") + json.dumps(
                statement["Resource"]
            )

    assert "golden-db/dataset-version" in resources_by_action["ssm:GetParameter"]
    assert "PreviewDomain" in resources_by_action["ssm:GetParameter"]
    assert "golden-db/dataset-version" in resources_by_action["ssm:PutParameter"]
    assert "PreviewDomain" not in resources_by_action["ssm:PutParameter"]


def test_preview_domain_is_published_for_the_cli(templates) -> None:
    templates["CiAccess"].has_resource_properties(
        "AWS::SSM::Parameter",
        {"Name": "/prismatic/preview/domain", "Type": "String", "Value": "preview.example.com"},
    )


def _as_list(value: object) -> list:
    return value if isinstance(value, list) else [value]
