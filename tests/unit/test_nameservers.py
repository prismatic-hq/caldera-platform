from collections.abc import Iterator
from pathlib import Path

import boto3
import pytest
from botocore.stub import Stubber

from scripts.nameservers import domain_for, main, nameservers

NAMESERVERS = ["ns-1.awsdns-01.org", "ns-2.awsdns-02.co.uk", "ns-3.awsdns-03.com"]


@pytest.fixture
def route53() -> Iterator[tuple[object, Stubber]]:
    client = boto3.Session(
        region_name="us-east-1", aws_access_key_id="test", aws_secret_access_key="test"
    ).client("route53")
    with Stubber(client) as stubber:
        yield client, stubber
        stubber.assert_no_pending_responses()


def zone(zone_id: str, name: str, *, private: bool = False) -> dict[str, object]:
    return {
        "Id": f"/hostedzone/{zone_id}",
        "Name": name,
        "CallerReference": zone_id,
        "Config": {"PrivateZone": private},
    }


def expect_zones(stubber: Stubber, domain: str, zones: list[dict[str, object]]) -> None:
    stubber.add_response(
        "list_hosted_zones_by_name",
        {"HostedZones": zones, "IsTruncated": False, "MaxItems": "100"},
        {"DNSName": domain, "MaxItems": "100"},
    )


def expect_delegation(stubber: Stubber, zone_id: str, name: str) -> None:
    stubber.add_response(
        "get_hosted_zone",
        {"HostedZone": zone(zone_id, name), "DelegationSet": {"NameServers": NAMESERVERS}},
        {"Id": f"/hostedzone/{zone_id}"},
    )


def test_returns_the_public_zone_nameservers(route53) -> None:
    client, stubber = route53
    expect_zones(
        stubber,
        "example.com",
        [
            zone("ZPRIVATE", "example.com.", private=True),
            zone("ZPUBLIC", "example.com."),
            zone("ZOTHER", "sub.example.com."),
        ],
    )
    expect_delegation(stubber, "ZPUBLIC", "example.com.")

    assert nameservers(client, "example.com") == NAMESERVERS


def test_fails_when_no_public_zone_exists(route53) -> None:
    client, stubber = route53
    expect_zones(stubber, "example.com", [zone("ZOTHER", "other.com.")])

    with pytest.raises(LookupError, match="no public hosted zone for example.com"):
        nameservers(client, "example.com")


def test_fails_when_several_public_zones_share_the_domain(route53) -> None:
    client, stubber = route53
    expect_zones(stubber, "example.com", [zone("ZA", "example.com."), zone("ZB", "example.com.")])

    with pytest.raises(LookupError, match="2 public hosted zones for example.com: ZA, ZB"):
        nameservers(client, "example.com")


def test_domain_prefers_the_flag_over_the_environment_file(tmp_path: Path) -> None:
    (tmp_path / "sandbox.yaml").write_text("domain: from-file.com\n")

    assert domain_for("flag.com", "sandbox.yaml", tmp_path) == "flag.com"
    assert domain_for(None, "sandbox.yaml", tmp_path) == "from-file.com"


def test_domain_is_required(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="domain is not set"):
        domain_for(None, None, tmp_path)


def test_cli_reports_a_missing_domain(monkeypatch, capsys) -> None:
    monkeypatch.delenv("ENV", raising=False)

    assert main([]) == 2
    assert "domain is not set" in capsys.readouterr().err
