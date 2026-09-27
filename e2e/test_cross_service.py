import uuid

import httpx
import pytest


def _alert(tremor: httpx.Client, severity: str) -> dict:
    station = f"E2E-{uuid.uuid4().hex[:8]}"
    response = tremor.post(
        "/alerts", json={"station": station, "severity": severity, "message": "e2e cross-service"}
    )
    assert response.status_code == 201, response.text
    return response.json()


def _work_orders_for(steward: httpx.Client, alert_id: str) -> list[dict]:
    response = steward.get("/work-orders", params={"source_alert_id": alert_id})
    assert response.status_code == 200, response.text
    return response.json()


def test_critical_alert_opens_a_steward_work_order(
    tremor: httpx.Client, steward: httpx.Client
) -> None:
    alert = _alert(tremor, "critical")

    [work_order] = _work_orders_for(steward, alert["id"])

    assert work_order["source_alert_id"] == alert["id"]
    assert work_order["site"] == alert["station"]
    assert alert["work_order_id"] == work_order["id"]
    assert tremor.get(f"/alerts/{alert['id']}").json()["work_order_id"] == work_order["id"]


@pytest.mark.parametrize("severity", ["info", "warning"])
def test_non_critical_alert_opens_no_work_order(
    severity: str, tremor: httpx.Client, steward: httpx.Client
) -> None:
    alert = _alert(tremor, severity)

    assert alert["work_order_id"] is None
    assert _work_orders_for(steward, alert["id"]) == []
