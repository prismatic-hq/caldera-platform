"""CloudFormation custom resource runtime shared by the drainer and sweeper Lambdas.

Create and Update succeed immediately. Delete calls ``cleanup`` until it reports nothing left,
re-invoking the Lambda asynchronously when its own time runs low, and fails with the names of
the leftovers once the deadline passes.
"""

import json
import logging
import time
import urllib.request
from collections.abc import Callable
from typing import Any

import boto3

logger = logging.getLogger()
logger.setLevel(logging.INFO)

Cleanup = Callable[[dict[str, Any]], list[str]]

POLL_SECONDS = 15
RESERVED_MILLIS = 90_000
STARTED_AT = "CalderaStartedAt"


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


def drain(event: dict, context: Any, cleanup: Cleanup, now: Callable[[], float]) -> None:
    properties = event.get("ResourceProperties", {})
    started_at = float(event.setdefault(STARTED_AT, now()))
    deadline = started_at + deadline_seconds(properties)
    while True:
        remaining = cleanup(properties)
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


def run(event: dict, context: Any, cleanup: Cleanup, now: Callable[[], float] = time.time) -> None:
    logger.info("%s %s", event["RequestType"], event["LogicalResourceId"])
    try:
        if event["RequestType"] == "Delete":
            drain(event, context, cleanup, now)
        else:
            respond(event, "SUCCESS")
    except Exception as error:
        logger.exception("custom resource handler failed")
        respond(event, "FAILED", f"{type(error).__name__}: {error}")
