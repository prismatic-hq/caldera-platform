"""Presenter runner for the REQUIREMENTS.md Section 8 demo: pushes, previews, isolation, teardown.

Each step prints what the audience should see and the exact commands, then waits for Enter.
`--dry-run` prints every command without calling GitHub, AWS or the cluster.
"""

import argparse
import base64
import json
import shlex
import subprocess
import sys
import time
import urllib.error
import urllib.request
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from pathlib import Path

from preview_cli.aws import Parameters
from preview_cli.parameters import DATASET_VERSION_PARAMETER, PREVIEW_DOMAIN_PARAMETER
from preview_cli.registry import ServiceRegistry
from preview_cli.resolver import environment_name

ORG = "prismatic-hq"
SERVICES_FILE = Path(__file__).resolve().parents[1] / "services.yaml"
DOMAIN_PLACEHOLDER = "<preview-domain>"
MAIN_SHA_PLACEHOLDER = "<main-sha>"
KIND_CONTEXT = "kind-caldera"
POLL_SECONDS = 2.0
LIVE_TIMEOUT_SECONDS = 600.0
RUN_TIMEOUT_SECONDS = 900.0
TEARDOWN_TIMEOUT_SECONDS = 300.0
WATCH_ROUNDS = 6
WATCH_SECONDS = 5.0
PLAN_STEPS = 10
DEMO_BRANCHES = (
    ("tremor-api", "feature/quake-alerts"),
    ("steward-api", "fix-crew-sync"),
    ("steward-api", "feature/quake-alerts"),
    ("tremor-api", "feature/tsunami"),
    ("steward-api", "feature/lava-flow"),
)
CODE_WALK = (
    ("CDK stacks", "cdk/stacks/"),
    ("service chart", "charts/service/"),
    ("services umbrella chart", "charts/services/"),
    ("preview CLI", "cli/src/preview_cli/"),
    ("preview environment workflow", ".github/workflows/preview-environment.yml"),
    ("teardown workflow", ".github/workflows/preview-environment-teardown.yml"),
    ("golden image build", ".github/workflows/golden-image.yml, images/golden-db/"),
    ("headroom and priorities", "platform/"),
)

Runner = Callable[[list[str]], subprocess.CompletedProcess]
Request = Callable[..., tuple[int, dict | None]]


class DemoError(Exception):
    pass


@dataclass(frozen=True)
class DemoConfig:
    context: str
    domain: str
    org: str
    stamp: str
    dry_run: bool
    auto: bool


@dataclass(frozen=True)
class Step:
    number: int
    title: str
    audience: str
    actions: tuple = field(default_factory=tuple)


def environment_for(registry: ServiceRegistry, repo: str, branch: str) -> str:
    return environment_name(registry.names, registry.by_repo(repo).name, branch)


def service_url(service: str, environment: str, domain: str) -> str:
    return f"https://{service}-{environment}.{domain}"


def _kubectl(config: DemoConfig, *args: str) -> list[str]:
    return ["kubectl", "--context", config.context, *args]


def _main_sha_command(config: DemoConfig, repo: str) -> list[str]:
    return ["gh", "api", f"repos/{config.org}/{repo}/git/ref/heads/main", "--jq", ".object.sha"]


def _delete_branch_command(config: DemoConfig, repo: str, branch: str) -> list[str]:
    return ["gh", "api", "-X", "DELETE", f"repos/{config.org}/{repo}/git/refs/heads/{branch}"]


def _run_list_command(config: DemoConfig, repo: str, branch: str) -> list[str]:
    return [
        "gh",
        "run",
        "list",
        "--repo",
        f"{config.org}/{repo}",
        "--branch",
        branch,
        "--limit",
        "1",
        "--json",
        "status,conclusion,url",
    ]


@dataclass(frozen=True)
class Shell:
    argv: tuple[str, ...]

    def lines(self, config: DemoConfig) -> list[str]:
        return [shlex.join(self.argv)]

    def run(self, demo: "Demo") -> None:
        demo.echo(demo.check(list(self.argv)).rstrip())


@dataclass(frozen=True)
class Push:
    repo: str
    branch: str

    def _marker(self, config: DemoConfig) -> str:
        return f"repos/{config.org}/{self.repo}/contents/.demo/{config.stamp}.txt"

    def _commands(self, config: DemoConfig, sha: str) -> list[list[str]]:
        content = base64.b64encode(f"demo push {config.stamp}\n".encode()).decode()
        return [
            [
                "gh",
                "api",
                f"repos/{config.org}/{self.repo}/git/refs",
                "-f",
                f"ref=refs/heads/{self.branch}",
                "-f",
                f"sha={sha}",
            ],
            [
                "gh",
                "api",
                "-X",
                "PUT",
                self._marker(config),
                "-f",
                f"message=chore: demo push {config.stamp}",
                "-f",
                f"content={content}",
                "-f",
                f"branch={self.branch}",
            ],
        ]

    def lines(self, config: DemoConfig) -> list[str]:
        commands = [_main_sha_command(config, self.repo)]
        commands += self._commands(config, MAIN_SHA_PLACEHOLDER)
        return [shlex.join(command) for command in commands]

    def run(self, demo: "Demo") -> None:
        sha = demo.check(_main_sha_command(demo.config, self.repo)).strip()
        create_ref, commit = self._commands(demo.config, sha)
        created = demo.runner(create_ref)
        exists = "Reference already exists" in created.stdout + created.stderr
        if created.returncode != 0 and not exists:
            raise DemoError(f"could not create {self.repo} {self.branch}: {created.stderr}")
        demo.check(commit)
        demo.echo(f"pushed {self.repo} {self.branch}")


@dataclass(frozen=True)
class WaitLive:
    service: str
    environment: str
    repo: str
    branch: str

    def lines(self, config: DemoConfig) -> list[str]:
        url = service_url(self.service, self.environment, config.domain)
        return [
            f"poll {url}/readyz until 200",
            shlex.join(_run_list_command(config, self.repo, self.branch)),
        ]

    def run(self, demo: "Demo") -> None:
        demo.wait_live(self.service, self.environment, self.repo, self.branch)


@dataclass(frozen=True)
class WaitRun:
    repo: str
    branch: str

    def lines(self, config: DemoConfig) -> list[str]:
        command = shlex.join(_run_list_command(config, self.repo, self.branch))
        return [f"poll {command} until completed"]

    def run(self, demo: "Demo") -> None:
        demo.wait_run(self.repo, self.branch)


@dataclass(frozen=True)
class WriteAlert:
    environment: str

    def lines(self, config: DemoConfig) -> list[str]:
        url = service_url("tremor", self.environment, config.domain)
        return [f'curl -X POST {url}/alerts -d \'{{"station": "DEMO-{config.stamp}", ...}}\'']

    def run(self, demo: "Demo") -> None:
        demo.write_alert(self.environment)


@dataclass(frozen=True)
class ExpectAbsent:
    environment: str

    def lines(self, config: DemoConfig) -> list[str]:
        url = service_url("tremor", self.environment, config.domain)
        return [f"curl {url}/alerts/<alert-id>  # expect 404"]

    def run(self, demo: "Demo") -> None:
        demo.expect_absent(self.environment)


@dataclass(frozen=True)
class Watch:
    commands: tuple[tuple[str, ...], ...]

    def lines(self, config: DemoConfig) -> list[str]:
        header = f"every {WATCH_SECONDS:g}s, {WATCH_ROUNDS} times:"
        return [header, *(shlex.join(command) for command in self.commands)]

    def run(self, demo: "Demo") -> None:
        for round_number in range(1, WATCH_ROUNDS + 1):
            demo.echo(f"-- round {round_number}/{WATCH_ROUNDS}")
            for command in self.commands:
                demo.echo(demo.check(list(command)).rstrip())
            if round_number < WATCH_ROUNDS:
                demo.sleep(WATCH_SECONDS)


@dataclass(frozen=True)
class Teardown:
    environments: tuple[str, ...] = ()

    def lines(self, config: DemoConfig) -> list[str]:
        deletes = [
            shlex.join(_delete_branch_command(config, repo, branch))
            for repo, branch in DEMO_BRANCHES
        ]
        if not self.environments:
            return deletes
        namespaces = " ".join(f"preview-{environment}" for environment in self.environments)
        return [
            *deletes,
            f"poll kubectl --context {config.context} get namespace {namespaces} until gone",
        ]

    def run(self, demo: "Demo") -> None:
        if not demo.confirm("Delete the demo branches in both service repos?"):
            demo.echo("Not deleting branches.")
            return
        demo.delete_branches()
        if self.environments:
            demo.wait_gone(self.environments)


@dataclass(frozen=True)
class KindUp:
    repo: str
    branch: str
    registry: ServiceRegistry

    def _command(self, shas: dict[str, str], dataset_version: str) -> list[str]:
        pushed = self.registry.by_repo(self.repo).name
        others = [
            f"--sha-for={service}={sha}" for service, sha in shas.items() if service != pushed
        ]
        return [
            "uv",
            "run",
            "preview",
            "env",
            "up",
            "--context",
            KIND_CONTEXT,
            "--repo",
            self.repo,
            "--branch",
            self.branch,
            "--sha",
            shas[pushed],
            "--offline",
            *others,
            "--dataset-version",
            dataset_version,
        ]

    def lines(self, config: DemoConfig) -> list[str]:
        shas = {name: MAIN_SHA_PLACEHOLDER for name in self.registry.names}
        return [
            "kind get clusters  # mise run local:up when caldera is missing",
            shlex.join(self._command(shas, "<dataset-version>")),
        ]

    def run(self, demo: "Demo") -> None:
        if "caldera" not in demo.check(["kind", "get", "clusters"]).split():
            if not demo.confirm("kind cluster caldera is missing; run mise run local:up?"):
                demo.echo("Skipping the kind run.")
                return
            demo.echo(demo.check(["mise", "run", "local:up"]).rstrip())
        shas = {
            service.name: demo.check(_main_sha_command(demo.config, service.repo)).strip()
            for service in self.registry.services
        }
        demo.echo(demo.check(self._command(shas, demo.dataset_version())).rstrip())


@dataclass(frozen=True)
class Note:
    text: tuple[str, ...]

    def lines(self, config: DemoConfig) -> list[str]:
        return list(self.text)

    def run(self, demo: "Demo") -> None:
        return None


def build_steps(config: DemoConfig, registry: ServiceRegistry) -> list[Step]:
    def env(repo: str, branch: str) -> str:
        return environment_for(registry, repo, branch)

    quake = env("tremor-api", "feature/quake-alerts")
    crew = env("steward-api", "fix-crew-sync")
    tsunami = env("tremor-api", "feature/tsunami")
    lava = env("steward-api", "feature/lava-flow")
    hubble = f"https://hubble.dev.{config.domain.removeprefix('preview.')}"
    return [
        Step(
            1,
            "Baseline: dev runs main of both services",
            "Helm releases and routes for dev. The cycle-time dashboard is not built yet;"
            " show the timings table in a preview run's job summary instead.",
            (
                Shell(("helm", "--kube-context", config.context, "list", "-A")),
                Shell(tuple(_kubectl(config, "get", "httproutes", "-A"))),
            ),
        ),
        Step(
            2,
            "Scenario A: feature/quake-alerts in tremor-api only",
            f"Preview environment {quake} with steward on main; the URL answers while the"
            " build-gated run is still in progress (optimistic deploy).",
            (
                Push("tremor-api", "feature/quake-alerts"),
                WaitLive("tremor", quake, "tremor-api", "feature/quake-alerts"),
                WaitRun("tremor-api", "feature/quake-alerts"),
            ),
        ),
        Step(
            3,
            "Scenario B: fix-crew-sync in steward-api only",
            f"Non-feature branch gets its own preview environment {crew}.",
            (
                Push("steward-api", "fix-crew-sync"),
                WaitLive("steward", crew, "steward-api", "fix-crew-sync"),
            ),
        ),
        Step(
            4,
            "Scenario C1: feature/quake-alerts in steward-api joins the environment",
            f"The existing {quake} release updates to both branches; its values show the new"
            " steward image tag.",
            (
                Push("steward-api", "feature/quake-alerts"),
                WaitRun("steward-api", "feature/quake-alerts"),
                Shell(
                    (
                        "helm",
                        "--kube-context",
                        config.context,
                        "get",
                        "values",
                        f"preview-{quake}",
                        "-n",
                        f"preview-{quake}",
                    )
                ),
            ),
        ),
        Step(
            5,
            "Scenario C2: feature/tsunami and feature/lava-flow",
            f"Two separate preview environments, {tsunami} and {lava}.",
            (
                Push("tremor-api", "feature/tsunami"),
                Push("steward-api", "feature/lava-flow"),
                WaitLive("tremor", tsunami, "tremor-api", "feature/tsunami"),
                WaitLive("steward", lava, "steward-api", "feature/lava-flow"),
            ),
        ),
        Step(
            6,
            "Isolation and preview env reset",
            f"An alert written in {quake} is absent in {tsunami}; after reset it is gone from"
            f" {quake} too. Dropped cross-namespace flows show in Hubble at {hubble}.",
            (
                WriteAlert(quake),
                ExpectAbsent(tsunami),
                Shell(
                    ("uv", "run", "preview", "env", "reset", "--name", quake)
                    + ("--context", config.context)
                ),
                ExpectAbsent(quake),
            ),
        ),
        Step(
            7,
            "Headroom preemption",
            "Headroom pause pods are preempted by preview pods; Karpenter adds a NodeClaim"
            " in the background.",
            (
                Watch(
                    (
                        tuple(
                            _kubectl(
                                config,
                                "-n",
                                "caldera-system",
                                "get",
                                "pods",
                                "-l",
                                "app.kubernetes.io/name=headroom",
                                "-o",
                                "wide",
                            )
                        ),
                        tuple(
                            _kubectl(
                                config,
                                "get",
                                "pods",
                                "-A",
                                "-l",
                                "prismatic.dev/environment-kind=preview",
                                "-o",
                                "wide",
                            )
                        ),
                        tuple(_kubectl(config, "get", "nodeclaims")),
                        tuple(
                            _kubectl(
                                config,
                                "-n",
                                "caldera-system",
                                "get",
                                "events",
                                "--field-selector",
                                "reason=Preempted",
                            )
                        ),
                    )
                ),
            ),
        ),
        Step(
            8,
            "Teardown: delete the demo branches",
            "Branch deletes fire the teardown workflow; every demo namespace is gone in under"
            " a minute.",
            (Teardown((quake, crew, tsunami, lava)),),
        ),
        Step(
            9,
            "Same preview env up on kind",
            "The same CLI deploys to the local kind cluster with --context kind-caldera.",
            (KindUp("tremor-api", "feature/quake-alerts", registry),),
        ),
        Step(
            10,
            "Code walk",
            "Where each part lives.",
            (Note(tuple(f"{label}: {path}" for label, path in CODE_WALK)),),
        ),
    ]


def select_steps(
    steps: Sequence[Step], from_step: int | None = None, only: int | None = None
) -> list[Step]:
    for value in (from_step, only):
        if value is not None and not 1 <= value <= len(steps):
            raise DemoError(f"step must be between 1 and {len(steps)}, got {value}")
    if only is not None:
        return [step for step in steps if step.number == only]
    return [step for step in steps if step.number >= (from_step or 1)]


def describe(step: Step, config: DemoConfig) -> list[str]:
    return [line for action in step.actions for line in action.lines(config)]


def run_command(argv: list[str]) -> subprocess.CompletedProcess:
    return subprocess.run(argv, capture_output=True, text=True, check=False)


def http_request(method: str, url: str, body: dict | None = None) -> tuple[int, dict | None]:
    data = json.dumps(body).encode() if body is not None else None
    request = urllib.request.Request(
        url, data=data, method=method, headers={"Content-Type": "application/json"}
    )
    try:
        with urllib.request.urlopen(request, timeout=10) as response:
            payload = response.read()
            return response.status, json.loads(payload) if payload else None
    except urllib.error.HTTPError as error:
        return error.code, None
    except (urllib.error.URLError, TimeoutError, ConnectionError):
        return 0, None


class Demo:
    def __init__(
        self,
        config: DemoConfig,
        runner: Runner = run_command,
        request: Request = http_request,
        ask: Callable[[str], str] = input,
        echo: Callable[[str], None] = print,
        clock: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self.config = config
        self.runner = runner
        self.request = request
        self.ask = ask
        self.echo = echo
        self.clock = clock
        self.sleep = sleep
        self.alert_id: str | None = None

    def check(self, argv: list[str]) -> str:
        result = self.runner(argv)
        if result.returncode != 0:
            raise DemoError(
                f"{shlex.join(argv)} failed with exit code {result.returncode}: "
                f"{result.stderr.strip()}"
            )
        return result.stdout

    def confirm(self, question: str) -> bool:
        if self.config.auto:
            return True
        return self.ask(f"{question} [y/N] ").strip().lower() == "y"

    def run(self, steps: Sequence[Step]) -> None:
        for step in steps:
            self.echo(f"\n== Step {step.number}/{PLAN_STEPS}: {step.title}")
            self.echo(f"Audience sees: {step.audience}")
            for line in describe(step, self.config):
                self.echo(f"  $ {line}")
            if self.config.dry_run:
                continue
            if not self.config.auto:
                choice = self.ask("[Enter] run  [s] skip  [q] quit ").strip().lower()
                if choice == "q":
                    self.echo(f"Stopped at step {step.number}.")
                    return
                if choice == "s":
                    continue
            for action in step.actions:
                action.run(self)

    def _latest_run(self, repo: str, branch: str) -> dict | None:
        runs = json.loads(self.check(_run_list_command(self.config, repo, branch)) or "[]")
        return runs[0] if runs else None

    def _run_status(self, repo: str, branch: str) -> str:
        run = self._latest_run(repo, branch)
        if run is None:
            return "no run yet"
        return f"{run['status']} {run.get('conclusion') or ''} {run['url']}".replace("  ", " ")

    def wait_live(self, service: str, environment: str, repo: str, branch: str) -> None:
        url = f"{service_url(service, environment, self.config.domain)}/readyz"
        started = self.clock()
        while self.clock() - started < LIVE_TIMEOUT_SECONDS:
            if self.request("GET", url)[0] == 200:
                elapsed = self.clock() - started
                self.echo(f"{url} live after {elapsed:.0f}s")
                self.echo(f"run: {self._run_status(repo, branch)}")
                return
            self.sleep(POLL_SECONDS)
        raise DemoError(f"{url} not ready after {LIVE_TIMEOUT_SECONDS:.0f}s")

    def wait_run(self, repo: str, branch: str) -> None:
        started = self.clock()
        while self.clock() - started < RUN_TIMEOUT_SECONDS:
            run = self._latest_run(repo, branch)
            if run and run["status"] == "completed":
                elapsed = self.clock() - started
                self.echo(f"run {run['conclusion']} after {elapsed:.0f}s: {run['url']}")
                return
            self.sleep(POLL_SECONDS * 5)
        raise DemoError(f"{repo} {branch} run not completed after {RUN_TIMEOUT_SECONDS:.0f}s")

    def write_alert(self, environment: str) -> None:
        url = f"{service_url('tremor', environment, self.config.domain)}/alerts"
        body = {"station": f"DEMO-{self.config.stamp}", "severity": "info", "message": "demo"}
        status, created = self.request("POST", url, body)
        if status != 201 or not created:
            raise DemoError(f"POST {url} returned {status}, expected 201")
        self.alert_id = created["id"]
        self.echo(f"wrote alert {self.alert_id} in {environment}")

    def expect_absent(self, environment: str) -> None:
        if self.alert_id is None:
            raise DemoError("no alert written yet; run the isolation step from the start")
        url = f"{service_url('tremor', environment, self.config.domain)}/alerts/{self.alert_id}"
        status, _ = self.request("GET", url)
        if status != 404:
            raise DemoError(f"alert {self.alert_id} is visible in {environment} (GET {status})")
        self.echo(f"alert {self.alert_id} is absent in {environment} (404)")

    def delete_branches(self) -> None:
        for repo, branch in DEMO_BRANCHES:
            result = self.runner(_delete_branch_command(self.config, repo, branch))
            outcome = "deleted" if result.returncode == 0 else "not found or not deleted"
            self.echo(f"{repo} {branch}: {outcome}")

    def wait_gone(self, environments: Sequence[str]) -> None:
        namespaces = [f"preview-{environment}" for environment in environments]
        command = _kubectl(
            self.config, "get", "namespace", *namespaces, "--ignore-not-found", "-o", "name"
        )
        started = self.clock()
        while self.clock() - started < TEARDOWN_TIMEOUT_SECONDS:
            remaining = self.check(command).split()
            if not remaining:
                self.echo(f"all demo namespaces gone after {self.clock() - started:.0f}s")
                return
            self.echo(f"waiting for {' '.join(remaining)}")
            self.sleep(POLL_SECONDS * 2.5)
        raise DemoError(f"namespaces remain after {TEARDOWN_TIMEOUT_SECONDS:.0f}s")

    def dataset_version(self) -> str:
        version = Parameters().get(DATASET_VERSION_PARAMETER)
        if not version:
            raise DemoError(f"SSM parameter {DATASET_VERSION_PARAMETER} is not set")
        return version


def _domain(explicit: str | None, dry_run: bool) -> str:
    if explicit:
        return explicit
    if dry_run:
        return DOMAIN_PLACEHOLDER
    domain = Parameters().get(PREVIEW_DOMAIN_PARAMETER)
    if not domain:
        raise DemoError(f"SSM parameter {PREVIEW_DOMAIN_PARAMETER} is not set; pass --domain")
    return domain


def parse_args(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--auto", action="store_true", help="Run every step without prompts")
    parser.add_argument("--dry-run", action="store_true", help="Print commands, run nothing")
    selection = parser.add_mutually_exclusive_group()
    selection.add_argument("--from-step", type=int, metavar="N")
    selection.add_argument("--only", type=int, metavar="N")
    parser.add_argument("--context", default="caldera", help="kubeconfig context of EKS")
    parser.add_argument(
        "--domain", help="Preview domain; defaults to SSM " + PREVIEW_DOMAIN_PARAMETER
    )
    parser.add_argument(
        "--cleanup", action="store_true", help="Delete only the demo branches, then exit"
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    try:
        config = DemoConfig(
            context=args.context,
            domain=_domain(args.domain, args.dry_run),
            org=ORG,
            stamp=time.strftime("%Y%m%dT%H%M%S"),
            dry_run=args.dry_run,
            auto=args.auto,
        )
        demo = Demo(config)
        if args.cleanup:
            for line in Teardown().lines(config):
                print(f"  $ {line}")
            if not args.dry_run:
                Teardown().run(demo)
            return 0
        steps = build_steps(config, ServiceRegistry.load(SERVICES_FILE))
        demo.run(select_steps(steps, args.from_step, args.only))
    except (DemoError, ValueError, OSError) as error:
        print(f"demo: {error}", file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        print("\ndemo: interrupted", file=sys.stderr)
        return 130
    return 0


if __name__ == "__main__":
    sys.exit(main())
