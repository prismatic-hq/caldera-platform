"""Empties the hosted zone of records other than its apex SOA and NS, so the zone can be deleted."""

from typing import Any

import boto3

import cfn

BATCH_SIZE = 100


def removable_records(route53: Any, zone_id: str) -> list[dict]:
    apex = route53.get_hosted_zone(Id=zone_id)["HostedZone"]["Name"]
    paginator = route53.get_paginator("list_resource_record_sets")
    return [
        record
        for page in paginator.paginate(HostedZoneId=zone_id)
        for record in page["ResourceRecordSets"]
        if not (record["Name"] == apex and record["Type"] in ("SOA", "NS"))
    ]


def cleanup(properties: dict[str, Any], route53: Any = None) -> list[str]:
    route53 = route53 or boto3.client("route53")
    zone_id = properties["HostedZoneId"]
    records = removable_records(route53, zone_id)
    for start in range(0, len(records), BATCH_SIZE):
        changes = [
            {"Action": "DELETE", "ResourceRecordSet": record}
            for record in records[start : start + BATCH_SIZE]
        ]
        route53.change_resource_record_sets(HostedZoneId=zone_id, ChangeBatch={"Changes": changes})
    return [f"{r['Type']} {r['Name']}" for r in removable_records(route53, zone_id)]


def handler(event: dict, context: Any) -> None:
    cfn.run(event, context, cleanup)
