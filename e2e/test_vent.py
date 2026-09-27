import httpx
import pytest


@pytest.mark.parametrize("client_name", ["tremor", "steward"])
def test_service_is_ready(client_name: str, request: pytest.FixtureRequest) -> None:
    client: httpx.Client = request.getfixturevalue(client_name)

    assert client.get("/readyz").status_code == 200


def test_alert_round_trip(tremor: httpx.Client) -> None:
    created = tremor.post(
        "/alerts", json={"station": "E2E-01", "severity": "info", "message": "e2e"}
    )

    assert created.status_code == 201
    assert tremor.get(f"/alerts/{created.json()['id']}").status_code == 200


def test_work_order_round_trip(steward: httpx.Client) -> None:
    created = steward.post("/work-orders", json={"site": "e2e-site", "title": "e2e"})

    assert created.status_code == 201
    assert steward.get(f"/work-orders/{created.json()['id']}").status_code == 200
