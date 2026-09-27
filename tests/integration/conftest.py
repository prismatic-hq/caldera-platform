import os
import subprocess
from collections.abc import Callable

import pytest
import yaml

KIND_CONTEXT = os.environ.get("KIND_CONTEXT", "")

Kubectl = Callable[..., subprocess.CompletedProcess]


@pytest.fixture(scope="session")
def kubectl() -> Kubectl:
    """kubectl against a kind cluster only; set KIND_CONTEXT=kind-<name> to run these tests."""
    if not KIND_CONTEXT.startswith("kind-"):
        pytest.skip("KIND_CONTEXT is not set to a kind-* context")

    def run(*args: str, documents: list[dict] | None = None, as_user: str | None = None):
        impersonation = [f"--as={as_user}"] if as_user else []
        return subprocess.run(
            ["kubectl", "--context", KIND_CONTEXT, *impersonation, *args],
            input=yaml.safe_dump_all(documents) if documents else None,
            capture_output=True,
            text=True,
            check=False,
        )

    return run
