import boto3
import pytest
from botocore.stub import Stubber

from preview_cli.aws import Ecr, Image, Parameters

DIGEST = "sha256:" + "a" * 64


@pytest.fixture
def ecr_client():
    client = boto3.client(
        "ecr", region_name="us-east-1", aws_access_key_id="x", aws_secret_access_key="x"
    )
    with Stubber(client) as stubber:
        yield client, stubber


@pytest.fixture
def ssm_client():
    client = boto3.client(
        "ssm", region_name="us-east-1", aws_access_key_id="x", aws_secret_access_key="x"
    )
    with Stubber(client) as stubber:
        yield client, stubber


def test_find_returns_the_digest_and_remembers_the_registry(ecr_client) -> None:
    client, stubber = ecr_client
    stubber.add_response(
        "describe_images",
        {"imageDetails": [{"registryId": "123456789012", "imageDigest": DIGEST}]},
        {"repositoryName": "tremor-api", "imageIds": [{"imageTag": "sha-a1b2c3d"}]},
    )
    ecr = Ecr(client)

    assert ecr.find("tremor-api", "sha-a1b2c3d") == Image("sha-a1b2c3d", DIGEST)
    assert ecr.registry == "123456789012.dkr.ecr.us-east-1.amazonaws.com"


def test_find_returns_none_for_a_missing_tag(ecr_client) -> None:
    client, stubber = ecr_client
    stubber.add_client_error("describe_images", service_error_code="ImageNotFoundException")

    assert Ecr(client).find("tremor-api", "sha-a1b2c3d") is None
    assert Ecr(client).registry is None


def test_find_names_the_repository_when_it_is_missing(ecr_client) -> None:
    client, stubber = ecr_client
    stubber.add_client_error("describe_images", service_error_code="RepositoryNotFoundException")

    with pytest.raises(LookupError, match="ECR repository 'tremor-api' not found"):
        Ecr(client).find("tremor-api", "sha-a1b2c3d")


def test_image_reference_pins_the_digest() -> None:
    assert Image("sha-a1b2c3d", DIGEST).reference == f"sha-a1b2c3d@{DIGEST}"


def test_parameter_value(ssm_client) -> None:
    client, stubber = ssm_client
    stubber.add_response(
        "get_parameter",
        {"Parameter": {"Name": "/p", "Value": "ds-42"}},
        {"Name": "/p"},
    )

    assert Parameters(client).get("/p") == "ds-42"


def test_missing_parameter_is_none(ssm_client) -> None:
    client, stubber = ssm_client
    stubber.add_client_error("get_parameter", service_error_code="ParameterNotFound")

    assert Parameters(client).get("/p") is None
