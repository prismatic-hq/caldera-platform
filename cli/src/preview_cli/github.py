from urllib.parse import quote

import httpx

API_URL = "https://api.github.com"
ORG = "prismatic-hq"
NO_ACCESS = (httpx.codes.FORBIDDEN, httpx.codes.NOT_FOUND)


class RepoAccessError(LookupError):
    pass


class GitHub:
    def __init__(self, token: str | None, client: httpx.Client | None = None) -> None:
        headers = {"Accept": "application/vnd.github+json"}
        if token:
            headers["Authorization"] = f"Bearer {token}"
        self._client = client or httpx.Client(base_url=API_URL, headers=headers, timeout=10)
        self._readable: set[str] = set()

    def _require_repo(self, repo: str) -> None:
        """A branch 404 only means "absent" once the token can read the repository."""
        if repo in self._readable:
            return
        response = self._client.get(f"/repos/{ORG}/{repo}")
        if response.status_code in NO_ACCESS:
            raise RepoAccessError(
                f"cannot read {ORG}/{repo} (HTTP {response.status_code}): set the CALDERA_TOKEN "
                f"secret in the calling repo to a token with contents:read on {ORG}/{repo}; "
                "the default GITHUB_TOKEN only reads its own repo"
            )
        response.raise_for_status()
        self._readable.add(repo)

    def _branch(self, repo: str, branch: str) -> httpx.Response:
        self._require_repo(repo)
        return self._client.get(f"/repos/{ORG}/{repo}/branches/{quote(branch, safe='')}")

    def branch_exists(self, repo: str, branch: str) -> bool:
        response = self._branch(repo, branch)
        if response.status_code == httpx.codes.NOT_FOUND:
            return False
        response.raise_for_status()
        return True

    def head_sha(self, repo: str, branch: str) -> str:
        response = self._branch(repo, branch)
        if response.status_code == httpx.codes.NOT_FOUND:
            raise LookupError(
                f"branch {branch!r} not found in {ORG}/{repo} (or the token cannot read the repo)"
            )
        response.raise_for_status()
        return response.json()["commit"]["sha"]
