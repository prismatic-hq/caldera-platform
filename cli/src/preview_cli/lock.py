import getpass
import json
import os
import socket
import subprocess
import sys
import threading
import time
from collections.abc import Callable, Iterator, Mapping
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Protocol

import yaml

MICRO_TIME = "%Y-%m-%dT%H:%M:%S.%fZ"
LEASE_DURATION_SECONDS = 120
POLL_SECONDS = 5


@dataclass(frozen=True)
class Lease:
    holder: str
    renewed: datetime
    duration_seconds: int
    version: str | None = None

    def expired(self, now: datetime) -> bool:
        return now > self.renewed + timedelta(seconds=self.duration_seconds)


class LeaseConflict(Exception):
    pass


class LockTimeout(TimeoutError):
    pass


class LeaseStore(Protocol):
    def get(self, name: str) -> Lease | None: ...
    def create(self, name: str, lease: Lease) -> None: ...
    def replace(self, name: str, lease: Lease) -> None: ...
    def delete(self, name: str) -> None: ...


def holder_identity(env: Mapping[str, str] = os.environ) -> str:
    if env.get("GITHUB_REPOSITORY") and env.get("GITHUB_RUN_ID"):
        return f"{env['GITHUB_REPOSITORY']}/{env['GITHUB_RUN_ID']}"
    return f"{getpass.getuser()}@{socket.gethostname()}/{os.getpid()}"


def lease_manifest(name: str, namespace: str, lease: Lease) -> dict:
    metadata = {"name": name, "namespace": namespace}
    if lease.version:
        metadata["resourceVersion"] = lease.version
    return {
        "apiVersion": "coordination.k8s.io/v1",
        "kind": "Lease",
        "metadata": metadata,
        "spec": {
            "holderIdentity": lease.holder,
            "leaseDurationSeconds": lease.duration_seconds,
            "renewTime": lease.renewed.strftime(MICRO_TIME),
        },
    }


def parse_lease(lease_json: str) -> Lease:
    lease = json.loads(lease_json)
    spec = lease.get("spec", {})
    return Lease(
        holder=spec.get("holderIdentity", ""),
        renewed=datetime.strptime(spec["renewTime"], MICRO_TIME).replace(tzinfo=UTC),
        duration_seconds=int(spec.get("leaseDurationSeconds", 0)),
        version=lease["metadata"].get("resourceVersion"),
    )


class EnvironmentLock:
    """A Lease per preview environment so deploys from different repos never overlap."""

    def __init__(
        self,
        store: LeaseStore,
        environment: str,
        holder: str,
        *,
        wait_seconds: float,
        duration_seconds: int = LEASE_DURATION_SECONDS,
        poll_seconds: float = POLL_SECONDS,
        now: Callable[[], datetime] = lambda: datetime.now(UTC),
        monotonic: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self.store = store
        self.name = f"preview-{environment}"
        self.holder = holder
        self.wait_seconds = wait_seconds
        self.duration_seconds = duration_seconds
        self.poll_seconds = poll_seconds
        self._now = now
        self._monotonic = monotonic
        self._sleep = sleep

    def _claim(self, current: Lease | None) -> None:
        version = current.version if current else None
        lease = Lease(self.holder, self._now(), self.duration_seconds, version)
        if current is None:
            self.store.create(self.name, lease)
        else:
            self.store.replace(self.name, lease)

    def acquire(self) -> None:
        deadline = self._monotonic() + self.wait_seconds
        while True:
            current = self.store.get(self.name)
            if current is None or current.holder == self.holder or current.expired(self._now()):
                try:
                    self._claim(current)
                    return
                except LeaseConflict:
                    continue
            remaining = deadline - self._monotonic()
            if remaining <= 0:
                raise LockTimeout(
                    f"preview environment lock {self.name} is held by {current.holder} "
                    f"(renewed {current.renewed:%H:%M:%S} UTC); waited {self.wait_seconds:g}s. "
                    f"Retry later, or it expires {current.duration_seconds}s after its last renewal"
                )
            self._sleep(min(self.poll_seconds, remaining))

    def renew(self) -> None:
        current = self.store.get(self.name)
        if current is not None and current.holder == self.holder:
            self._claim(current)

    def release(self) -> None:
        current = self.store.get(self.name)
        if current is not None and current.holder == self.holder:
            self.store.delete(self.name)

    @contextmanager
    def held(self) -> Iterator[None]:
        self.acquire()
        stop = threading.Event()
        renewer = threading.Thread(target=self._renew_until, args=(stop,), daemon=True)
        renewer.start()
        try:
            yield
        finally:
            stop.set()
            renewer.join()
            self.release()

    def _renew_until(self, stop: threading.Event) -> None:
        while not stop.wait(self.duration_seconds / 4):
            try:
                self.renew()
            except (LeaseConflict, LookupError, OSError) as error:
                print(f"warning: could not renew lock {self.name}: {error}", file=sys.stderr)


class KubectlLeases:
    """Lease storage through kubectl, using resourceVersion for optimistic concurrency."""

    def __init__(self, namespace: str, context: str | None = None) -> None:
        self.namespace = namespace
        self._context = ["--context", context] if context else []

    def _kubectl(self, *args: str, stdin: str | None = None) -> subprocess.CompletedProcess:
        return subprocess.run(
            ["kubectl", *self._context, *args],
            input=stdin,
            capture_output=True,
            text=True,
            check=False,
        )

    def _fail(self, action: str, name: str, result: subprocess.CompletedProcess) -> None:
        error = result.stderr.strip()
        if "AlreadyExists" in error or "Operation cannot be fulfilled" in error:
            raise LeaseConflict(name)
        if "namespaces" in error and "not found" in error:
            raise LookupError(
                f"lease namespace {self.namespace} not found: it is created with the platform "
                "(cdk/runner_access.py); create it for local clusters"
            )
        raise OSError(f"kubectl {action} lease {self.namespace}/{name} failed: {error}")

    def get(self, name: str) -> Lease | None:
        result = self._kubectl(
            "get", "lease", name, "-n", self.namespace, "-o", "json", "--ignore-not-found"
        )
        if result.returncode != 0:
            self._fail("get", name, result)
        return parse_lease(result.stdout) if result.stdout.strip() else None

    def _write(self, verb: str, name: str, lease: Lease) -> None:
        manifest = yaml.safe_dump(lease_manifest(name, self.namespace, lease))
        result = self._kubectl(verb, "-f", "-", stdin=manifest)
        if result.returncode != 0:
            self._fail(verb, name, result)

    def create(self, name: str, lease: Lease) -> None:
        self._write("create", name, lease)

    def replace(self, name: str, lease: Lease) -> None:
        self._write("replace", name, lease)

    def delete(self, name: str) -> None:
        result = self._kubectl("delete", "lease", name, "-n", self.namespace, "--ignore-not-found")
        if result.returncode != 0:
            self._fail("delete", name, result)
