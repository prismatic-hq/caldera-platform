import uuid
from datetime import UTC, datetime, timedelta

import pytest

from preview_cli.lock import EnvironmentLock, KubectlLeases, Lease, LeaseConflict, LockTimeout
from tests.integration.conftest import KIND_CONTEXT

NAMESPACE = "lock-test"


@pytest.fixture(scope="module")
def leases(kubectl) -> KubectlLeases:
    kubectl("create", "namespace", NAMESPACE)
    yield KubectlLeases(NAMESPACE, KIND_CONTEXT)
    kubectl("delete", "namespace", NAMESPACE, "--ignore-not-found", "--wait=false")


def environment_lock(leases: KubectlLeases, holder: str, environment: str, wait: float = 0):
    return EnvironmentLock(leases, environment, holder, wait_seconds=wait, poll_seconds=0.2)


def test_second_holder_times_out_until_the_first_releases(leases) -> None:
    environment = f"env-{uuid.uuid4().hex[:8]}"
    first = environment_lock(leases, "tremor-api/1", environment)

    with first.held(), pytest.raises(LockTimeout, match="held by tremor-api/1"):
        environment_lock(leases, "steward-api/2", environment, wait=0.5).acquire()

    with environment_lock(leases, "steward-api/2", environment).held():
        assert leases.get(f"preview-{environment}").holder == "steward-api/2"
    assert leases.get(f"preview-{environment}") is None


def test_stale_lease_is_taken_over(leases) -> None:
    name = f"preview-stale-{uuid.uuid4().hex[:8]}"
    leases.create(name, Lease("crashed/1", datetime.now(UTC) - timedelta(seconds=300), 120))

    environment_lock(leases, "steward-api/2", name.removeprefix("preview-")).acquire()

    assert leases.get(name).holder == "steward-api/2"


def test_writes_with_an_old_resource_version_conflict(leases) -> None:
    name = f"preview-race-{uuid.uuid4().hex[:8]}"
    leases.create(name, Lease("a/1", datetime.now(UTC), 120))
    stale = leases.get(name)
    leases.replace(name, Lease("b/2", datetime.now(UTC), 120, stale.version))

    with pytest.raises(LeaseConflict):
        leases.replace(name, Lease("c/3", datetime.now(UTC), 120, stale.version))
    with pytest.raises(LeaseConflict):
        leases.create(name, Lease("c/3", datetime.now(UTC), 120))


def test_missing_lease_namespace_fails_clearly() -> None:
    if not KIND_CONTEXT.startswith("kind-"):
        pytest.skip("KIND_CONTEXT is not set to a kind-* context")

    with pytest.raises(LookupError, match="lease namespace no-such-namespace not found"):
        KubectlLeases("no-such-namespace", KIND_CONTEXT).create(
            "preview-x", Lease("a/1", datetime.now(UTC), 120)
        )
