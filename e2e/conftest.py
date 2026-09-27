import os
from collections.abc import Iterator

import httpx
import pytest

BASE_URL_TEMPLATE = os.getenv("PREVIEW_BASE_URL", "")


def service_url(service: str) -> str:
    """`<SERVICE>_URL` set by the services chart in-cluster, else PREVIEW_BASE_URL."""
    explicit = os.getenv(f"{service.upper().replace('-', '_')}_URL", "")
    return explicit or BASE_URL_TEMPLATE.format(service=service)


@pytest.fixture
def client_for() -> Iterator:
    clients: list[httpx.Client] = []

    def connect(service: str) -> httpx.Client:
        url = service_url(service)
        if not url:
            pytest.skip(f"set {service.upper()}_URL or PREVIEW_BASE_URL to run against a preview")
        clients.append(httpx.Client(base_url=url, timeout=10))
        return clients[-1]

    yield connect
    for client in clients:
        client.close()


@pytest.fixture
def tremor(client_for) -> httpx.Client:
    return client_for("tremor")


@pytest.fixture
def steward(client_for) -> httpx.Client:
    return client_for("steward")
