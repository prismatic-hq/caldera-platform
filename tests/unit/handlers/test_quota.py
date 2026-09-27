from datetime import datetime

import boto3
import pytest
import quota
from botocore.stub import Stubber

import cfn

PROPERTIES = {"ServiceCode": "lambda", "QuotaCode": "L-B99A9384", "DesiredValue": "50"}
QUOTA = {"ServiceCode": "lambda", "QuotaCode": "L-B99A9384"}


@pytest.fixture
def service_quotas():
    client = boto3.Session(
        region_name="us-east-2", aws_access_key_id="test", aws_secret_access_key="test"
    ).client("service-quotas")
    with Stubber(client) as stubber:
        yield client, stubber
        stubber.assert_no_pending_responses()


def stub_current(stubber: Stubber, value: float) -> None:
    stubber.add_response("get_service_quota", {"Quota": {"Value": value}}, QUOTA)


def stub_history(stubber: Stubber, *requests: dict) -> None:
    stubber.add_response(
        "list_requested_service_quota_change_history_by_quota",
        {"RequestedQuotas": list(requests)},
        QUOTA,
    )


def request(request_id: str, status: str, desired: float, day: int) -> dict:
    return {
        "Id": request_id,
        "Status": status,
        "DesiredValue": desired,
        "Created": datetime(2026, 9, day),
    }


def test_quota_already_at_desired_value_is_done(service_quotas) -> None:
    client, stubber = service_quotas
    stub_current(stubber, 50.0)

    assert quota.pending_quota(PROPERTIES, client) == []


def test_open_request_is_waited_on_without_a_new_request(service_quotas) -> None:
    client, stubber = service_quotas
    stub_current(stubber, 10.0)
    stub_history(
        stubber,
        request("old", "APPROVED", 20.0, 1),
        request("abc", "PENDING", 1000.0, 27),
    )

    assert quota.pending_quota(PROPERTIES, client) == [
        "lambda/L-B99A9384 at 10, waiting on request abc (PENDING) for 1000"
    ]


def test_missing_quota_is_requested(service_quotas) -> None:
    client, stubber = service_quotas
    stub_current(stubber, 10.0)
    stub_history(stubber)
    stubber.add_response(
        "request_service_quota_increase",
        {"RequestedQuota": {"Id": "new", "Status": "PENDING"}},
        {**QUOTA, "DesiredValue": 50.0},
    )

    assert quota.pending_quota(PROPERTIES, client) == ["requested lambda/L-B99A9384 10 -> 50"]


def test_denied_latest_request_fails_with_an_actionable_error(service_quotas) -> None:
    client, stubber = service_quotas
    stub_current(stubber, 10.0)
    stub_history(stubber, request("abc", "DENIED", 50.0, 27), request("old", "APPROVED", 5.0, 1))

    with pytest.raises(RuntimeError, match="lambda/L-B99A9384 request abc was DENIED"):
        quota.pending_quota(PROPERTIES, client)


def test_smaller_denied_request_does_not_block_a_new_one(service_quotas) -> None:
    client, stubber = service_quotas
    stub_current(stubber, 10.0)
    stub_history(stubber, request("abc", "DENIED", 20.0, 27))
    stubber.add_response(
        "request_service_quota_increase",
        {"RequestedQuota": {"Id": "new", "Status": "PENDING"}},
        {**QUOTA, "DesiredValue": 50.0},
    )

    assert quota.pending_quota(PROPERTIES, client) == ["requested lambda/L-B99A9384 10 -> 50"]


def cfn_event(request_type: str) -> dict:
    return {
        "RequestType": request_type,
        "LogicalResourceId": "LambdaConcurrency",
        "ResourceProperties": {**PROPERTIES, "TimeoutMinutes": "55"},
    }


def test_delete_succeeds_without_touching_the_quota(monkeypatch) -> None:
    responses = []
    monkeypatch.setattr(cfn, "respond", lambda e, status, reason="": responses.append(status))
    monkeypatch.setattr(quota, "pending_quota", lambda p: pytest.fail("quota checked"))

    quota.handler(cfn_event("Delete"), None)

    assert responses == ["SUCCESS"]


def test_create_polls_until_the_quota_is_applied(monkeypatch) -> None:
    responses = []
    passes = iter([["requested"], []])
    monkeypatch.setattr(cfn, "respond", lambda e, status, reason="": responses.append(status))
    monkeypatch.setattr(cfn.time, "sleep", lambda seconds: None)
    monkeypatch.setattr(quota, "pending_quota", lambda p: next(passes))

    class Context:
        def get_remaining_time_in_millis(self) -> int:
            return 900_000

    quota.handler(cfn_event("Create"), Context())

    assert responses == ["SUCCESS"]


def test_handler_errors_are_reported_as_failed(monkeypatch) -> None:
    responses = []
    monkeypatch.setattr(
        cfn, "respond", lambda e, status, reason="": responses.append((status, reason))
    )

    def denied(properties: dict) -> list[str]:
        raise RuntimeError("request abc was DENIED")

    monkeypatch.setattr(quota, "pending_quota", denied)

    quota.handler(cfn_event("Update"), None)

    assert responses == [("FAILED", "RuntimeError: request abc was DENIED")]
