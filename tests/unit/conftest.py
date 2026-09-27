import json
from collections.abc import Callable
from pathlib import Path

import aws_cdk as cdk
import pytest

CDK_JSON = Path(__file__).resolve().parents[2] / "cdk.json"

AppFactory = Callable[..., cdk.App]


@pytest.fixture(scope="session")
def new_app() -> AppFactory:
    """Build an App with the cdk.json feature flags, as `cdk synth` does."""
    flags = json.loads(CDK_JSON.read_text())["context"]

    def factory(context: dict | None = None, **kwargs: object) -> cdk.App:
        return cdk.App(context={**flags, **(context or {})}, **kwargs)

    return factory
