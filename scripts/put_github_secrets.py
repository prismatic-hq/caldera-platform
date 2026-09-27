"""Write the GitHub App credentials and AWS region to each repo's Actions settings.

Private repositories on the GitHub Free plan cannot read organization secrets or variables, so
every repo that runs or calls the platform workflows needs its own copy.
"""

import argparse
import re
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

from caldera.config import SERVICES_FILE
from preview_cli.registry import ServiceRegistry

CLIENT_ID = re.compile(r"^Iv[0-9A-Za-z.]{8,}$")
REGION = re.compile(r"^[a-z]{2}(-[a-z]+)+-\d$")


@dataclass(frozen=True)
class GitHubSetting:
    kind: str
    name: str
    value: str

    @property
    def label(self) -> str:
        return f"{self.kind} {self.name}"


def settings_for(*, client_id: str, private_key: str, region: str) -> list[GitHubSetting]:
    if not CLIENT_ID.match(client_id):
        raise ValueError(
            f"client id must be the app's Client ID (Iv...), not the App ID; got {client_id!r}"
        )
    if "-----BEGIN" not in private_key or "PRIVATE KEY-----" not in private_key:
        raise ValueError("private key is not a PEM private key; pass the .pem GitHub generated")
    if not REGION.match(region):
        raise ValueError(f"region must look like us-east-2; got {region!r}")
    return [
        GitHubSetting("secret", "CALDERA_APP_CLIENT_ID", client_id),
        GitHubSetting("secret", "CALDERA_APP_PRIVATE_KEY", private_key),
        GitHubSetting("variable", "AWS_REGION", region),
    ]


def run_gh(args: list[str], value: str) -> None:
    subprocess.run(["gh", *args], input=value, text=True, check=True, capture_output=True)


def put_github_settings(gh, org: str, repos: list[str], settings: list[GitHubSetting]) -> list[str]:
    written = []
    for repo in repos:
        slug = f"{org}/{repo}"
        for setting in settings:
            target = f"{slug}: {setting.label}"
            try:
                gh([setting.kind, "set", setting.name, "--repo", slug], setting.value)
            except subprocess.CalledProcessError as error:
                raise RuntimeError(f"failed to set {target}: {error.stderr or error}") from error
            written.append(target)
    return written


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--client-id", required=True)
    parser.add_argument("--private-key-file", required=True, type=Path)
    parser.add_argument("--region", required=True)
    parser.add_argument("--org", default="prismatic-hq")
    parser.add_argument("--platform-repo", default="caldera-platform")
    args = parser.parse_args(argv)
    if not args.private_key_file.is_file():
        print(f"private key file not found: {args.private_key_file}", file=sys.stderr)
        return 2
    try:
        settings = settings_for(
            client_id=args.client_id.strip(),
            private_key=args.private_key_file.read_text(),
            region=args.region.strip(),
        )
    except ValueError as error:
        print(f"invalid input: {error}", file=sys.stderr)
        return 2
    services = ServiceRegistry.load(SERVICES_FILE).services
    repos = [args.platform_repo, *(service.repo for service in services)]
    try:
        written = put_github_settings(run_gh, args.org, repos, settings)
    except RuntimeError as error:
        print(error, file=sys.stderr)
        return 1
    for target in written:
        print(f"set {target}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
