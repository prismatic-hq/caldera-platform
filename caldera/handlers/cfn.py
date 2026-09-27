"""CloudFormation custom resource runtime shared by the drainer and sweeper Lambdas.

Create and Update succeed immediately. Delete calls ``cleanup`` until it reports nothing left,
re-invoking the Lambda asynchronously when its own time runs low, and fails with the names of
the leftovers once the deadline passes. Delete only cleans up while the whole stack is being
deleted, so removing or renaming the resource on a live stack never tears the platform down.
"""

import json
import logging
import time
import urllib.request
from collections.abc import Callable
from typing import Any

import boto3
from botocore.exceptions import ClientError

logger = logging.getLogger()
logger.setLevel(logging.INFO)

Cleanup = Callable[[dict[str, Any]], list[str]]

POLL_SECONDS = 15
RESERVED_MILLIS = 90_000
STARTED_AT = "CalderaStartedAt"
RETRYABLE_ERROR_CODES = {
    "InternalError",
    "InternalFailure",
    "PriorRequestNotComplete",
    "RequestLimitExceeded",
    "ServiceUnavailable",
    "Throttling",
    "ThrottlingException",
    "TooManyRequestsException",
}


def respond(event: dict, status: str, reason: str = "") -> None:
    body = json.dumps(
        {
            "Status": status,
            "Reason": reason[:3000] or status,
            "PhysicalResourceId": event.get("PhysicalResourceId") or event["LogicalResourceId"],
            "StackId": event["StackId"],
            "RequestId": event["RequestId"],
            "LogicalResourceId": event["LogicalResourceId"],
        }
    ).encode()
    request = urllib.request.Request(
        event["ResponseURL"],
        data=body,
        method="PUT",
        headers={"Content-Type": "", "Content-Length": str(len(body))},
    )
    with urllib.request.urlopen(request, timeout=30) as response:
        logger.info("responded %s to CloudFormation: HTTP %s", status, response.status)


def deadline_seconds(properties: dict) -> int:
    return int(properties.get("TimeoutMinutes", 20)) * 60


def reinvoke(event: dict, context: Any) -> None:
    boto3.client("lambda").invoke(
        FunctionName=context.invoked_function_arn,
        InvocationType="Event",
        Payload=json.dumps(event).encode(),
    )


def stack_is_deleting(event: dict) -> bool:
    stacks = boto3.client("cloudformation").describe_stacks(StackName=event["StackId"])["Stacks"]
    return stacks[0]["StackStatus"] == "DELETE_IN_PROGRESS"


def attempt(cleanup: Cleanup, properties: dict) -> list[str]:
    try:
        return cleanup(properties)
    except ClientError as error:
        code = error.response["Error"]["Code"]
        if code not in RETRYABLE_ERROR_CODES:
            raise
        return [f"retrying after {code}"]


def drain(event: dict, context: Any, cleanup: Cleanup, now: Callable[[], float]) -> None:
    properties = event.get("ResourceProperties", {})
    started_at = float(event.setdefault(STARTED_AT, now()))
    deadline = started_at + deadline_seconds(properties)
    while True:
        remaining = attempt(cleanup, properties)
        if not remaining:
            respond(event, "SUCCESS")
            return
        logger.info("waiting on %d resource(s): %s", len(remaining), ", ".join(remaining))
        if now() >= deadline:
            minutes = deadline_seconds(properties) // 60
            respond(event, "FAILED", f"still present after {minutes}m: {', '.join(remaining)}")
            return
        if context.get_remaining_time_in_millis() < RESERVED_MILLIS:
            reinvoke(event, context)
            return
        time.sleep(POLL_SECONDS)


def report_failure(event: dict, context: Any) -> None:
    condition = event.get("requestContext", {}).get("condition", "unknown")
    respond(event["requestPayload"], "FAILED", f"re-invocation failed: {condition}")


def run(
    event: dict,
    context: Any,
    cleanup: Cleanup,
    now: Callable[[], float] = time.time,
    deleting: Callable[[dict], bool] = stack_is_deleting,
) -> None:
    logger.info("%s %s", event["RequestType"], event["LogicalResourceId"])
    try:
        if event["RequestType"] != "Delete":
            respond(event, "SUCCESS")
        elif STARTED_AT not in event and not deleting(event):
            logger.info("stack is not being deleted; skipping cleanup")
            respond(event, "SUCCESS")
        else:
            drain(event, context, cleanup, now)
    except Exception as error:
        logger.exception("custom resource handler failed")
        respond(event, "FAILED", f"{type(error).__name__}: {error}")
