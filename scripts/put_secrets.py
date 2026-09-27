"""Write the GitHub App credentials the ARC runners need to SSM Parameter Store.

CloudFormation cannot create SecureString parameters, and the network sweeper deletes
everything under /prismatic/ on destroy, so run this after every fresh deploy.
"""

import argparse
import sys
from pathlib import Path

import boto3

from caldera.config import github_app_parameter


def _validated(app_id: str, installation_id: str, private_key: str) -> dict[str, str]:
    if not app_id.isdigit():
        raise ValueError(f"app id must be numeric, got {app_id!r}")
    if not installation_id.isdigit():
        raise ValueError(f"installation id must be numeric, got {installation_id!r}")
    if "-----BEGIN" not in private_key or "PRIVATE KEY-----" not in private_key:
        raise ValueError("private key is not a PEM private key; pass the .pem GitHub generated")
    return {
        "github_app_id": app_id,
        "github_app_installation_id": installation_id,
        "github_app_private_key": private_key,
    }


def put_github_app(ssm, *, app_id: str, installation_id: str, private_key: str) -> list[str]:
    values = _validated(app_id, installation_id, private_key)
    written = []
    for key, value in values.items():
        name = github_app_parameter(key)
        ssm.put_parameter(Name=name, Value=value, Type="SecureString", Overwrite=True)
        written.append(name)
    return written


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--app-id", required=True)
    parser.add_argument("--installation-id", required=True)
    parser.add_argument("--private-key-file", required=True, type=Path)
    parser.add_argument("--region")
    args = parser.parse_args(argv)
    if not args.private_key_file.is_file():
        print(f"private key file not found: {args.private_key_file}", file=sys.stderr)
        return 2
    try:
        written = put_github_app(
            boto3.Session(region_name=args.region).client("ssm"),
            app_id=args.app_id.strip(),
            installation_id=args.installation_id.strip(),
            private_key=args.private_key_file.read_text(),
        )
    except ValueError as error:
        print(f"invalid GitHub App credentials: {error}", file=sys.stderr)
        return 2
    for name in written:
        print(f"wrote {name}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
