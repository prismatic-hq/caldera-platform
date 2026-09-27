"""Write the GitHub App credentials the ARC runners need to SSM Parameter Store.

CloudFormation cannot create SecureString parameters, and the network sweeper deletes
everything under /prismatic/ on destroy, so run this after every fresh deploy. The app's
app_id and installation_id fields and pem file are read from 1Password unless overridden.
"""

import argparse
import sys
from pathlib import Path

import boto3

from cdk.config import github_app_parameter
from scripts.onepassword import OP_ITEM, OP_VAULT, OpError, op_reference, read_op


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


def read_credentials(args: argparse.Namespace) -> dict[str, str]:
    def field(override: str | None, name: str) -> str:
        return override or read_op(op_reference(args.op_vault, args.op_item, name))

    app_id = field(args.app_id, "app_id").strip()
    installation_id = field(args.installation_id, "installation_id").strip()
    if args.private_key_file:
        if not args.private_key_file.is_file():
            raise FileNotFoundError(f"private key file not found: {args.private_key_file}")
        private_key = args.private_key_file.read_text()
    else:
        private_key = field(None, "pem")
    return {
        "app_id": app_id,
        "installation_id": installation_id,
        "private_key": private_key,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--op-vault", default=OP_VAULT)
    parser.add_argument("--op-item", default=OP_ITEM)
    parser.add_argument("--app-id", help="Override the 1Password app_id field")
    parser.add_argument("--installation-id", help="Override the 1Password installation_id field")
    parser.add_argument("--private-key-file", type=Path, help="Override the 1Password pem file")
    parser.add_argument("--region")
    args = parser.parse_args(argv)
    try:
        credentials = read_credentials(args)
        written = put_github_app(
            boto3.Session(region_name=args.region).client("ssm"), **credentials
        )
    except (OpError, FileNotFoundError) as error:
        print(error, file=sys.stderr)
        return 2
    except ValueError as error:
        print(f"invalid GitHub App credentials: {error}", file=sys.stderr)
        return 2
    for name in written:
        print(f"wrote {name}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
