import os

import httpx
import pytest

BASE_URL_TEMPLATE = os.getenv("VENT_BASE_URL", "")


@pytest.fixture(autouse=True)
def require_vent() -> None:
    if not BASE_URL_TEMPLATE:
        pytest.skip("VENT_BASE_URL is not set")


def service_url(service: str) -> str:
    return BASE_URL_TEMPLATE.format(service=service)


@pytest.fixture
def tremor() -> httpx.Client:
    with httpx.Client(base_url=service_url("tremor"), timeout=10) as client:
        yield client


@pytest.fixture
def steward() -> httpx.Client:
    with httpx.Client(base_url=service_url("steward"), timeout=10) as client:
        yield client
