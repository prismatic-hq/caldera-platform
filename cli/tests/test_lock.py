import json
from collections.abc import Callable
from dataclasses import replace
from datetime import UTC, datetime, timedelta

import pytest

from preview_cli.lock import (
    EnvironmentLock,
    Lease,
    LeaseConflict,
    LockTimeout,
    holder_identity,
    lease_manifest,
    parse_lease,
)

START = datetime(2026, 9, 27, 12, 0, tzinfo=UTC)


class FakeClock:
    def __init__(self) -> None:
        self.elapsed = 0.0

    def now(self) -> datetime:
        return START + timedelta(seconds=self.elapsed)

    def monotonic(self) -> float:
        return self.elapsed

    def sleep(self, seconds: float) -> None:
        self.elapsed += seconds


class FakeLeases:
    """In-memory lease store with the API server's optimistic concurrency."""

    def __init__(self) -> None:
        self.leases: dict[str, Lease] = {}
        self.writes = 0
        self.conflict_next_write = False

    def get(self, name: str) -> Lease | None:
        return self.leases.get(name)

    def _write(self, name: str, lease: Lease) -> None:
        if self.conflict_next_write:
            self.conflict_next_write = False
            self.leases[name] = Lease("racer", START, 120, "racer-version")
            raise LeaseConflict(name)
        self.writes += 1
        self.leases[name] = replace(lease, version=str(self.writes))

    def create(self, name: str, lease: Lease) -> None:
        if name in self.leases:
            raise LeaseConflict(name)
        self._write(name, lease)

    def replace(self, name: str, lease: Lease) -> None:
        current = self.leases.get(name)
        if current is None or current.version != lease.version:
            raise LeaseConflict(name)
        self._write(name, lease)

    def delete(self, name: str) -> None:
        self.leases.pop(name, None)


class ReleasingClock(FakeClock):
    def __init__(self, release: Callable[[], None]) -> None:
        super().__init__()
        self.release = release

    def sleep(self, seconds: float) -> None:
        super().sleep(seconds)
        self.release()


def lock(store: FakeLeases, clock: FakeClock, holder: str, wait: float = 30) -> EnvironmentLock:
    return EnvironmentLock(
        store,
        "quake-alerts",
        holder,
        wait_seconds=wait,
        duration_seconds=120,
        poll_seconds=5,
        now=clock.now,
        monotonic=clock.monotonic,
        sleep=clock.sleep,
    )


def test_acquire_creates_a_lease_named_after_the_environment() -> None:
    store, clock = FakeLeases(), FakeClock()

    lock(store, clock, "tremor-api/1").acquire()

    assert store.leases["preview-quake-alerts"].holder == "tremor-api/1"
    assert store.leases["preview-quake-alerts"].renewed == START


def test_a_held_lock_times_out_with_the_holder_named() -> None:
    store, clock = FakeLeases(), FakeClock()
    lock(store, clock, "tremor-api/1").acquire()

    with pytest.raises(LockTimeout, match="held by tremor-api/1.*waited 30s"):
        lock(store, clock, "steward-api/2").acquire()

    assert clock.elapsed == 30


def test_waiter_acquires_once_the_holder_releases() -> None:
    store = FakeLeases()
    first = lock(store, FakeClock(), "tremor-api/1")
    first.acquire()

    lock(store, ReleasingClock(first.release), "steward-api/2").acquire()

    assert store.leases["preview-quake-alerts"].holder == "steward-api/2"


def test_a_stale_lease_expires_after_its_duration() -> None:
    store, clock = FakeLeases(), FakeClock()
    lock(store, clock, "crashed/1").acquire()
    clock.elapsed = 121

    lock(store, clock, "steward-api/2", wait=0).acquire()

    assert store.leases["preview-quake-alerts"].holder == "steward-api/2"


def test_a_lost_race_retries_instead_of_overwriting() -> None:
    store, clock = FakeLeases(), FakeClock()
    store.conflict_next_write = True

    with pytest.raises(LockTimeout, match="held by racer"):
        lock(store, clock, "steward-api/2", wait=10).acquire()


def test_release_leaves_a_lease_taken_over_by_someone_else() -> None:
    store, clock = FakeLeases(), FakeClock()
    mine = lock(store, clock, "tremor-api/1")
    mine.acquire()
    clock.elapsed = 121
    lock(store, clock, "steward-api/2", wait=0).acquire()

    mine.release()

    assert store.leases["preview-quake-alerts"].holder == "steward-api/2"


def test_held_releases_on_failure() -> None:
    store, clock = FakeLeases(), FakeClock()

    with pytest.raises(RuntimeError), lock(store, clock, "tremor-api/1").held():
        raise RuntimeError("helm failed")

    assert store.leases == {}


def test_renew_moves_the_renew_time_forward() -> None:
    store, clock = FakeLeases(), FakeClock()
    mine = lock(store, clock, "tremor-api/1")
    mine.acquire()
    clock.elapsed = 60

    mine.renew()

    assert store.leases["preview-quake-alerts"].renewed == START + timedelta(seconds=60)


def test_lease_manifest_round_trips() -> None:
    lease = Lease("tremor-api/1", START, 120, "42")
    manifest = lease_manifest("preview-x", "caldera-locks", lease)

    assert manifest["metadata"] == {
        "name": "preview-x",
        "namespace": "caldera-locks",
        "resourceVersion": "42",
    }
    assert manifest["spec"]["renewTime"] == "2026-09-27T12:00:00.000000Z"
    assert parse_lease(json.dumps(manifest)) == lease


def test_holder_identity_is_repo_and_run_id_in_actions() -> None:
    env = {"GITHUB_REPOSITORY": "prismatic-hq/tremor-api", "GITHUB_RUN_ID": "99"}

    assert holder_identity(env) == "prismatic-hq/tremor-api/99"
