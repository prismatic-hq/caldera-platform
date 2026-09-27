import httpx
import pytest

from caldera_cli.github import API_URL, GitHub


def github(status: int, body: dict | None = None) -> GitHub:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.raw_path == b"/repos/prismatic-hq/steward-api/branches/feature%2Fx"
        return httpx.Response(status, json=body or {})

    return GitHub(None, httpx.Client(base_url=API_URL, transport=httpx.MockTransport(handler)))


def test_branch_exists() -> None:
    assert github(200).branch_exists("steward-api", "feature/x") is True
    assert github(404).branch_exists("steward-api", "feature/x") is False


def test_branch_lookup_surfaces_other_errors() -> None:
    with pytest.raises(httpx.HTTPStatusError):
        github(401).branch_exists("steward-api", "feature/x")


def test_head_sha() -> None:
    assert github(200, {"commit": {"sha": "abc"}}).head_sha("steward-api", "feature/x") == "abc"


def test_head_sha_of_missing_branch_fails_with_context() -> None:
    with pytest.raises(LookupError, match="feature/x"):
        github(404).head_sha("steward-api", "feature/x")
