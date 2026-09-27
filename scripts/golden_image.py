"""Golden-image workflow helpers: dataset version, ECR tag check, size cap, SSM publish."""

import argparse
import hashlib
import json
import sys
from pathlib import Path

import boto3
from botocore.exceptions import ClientError

from golden_seeder.fixtures import dataset
from golden_seeder.render import digest, render
from preview_cli.parameters import DATASET_VERSION_PARAMETER

DATASET_VERSION_PREFIX = "ds-"
DATASET_VERSION_HEX_LENGTH = 16
ONE_GIB = 1024**3


def dataset_version(seeder_digest: str, heads: dict[str, str]) -> str:
    inputs = "\n".join([seeder_digest, *(f"{name}={heads[name]}" for name in sorted(heads))])
    hashed = hashlib.sha256(inputs.encode()).hexdigest()
    return DATASET_VERSION_PREFIX + hashed[:DATASET_VERSION_HEX_LENGTH]


def parse_heads(values: list[str]) -> dict[str, str]:
    """Parse `name=<alembic heads output>`; each service must have exactly one head."""
    heads = {}
    for value in values:
        name, _, output = value.partition("=")
        revisions = [line.split()[0] for line in output.splitlines() if line.strip()]
        if len(revisions) != 1:
            raise ValueError(
                f"{name} must have exactly one Alembic head, got {revisions or 'none'}"
            )
        heads[name] = revisions[0]
    return heads


def image_exists(ecr, repository: str, tag: str) -> bool:
    try:
        ecr.describe_images(repositoryName=repository, imageIds=[{"imageTag": tag}])
    except ClientError as error:
        if error.response["Error"]["Code"] == "ImageNotFoundException":
            return False
        raise
    return True


def publish_dataset_version(ssm, version: str) -> str:
    ssm.put_parameter(Name=DATASET_VERSION_PARAMETER, Value=version, Type="String", Overwrite=True)
    return DATASET_VERSION_PARAMETER


def _read_blob(layout: Path, descriptor: dict) -> dict:
    algorithm, hexdigest = descriptor["digest"].split(":", 1)
    return json.loads((layout / "blobs" / algorithm / hexdigest).read_text())


def _platform_manifests(layout: Path, index: dict):
    for descriptor in index.get("manifests", []):
        document = _read_blob(layout, descriptor)
        if "manifests" in document:
            yield from _platform_manifests(layout, document)
        elif "platform" in descriptor:
            platform = descriptor["platform"]
            yield f"{platform['os']}/{platform['architecture']}", document


def compressed_sizes(layout: Path) -> dict[str, int]:
    """Compressed pull size per platform of an OCI image layout, attestations excluded."""
    index = json.loads((layout / "index.json").read_text())
    return {
        platform: manifest["config"]["size"] + sum(layer["size"] for layer in manifest["layers"])
        for platform, manifest in _platform_manifests(layout, index)
        if platform != "unknown/unknown"
    }


def _version(args: argparse.Namespace) -> int:
    print(dataset_version(digest(render(dataset())), parse_heads(args.head)))
    return 0


def _exists(args: argparse.Namespace) -> int:
    ecr = boto3.client("ecr")
    print("true" if image_exists(ecr, args.repository, args.tag) else "false")
    return 0


def _publish(args: argparse.Namespace) -> int:
    print(f"wrote {publish_dataset_version(boto3.client('ssm'), args.version)}={args.version}")
    return 0


def _check_size(args: argparse.Namespace) -> int:
    sizes = compressed_sizes(args.layout)
    if not sizes:
        print(f"no platform manifests found in {args.layout}", file=sys.stderr)
        return 1
    too_big = {platform: size for platform, size in sizes.items() if size > args.limit_bytes}
    for platform, size in sorted(sizes.items()):
        print(f"{platform}: {size} bytes compressed (limit {args.limit_bytes})")
    for platform, size in sorted(too_big.items()):
        print(
            f"{platform} image is {size} bytes compressed, over the {args.limit_bytes} byte cap;"
            " shrink the golden dataset",
            file=sys.stderr,
        )
    return 1 if too_big else 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)

    version = commands.add_parser("version", help="Print the dataset version")
    version.add_argument("--head", action="append", required=True, metavar="NAME=HEADS")
    version.set_defaults(run=_version)

    exists = commands.add_parser("exists", help="Print true if the ECR tag exists")
    exists.add_argument("--repository", required=True)
    exists.add_argument("--tag", required=True)
    exists.set_defaults(run=_exists)

    publish = commands.add_parser("publish", help="Write the dataset version to SSM")
    publish.add_argument("--version", required=True)
    publish.set_defaults(run=_publish)

    check_size = commands.add_parser("check-size", help="Fail if an image is over the size cap")
    check_size.add_argument("layout", type=Path, help="OCI image layout directory")
    check_size.add_argument("--limit-bytes", type=int, default=ONE_GIB)
    check_size.set_defaults(run=_check_size)

    args = parser.parse_args(argv)
    try:
        return args.run(args)
    except ValueError as error:
        print(f"golden-image: {error}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
