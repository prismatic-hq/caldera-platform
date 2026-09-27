import httpx
import pytest

from preview_cli.github import API_URL, GitHub, RepoAccessError

REPO_PATH = b"/repos/prismatic-hq/steward-api"
BRANCH_PATH = REPO_PATH + b"/branches/feature%2Fx"


def github(branch_status: int, body: dict | None = None, repo_status: int = 200) -> GitHub:
    responses = {REPO_PATH: (repo_status, {}), BRANCH_PATH: (branch_status, body or {})}

    def handler(request: httpx.Request) -> httpx.Response:
        status, payload = responses[request.url.raw_path]
        return httpx.Response(status, json=payload)

    return GitHub(None, httpx.Client(base_url=API_URL, transport=httpx.MockTransport(handler)))


def test_branch_exists() -> None:
    assert github(200).branch_exists("steward-api", "feature/x") is True


def test_missing_branch_in_a_readable_repo_is_absent() -> None:
    assert github(404).branch_exists("steward-api", "feature/x") is False


@pytest.mark.parametrize("repo_status", [403, 404])
def test_unreadable_repo_fails_loudly_instead_of_falling_back_to_main(repo_status: int) -> None:
    client = github(404, repo_status=repo_status)

    with pytest.raises(RepoAccessError) as error:
        client.branch_exists("steward-api", "feature/x")

    message = str(error.value)
    assert f"cannot read prismatic-hq/steward-api (HTTP {repo_status})" in message
    assert "CALDERA_TOKEN" in message


def test_head_sha_checks_repo_access_first() -> None:
    with pytest.raises(RepoAccessError, match="CALDERA_TOKEN"):
        github(404, repo_status=404).head_sha("steward-api", "feature/x")


def test_branch_lookup_surfaces_other_errors() -> None:
    with pytest.raises(httpx.HTTPStatusError):
        github(401).branch_exists("steward-api", "feature/x")


def test_head_sha() -> None:
    assert github(200, {"commit": {"sha": "abc"}}).head_sha("steward-api", "feature/x") == "abc"


def test_head_sha_of_missing_branch_fails_with_context() -> None:
    with pytest.raises(LookupError, match="feature/x"):
        github(404).head_sha("steward-api", "feature/x")
