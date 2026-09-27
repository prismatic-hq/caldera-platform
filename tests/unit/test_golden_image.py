import hashlib
import json
from collections.abc import Iterator
from pathlib import Path

import boto3
import pytest
from botocore.stub import Stubber

from golden_seeder.fixtures import dataset
from golden_seeder.render import digest, render
from preview_cli.parameters import DATASET_VERSION_PARAMETER
from scripts.golden_image import (
    compressed_sizes,
    dataset_version,
    image_exists,
    main,
    parse_heads,
    publish_dataset_version,
)

DIGEST = "a" * 64


def _client(service: str):
    return boto3.Session(
        region_name="us-east-1", aws_access_key_id="test", aws_secret_access_key="test"
    ).client(service)


@pytest.fixture
def ecr() -> Iterator[tuple[object, Stubber]]:
    client = _client("ecr")
    with Stubber(client) as stubber:
        yield client, stubber
        stubber.assert_no_pending_responses()


@pytest.fixture
def ssm() -> Iterator[tuple[object, Stubber]]:
    client = _client("ssm")
    with Stubber(client) as stubber:
        yield client, stubber
        stubber.assert_no_pending_responses()


def test_dataset_version_is_stable_for_the_same_inputs() -> None:
    heads = {"tremor": "0002", "steward": "0002"}

    assert dataset_version(DIGEST, heads) == dataset_version(DIGEST, dict(reversed(heads.items())))
    assert dataset_version(DIGEST, heads).startswith("ds-")


@pytest.mark.parametrize(
    ("digest", "heads"),
    [
        ("b" * 64, {"tremor": "0002", "steward": "0002"}),
        (DIGEST, {"tremor": "0003", "steward": "0002"}),
        (DIGEST, {"tremor": "0002", "steward": "0003"}),
    ],
)
def test_dataset_version_changes_with_the_seeder_or_either_alembic_head(
    digest: str, heads: dict[str, str]
) -> None:
    assert dataset_version(digest, heads) != dataset_version(
        DIGEST, {"tremor": "0002", "steward": "0002"}
    )


def test_parse_heads_reads_alembic_heads_output() -> None:
    assert parse_heads(["tremor=0002 (head)", "steward=0001 (head)"]) == {
        "tremor": "0002",
        "steward": "0001",
    }


@pytest.mark.parametrize("value", ["tremor", "tremor=", "tremor=0002 (head)\n0003 (head)"])
def test_parse_heads_rejects_missing_or_multiple_heads(value: str) -> None:
    with pytest.raises(ValueError, match="tremor"):
        parse_heads([value])


def test_image_exists_is_true_when_ecr_has_the_tag(ecr) -> None:
    client, stubber = ecr
    stubber.add_response(
        "describe_images",
        {"imageDetails": [{"imageTags": ["ds-1"]}]},
        {"repositoryName": "golden-db", "imageIds": [{"imageTag": "ds-1"}]},
    )

    assert image_exists(client, "golden-db", "ds-1")


def test_image_exists_is_false_when_ecr_has_no_such_tag(ecr) -> None:
    client, stubber = ecr
    stubber.add_client_error("describe_images", service_error_code="ImageNotFoundException")

    assert not image_exists(client, "golden-db", "ds-1")


def test_image_exists_raises_other_errors(ecr) -> None:
    client, stubber = ecr
    stubber.add_client_error("describe_images", service_error_code="AccessDeniedException")

    with pytest.raises(Exception, match="AccessDenied"):
        image_exists(client, "golden-db", "ds-1")


def test_publish_writes_the_dataset_version_parameter(ssm) -> None:
    client, stubber = ssm
    stubber.add_response(
        "put_parameter",
        {"Version": 3},
        {
            "Name": "/prismatic/golden-db/dataset-version",
            "Value": "ds-1",
            "Type": "String",
            "Overwrite": True,
        },
    )

    assert publish_dataset_version(client, "ds-1") == DATASET_VERSION_PARAMETER


def _blob(layout: Path, document: dict) -> dict:
    data = json.dumps(document).encode()
    digest = hashlib.sha256(data).hexdigest()
    (layout / "blobs" / "sha256" / digest).write_bytes(data)
    return {"digest": f"sha256:{digest}", "size": len(data)}


def _manifest(layout: Path, platform: str, layer_sizes: list[int]) -> dict:
    manifest = {
        "config": {"size": 100},
        "layers": [{"size": size} for size in layer_sizes],
    }
    os, arch = platform.split("/")
    return {**_blob(layout, manifest), "platform": {"os": os, "architecture": arch}}


@pytest.fixture
def layout(tmp_path: Path) -> Path:
    (tmp_path / "blobs" / "sha256").mkdir(parents=True)
    index = {
        "manifests": [
            _manifest(tmp_path, "linux/amd64", [1000, 2000]),
            _manifest(tmp_path, "linux/arm64", [1500, 2500]),
        ]
    }
    image_index = _blob(tmp_path, index)
    (tmp_path / "index.json").write_text(json.dumps({"manifests": [image_index]}))
    return tmp_path


def test_compressed_sizes_sums_config_and_layers_per_platform(layout: Path) -> None:
    assert compressed_sizes(layout) == {"linux/amd64": 3100, "linux/arm64": 4100}


def test_check_size_fails_when_any_platform_exceeds_the_cap(layout: Path, capsys) -> None:
    assert main(["check-size", str(layout), "--limit-bytes", "4000"]) == 1
    assert "linux/arm64" in capsys.readouterr().err


def test_check_size_passes_under_the_cap(layout: Path) -> None:
    assert main(["check-size", str(layout), "--limit-bytes", "5000"]) == 0


def test_version_prints_the_dataset_version_for_the_current_seeder(capsys) -> None:
    heads = ["tremor=0002 (head)", "steward=0001 (head)"]
    assert main(["version", "--head", heads[0], "--head", heads[1]]) == 0

    expected = dataset_version(digest(render(dataset())), parse_heads(heads))
    assert capsys.readouterr().out.strip() == expected
