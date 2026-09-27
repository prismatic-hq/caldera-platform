"""Raises an AWS service quota to a desired value and waits until it is applied.

Create and Update request the increase once and poll until the quota reaches the desired value,
waiting on any request already open instead of filing another. Delete succeeds without changes,
because quota increases are never reverted.
"""

import logging
import time
from typing import Any

import boto3

import cfn

logger = logging.getLogger()
logger.setLevel(logging.INFO)

OPEN_STATUSES = {"PENDING", "CASE_OPENED"}
FAILED_STATUSES = {"DENIED", "NOT_APPROVED", "INVALID_REQUEST"}


def pending_quota(properties: dict, client: Any = None) -> list[str]:
    client = client or boto3.client("service-quotas")
    key = {"ServiceCode": properties["ServiceCode"], "QuotaCode": properties["QuotaCode"]}
    name = f"{key['ServiceCode']}/{key['QuotaCode']}"
    desired = float(properties["DesiredValue"])
    current = client.get_service_quota(**key)["Quota"]["Value"]
    if current >= desired:
        return []
    history = client.list_requested_service_quota_change_history_by_quota(**key)
    requests = sorted(history["RequestedQuotas"], key=lambda r: r["Created"], reverse=True)
    open_request = next((r for r in requests if r["Status"] in OPEN_STATUSES), None)
    if open_request:
        return [
            f"{name} at {current:g}, waiting on request {open_request['Id']} "
            f"({open_request['Status']}) for {open_request['DesiredValue']:g}"
        ]
    latest = requests[0] if requests else None
    if latest and latest["DesiredValue"] >= desired and latest["Status"] in FAILED_STATUSES:
        raise RuntimeError(
            f"{name} request {latest['Id']} was {latest['Status']}; resolve it in the "
            f"Service Quotas console, then redeploy to request {desired:g} again"
        )
    client.request_service_quota_increase(**key, DesiredValue=desired)
    return [f"requested {name} {current:g} -> {desired:g}"]


def handler(event: dict, context: Any) -> None:
    logger.info("%s %s", event["RequestType"], event["LogicalResourceId"])
    try:
        if event["RequestType"] == "Delete":
            cfn.respond(event, "SUCCESS")
        else:
            cfn.drain(event, context, lambda properties: pending_quota(properties), time.time)
    except Exception as error:
        logger.exception("quota handler failed")
        cfn.respond(event, "FAILED", f"{type(error).__name__}: {error}")
