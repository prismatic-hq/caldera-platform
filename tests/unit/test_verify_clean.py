from collections.abc import Iterator

import boto3
import pytest
from botocore.stub import Stubber

from scripts.verify_clean import is_platform_tag, leftovers, report

CLUSTER = "caldera"
ARN = "arn:aws:elasticloadbalancing:us-east-1:123456789012:loadbalancer/net/gw/abc"


@pytest.fixture
def clients() -> Iterator[dict[str, tuple[object, Stubber]]]:
    session = boto3.Session(
        region_name="us-east-1", aws_access_key_id="test", aws_secret_access_key="test"
    )
    stubbed = {}
    for name in ("resourcegroupstaggingapi", "logs", "eks"):
        client = session.client(name)
        stubber = Stubber(client)
        stubber.activate()
        stubbed[name] = (client, stubber)
    yield stubbed
    for _, stubber in stubbed.values():
        stubber.assert_no_pending_responses()


def stub_account(stubbed: dict, resources: list[dict], log_groups: list[str], clusters: list[str]):
    stubbed["resourcegroupstaggingapi"][1].add_response(
        "get_resources", {"ResourceTagMappingList": resources}
    )
    logs = stubbed["logs"][1]
    logs.add_response(
        "describe_log_groups", {"logGroups": [{"logGroupName": g} for g in log_groups]}
    )
    logs.add_response("describe_log_groups", {"logGroups": []})
    stubbed["eks"][1].add_response("list_clusters", {"clusters": clusters})


def run(stubbed: dict) -> list[str]:
    return leftovers(*(stubbed[n][0] for n in ("resourcegroupstaggingapi", "logs", "eks")), CLUSTER)


@pytest.mark.parametrize(
    ("key", "value", "expected"),
    [
        ("prismatic:stack", "network", True),
        ("kubernetes.io/cluster/caldera", "owned", True),
        ("karpenter.sh/discovery", "caldera", True),
        ("elbv2.k8s.aws/cluster", "caldera", True),
        ("elbv2.k8s.aws/cluster", "other", False),
        ("kubernetes.io/cluster/other", "owned", False),
        ("team", "prismatic", False),
    ],
)
def test_is_platform_tag(key: str, value: str, expected: bool) -> None:
    assert is_platform_tag(key, value, CLUSTER) is expected


def test_clean_account_reports_nothing(clients: dict) -> None:
    stub_account(
        clients,
        [
            {
                "ResourceARN": "arn:aws:ec2:us-east-1:1:vpc/vpc-1",
                "Tags": [{"Key": "team", "Value": "x"}],
            }
        ],
        [],
        ["someone-else"],
    )

    assert run(clients) == []


def test_leftovers_are_listed(clients: dict) -> None:
    stub_account(
        clients,
        [{"ResourceARN": ARN, "Tags": [{"Key": "elbv2.k8s.aws/cluster", "Value": CLUSTER}]}],
        ["/aws/eks/caldera/cluster"],
        [CLUSTER],
    )

    assert run(clients) == ["/aws/eks/caldera/cluster", ARN, "eks cluster caldera"]


def test_report_exit_code(capsys: pytest.CaptureFixture[str]) -> None:
    assert report([]) == 0
    assert report([ARN]) == 1
    assert f"leftover: {ARN}" in capsys.readouterr().out
