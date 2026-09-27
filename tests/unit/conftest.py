import json
import os
from collections.abc import Callable
from pathlib import Path

import aws_cdk as cdk
import pytest

CDK_JSON = Path(__file__).resolve().parents[2] / "cdk.json"
TEST_DOMAIN = "example.com"

AppFactory = Callable[..., cdk.App]


@pytest.fixture(autouse=True)
def isolated_aws_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """Keep the developer's AWS profile out of stubbed boto3 sessions."""
    for name in ("AWS_PROFILE", "AWS_DEFAULT_PROFILE"):
        monkeypatch.delenv(name, raising=False)


@pytest.fixture(autouse=True)
def isolated_caldera_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """Keep the developer's CALDERA_* settings out of synthesized templates."""
    for name in [n for n in os.environ if n.startswith("CALDERA_")]:
        monkeypatch.delenv(name)


@pytest.fixture(scope="session")
def new_app() -> AppFactory:
    """Build an App with the cdk.json feature flags, as `cdk synth` does."""
    flags = json.loads(CDK_JSON.read_text())["context"]

    def factory(context: dict | None = None, **kwargs: object) -> cdk.App:
        return cdk.App(context={**flags, "domain": TEST_DOMAIN, **(context or {})}, **kwargs)

    return factory
