import aws_cdk as cdk
import pytest
from aws_cdk.assertions import Match, Template

from caldera.platform import build_platform

LATEST_RUNTIMES = {"python3.14"}


def synth(context: dict | None = None) -> dict[str, Template]:
    stacks = build_platform(cdk.App(context=context or {}))
    return {name: Template.from_stack(stack) for name, stack in stacks.items()}


@pytest.fixture(scope="module")
def templates() -> dict[str, Template]:
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


def test_nat_gateways_context_flag_adds_a_second_nat() -> None:
    synth({"natGateways": 2})["Network"].resource_count_is("AWS::EC2::NatGateway", 2)


def test_nat_gateways_context_flag_rejects_other_counts() -> None:
    with pytest.raises(ValueError, match="natGateways must be 1 or 2"):
        synth({"natGateways": 3})


def test_private_subnets_are_tagged_for_karpenter_discovery(templates) -> None:
    templates["Network"].has_resource_properties(
        "AWS::EC2::Subnet",
        {"Tags": Match.array_with([{"Key": "karpenter.sh/discovery", "Value": "caldera"}])},
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
