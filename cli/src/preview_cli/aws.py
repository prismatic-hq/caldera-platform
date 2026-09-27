from dataclasses import dataclass
from typing import Any

import boto3
from botocore.exceptions import ClientError


@dataclass(frozen=True)
class Image:
    tag: str
    digest: str

    @property
    def reference(self) -> str:
        return f"{self.tag}@{self.digest}"


def _error_code(error: ClientError) -> str:
    return error.response.get("Error", {}).get("Code", "")


class Ecr:
    def __init__(self, client: Any = None) -> None:
        self._client = client or boto3.client("ecr")
        self.registry: str | None = None

    def find(self, repository: str, tag: str) -> Image | None:
        try:
            response = self._client.describe_images(
                repositoryName=repository, imageIds=[{"imageTag": tag}]
            )
        except ClientError as error:
            if _error_code(error) == "ImageNotFoundException":
                return None
            if _error_code(error) == "RepositoryNotFoundException":
                raise LookupError(f"ECR repository {repository!r} not found") from error
            raise
        detail = response["imageDetails"][0]
        region = self._client.meta.region_name
        self.registry = f"{detail['registryId']}.dkr.ecr.{region}.amazonaws.com"
        return Image(tag, detail["imageDigest"])


class Parameters:
    def __init__(self, client: Any = None) -> None:
        self._client = client or boto3.client("ssm")

    def get(self, name: str) -> str | None:
        try:
            return self._client.get_parameter(Name=name)["Parameter"]["Value"]
        except ClientError as error:
            if _error_code(error) == "ParameterNotFound":
                return None
            raise
