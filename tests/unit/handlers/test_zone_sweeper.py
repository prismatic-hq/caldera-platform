import zone_sweeper

ZONE = "Z123"
APEX = "prismatic.dev."
SOA = {"Name": APEX, "Type": "SOA", "TTL": 900, "ResourceRecords": [{"Value": "soa"}]}
NS = {"Name": APEX, "Type": "NS", "TTL": 172800, "ResourceRecords": [{"Value": "ns"}]}
WILDCARD = {
    "Name": "\\052.preview.prismatic.dev.",
    "Type": "A",
    "AliasTarget": {"HostedZoneId": "Z26", "DNSName": "nlb.aws.", "EvaluateTargetHealth": False},
}


def stub_records(stub, records: list[dict]) -> None:
    stub.add_response(
        "get_hosted_zone",
        {
            "HostedZone": {"Id": ZONE, "Name": APEX, "CallerReference": "x"},
            "DelegationSet": {"NameServers": ["ns"]},
        },
        {"Id": ZONE},
    )
    stub.add_response(
        "list_resource_record_sets",
        {"ResourceRecordSets": records, "IsTruncated": False, "MaxItems": "300"},
        {"HostedZoneId": ZONE},
    )


def test_deletes_everything_except_the_apex_soa_and_ns(stubbed) -> None:
    route53, stub = stubbed["route53"]
    stub_records(stub, [SOA, NS, WILDCARD])
    stub.add_response(
        "change_resource_record_sets",
        {"ChangeInfo": {"Id": "c1", "Status": "PENDING", "SubmittedAt": "2026-09-27T00:00:00Z"}},
        {
            "HostedZoneId": ZONE,
            "ChangeBatch": {"Changes": [{"Action": "DELETE", "ResourceRecordSet": WILDCARD}]},
        },
    )
    stub_records(stub, [SOA, NS])

    assert zone_sweeper.cleanup({"HostedZoneId": ZONE}, route53) == []
