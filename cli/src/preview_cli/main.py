import json
import os
import shlex
import subprocess
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Annotated

import typer
from botocore.exceptions import BotoCoreError, ClientError

from preview_cli.aws import Ecr, Parameters
from preview_cli.commands import (
    Command,
    DeployedRelease,
    Target,
    down_commands,
    helm_test_commands,
    list_environments_command,
    parse_release,
    recover_commands,
    reset_commands,
    status_command,
    up_commands,
)
from preview_cli.github import GitHub
from preview_cli.images import ImageChoice, ImageSource, choose_image
from preview_cli.parameters import DATASET_VERSION_PARAMETER, PREVIEW_DOMAIN_PARAMETER
from preview_cli.registry import ServiceRegistry
from preview_cli.resolver import (
    Action,
    PreviewPlan,
    feature_name,
    resolve_delete,
    resolve_push,
    validate_environment_name,
)
from preview_cli.timings import Stopwatch, summary_markdown

app = typer.Typer(no_args_is_help=True)
env_app = typer.Typer(
    no_args_is_help=True, help="Create, update, tear down and reset preview environments."
)
app.add_typer(env_app, name="env")

RepoOption = Annotated[str, typer.Option("--repo", help="Service repo, e.g. tremor-api")]
BranchOption = Annotated[str, typer.Option("--branch")]
BranchInOption = Annotated[
    list[str] | None,
    typer.Option(
        "--branch-in",
        help="Service whose repo also has this branch (repeatable); skips the GitHub lookup",
    ),
]
OfflineOption = Annotated[
    bool,
    typer.Option(
        "--offline",
        help="Make no GitHub or AWS calls; pass --branch-in, --sha-for and --dataset-version",
    ),
]
ShaForOption = Annotated[
    list[str] | None,
    typer.Option("--sha-for", help="SERVICE=SHA commit to deploy for a service (repeatable)"),
]
ServicesFileOption = Annotated[
    Path,
    typer.Option("--services-file", envvar="PREVIEW_SERVICES_FILE", help="Service registry"),
]
DryRun = Annotated[bool, typer.Option("--dry-run", help="Print commands without running them")]
ContextOption = Annotated[str | None, typer.Option("--context", help="kubeconfig context")]
ChartOption = Annotated[str, typer.Option("--chart")]
RegistryOption = Annotated[
    str | None, typer.Option("--registry", envvar="PREVIEW_REGISTRY", help="ECR registry host")
]
DatasetOption = Annotated[
    str | None,
    typer.Option(
        "--dataset-version",
        envvar="PREVIEW_DATASET_VERSION",
        help=f"Golden DB dataset version; defaults to SSM {DATASET_VERSION_PARAMETER}",
    ),
]
DomainOption = Annotated[
    str | None,
    typer.Option(
        "--domain",
        envvar="PREVIEW_DOMAIN",
        help=f"Preview URL domain; defaults to SSM {PREVIEW_DOMAIN_PARAMETER}",
    ),
]
GitHubOutputOption = Annotated[
    Path | None,
    typer.Option("--github-output", envvar="GITHUB_OUTPUT", help="File for GitHub step outputs"),
]
StepSummaryOption = Annotated[
    Path | None,
    typer.Option(
        "--github-step-summary", envvar="GITHUB_STEP_SUMMARY", help="File for the timing table"
    ),
]
DEFAULT_SERVICES_FILE = Path("services.yaml")
GOLDEN_DB_REPOSITORY = "golden-db"
EXPECTED_ERRORS = (ValueError, LookupError, OSError, BotoCoreError, ClientError)


@dataclass(frozen=True)
class Report:
    github_output: Path | None = None
    step_summary: Path | None = None


@dataclass(frozen=True)
class DeployOptions:
    dataset_version: str | None
    domain: str | None
    offline: bool
    dry_run: bool
    report: Report


def _github() -> GitHub:
    return GitHub(os.getenv("GITHUB_TOKEN"))


def _ecr() -> Ecr:
    return Ecr()


def _parameters() -> Parameters:
    return Parameters()


def _or_exit(function, *args):
    try:
        return function(*args)
    except EXPECTED_ERRORS as error:
        typer.echo(f"error: {error}", err=True)
        raise typer.Exit(code=2) from error


def _sharing(
    services: ServiceRegistry, pushed: str, branch: str, branch_in: list[str], offline: bool
) -> frozenset[str]:
    if feature_name(branch) is None:
        return frozenset()
    if branch_in or offline:
        return frozenset(services.get(name).name for name in branch_in)
    github = _github()
    return frozenset(
        service.name
        for service in services.services
        if service.name != pushed and github.branch_exists(service.repo, branch)
    )


def _parse_shas(values: list[str]) -> dict[str, str]:
    shas = {}
    for value in values:
        name, separator, sha = value.partition("=")
        if not separator or not name or not sha:
            raise ValueError(f"--sha-for expects SERVICE=SHA, got {value!r}")
        shas[name] = sha
    return shas


def _shas(
    services: ServiceRegistry, plan: PreviewPlan, known: dict[str, str], offline: bool
) -> dict[str, str]:
    shas = {}
    for service in services.services:
        sha = known.get(service.name)
        if not sha and offline:
            raise ValueError(f"--offline needs --sha-for {service.name}=<sha>")
        shas[service.name] = sha or _github().head_sha(service.repo, plan.refs[service.name])
    return shas


def _existing_environments(target: Target, dry_run: bool) -> frozenset[str]:
    if dry_run:
        return frozenset()
    releases = subprocess.run(
        list_environments_command(target), check=True, capture_output=True, text=True
    ).stdout.split()
    return frozenset(release.removeprefix("preview-") for release in releases)


def _run(commands: list[Command], dry_run: bool) -> None:
    for command in commands:
        typer.echo(shlex.join(command))
        if dry_run:
            continue
        try:
            subprocess.run(command, check=True)
        except subprocess.CalledProcessError as error:
            typer.echo(
                f"error: {shlex.join(command)} failed with exit code {error.returncode}", err=True
            )
            raise typer.Exit(code=error.returncode) from error


@contextmanager
def _timed(command: str, report: Report, environment: str = "") -> Iterator[Stopwatch]:
    stopwatch = Stopwatch(command, environment)
    try:
        yield stopwatch
    finally:
        _publish_timings(stopwatch.record(), report)


def _publish_timings(record: dict, report: Report) -> None:
    typer.echo(json.dumps({"timings": record}))
    _write_outputs(report.github_output, {"timings": json.dumps(record)})
    if report.step_summary is not None:
        with report.step_summary.open("a") as file:
            file.write(summary_markdown(record))


def _deployed_release(plan: PreviewPlan, target: Target, dry_run: bool) -> DeployedRelease | None:
    if dry_run or not plan.joins_existing:
        return None
    status = subprocess.run(
        status_command(plan.release, target), check=False, capture_output=True, text=True
    )
    if status.returncode != 0:
        if "not found" in status.stderr:
            return None
        raise OSError(f"helm status {plan.release} failed: {status.stderr.strip()}")
    return parse_release(status.stdout)


def _dataset_version(explicit: str | None, parameters: Parameters | None) -> str:
    if explicit:
        return explicit
    if parameters is None:
        raise ValueError("--offline needs --dataset-version (no SSM lookup)")
    version = parameters.get(DATASET_VERSION_PARAMETER)
    if not version:
        raise LookupError(
            f"SSM parameter {DATASET_VERSION_PARAMETER} is not set: run the golden-image "
            "workflow or pass --dataset-version"
        )
    return version


def _golden_tag(dataset_version: str, ecr: Ecr | None) -> str | None:
    if ecr is None:
        return None
    image = ecr.find(GOLDEN_DB_REPOSITORY, dataset_version)
    if image is None:
        raise LookupError(
            f"{GOLDEN_DB_REPOSITORY}:{dataset_version} is not in ECR: run the golden-image "
            "workflow or pass another --dataset-version"
        )
    return image.reference


def _domain(explicit: str | None, parameters: Parameters | None) -> str | None:
    if explicit or parameters is None:
        return explicit
    return parameters.get(PREVIEW_DOMAIN_PARAMETER)


def _choose_images(
    services: ServiceRegistry,
    shas: dict[str, str],
    ecr: Ecr | None,
    deployed: DeployedRelease | None,
) -> dict[str, ImageChoice]:
    current = deployed.service_tags if deployed else {}
    return {
        service.name: choose_image(
            service.image, shas[service.name], ecr, current.get(service.name)
        )
        for service in services.services
    }


def _deploy(
    plan: PreviewPlan,
    services: ServiceRegistry,
    shas: dict[str, str],
    target: Target,
    options: DeployOptions,
    stopwatch: Stopwatch,
    pushed: str | None = None,
) -> None:
    with stopwatch.stage("images"):
        ecr = None if options.offline else _ecr()
        parameters = None if options.offline else _parameters()
        dataset_version = _or_exit(_dataset_version, options.dataset_version, parameters)
        deployed = _or_exit(_deployed_release, plan, target, options.dry_run)
        images = _or_exit(_choose_images, services, shas, ecr, deployed)
        golden_tag = _or_exit(_golden_tag, dataset_version, ecr)
        domain = _or_exit(_domain, options.domain, parameters)
    target = replace(target, registry=target.registry or (ecr.registry if ecr else None))
    tags = {name: choice.tag for name, choice in images.items()}
    if deployed and (recovery := recover_commands(plan.release, deployed, target)):
        with stopwatch.stage("recover"):
            _run(recovery, options.dry_run)
    dependencies, upgrade = up_commands(
        plan, services.services, tags, dataset_version, target, domain=domain, golden_tag=golden_tag
    )
    with stopwatch.stage("chart_dependencies"):
        _run([dependencies], options.dry_run)
    with stopwatch.stage("helm_upgrade"):
        _run([upgrade], options.dry_run)
    summary = _summary(plan, images, dataset_version, domain)
    typer.echo(json.dumps(summary, sort_keys=True))
    exact = pushed is None or images[pushed].source is ImageSource.SHA
    _write_outputs(
        options.report.github_output,
        {
            "environment": plan.environment,
            "exact-image": str(exact).lower(),
            "result": json.dumps(summary, sort_keys=True),
        },
    )


def _summary(
    plan: PreviewPlan, images: dict[str, ImageChoice], dataset_version: str, domain: str | None
) -> dict:
    return {
        "environment": plan.environment,
        "release": plan.release,
        "dataset_version": dataset_version,
        "images": {name: {"tag": c.tag, "source": c.source} for name, c in images.items()},
        "urls": (
            {name: f"https://{name}-{plan.environment}.{domain}" for name in images}
            if domain
            else {}
        ),
    }


def _write_outputs(github_output: Path | None, outputs: dict[str, str]) -> None:
    if github_output is None:
        return
    with github_output.open("a") as file:
        file.writelines(f"{name}={value}\n" for name, value in outputs.items())


def _plan_json(plan: PreviewPlan) -> str:
    return json.dumps(
        {
            "environment": plan.environment,
            "release": plan.release,
            "action": plan.action,
            "refs": plan.refs,
            "joins_existing": plan.joins_existing,
        },
        sort_keys=True,
    )


@env_app.command()
def resolve(
    repo: RepoOption,
    branch: BranchOption,
    branch_in: BranchInOption = None,
    offline: OfflineOption = False,
    services_file: ServicesFileOption = DEFAULT_SERVICES_FILE,
    github_output: GitHubOutputOption = None,
) -> None:
    """Print the preview environment plan for a push as JSON."""
    services = _or_exit(ServiceRegistry.load, services_file)
    pushed = _or_exit(services.by_repo, repo).name
    sharing = _or_exit(_sharing, services, pushed, branch, branch_in or [], offline)
    plan = _or_exit(resolve_push, services.names, pushed, branch, sharing)
    typer.echo(_plan_json(plan))
    _write_outputs(github_output, {"environment": plan.environment})


@env_app.command()
def up(
    repo: RepoOption,
    branch: BranchOption,
    sha: Annotated[str, typer.Option("--sha", help="Pushed commit")],
    dataset_version: DatasetOption = None,
    domain: DomainOption = None,
    branch_in: BranchInOption = None,
    offline: OfflineOption = False,
    sha_for: ShaForOption = None,
    services_file: ServicesFileOption = DEFAULT_SERVICES_FILE,
    context: ContextOption = None,
    chart: ChartOption = "charts/services",
    registry: RegistryOption = None,
    github_output: GitHubOutputOption = None,
    step_summary: StepSummaryOption = None,
    dry_run: DryRun = False,
) -> None:
    """Create or update the preview environment for a pushed branch."""
    target = Target(chart=chart, context=context, registry=registry)
    report = Report(github_output, step_summary)
    options = DeployOptions(dataset_version, domain, offline, dry_run, report)
    with _timed("up", report) as stopwatch:
        with stopwatch.stage("resolve"):
            services = _or_exit(ServiceRegistry.load, services_file)
            pushed = _or_exit(services.by_repo, repo).name
            sharing = _or_exit(_sharing, services, pushed, branch, branch_in or [], offline)
            existing = _existing_environments(target, dry_run)
            plan = _or_exit(resolve_push, services.names, pushed, branch, sharing, existing)
            known = {**_or_exit(_parse_shas, sha_for or []), pushed: sha}
            shas = _or_exit(_shas, services, plan, known, offline)
        stopwatch.environment = plan.environment
        _deploy(plan, services, shas, target, options, stopwatch, pushed)


@env_app.command()
def down(
    repo: RepoOption,
    branch: BranchOption,
    dataset_version: DatasetOption = None,
    domain: DomainOption = None,
    branch_in: BranchInOption = None,
    offline: OfflineOption = False,
    sha_for: ShaForOption = None,
    services_file: ServicesFileOption = DEFAULT_SERVICES_FILE,
    context: ContextOption = None,
    chart: ChartOption = "charts/services",
    registry: RegistryOption = None,
    github_output: GitHubOutputOption = None,
    step_summary: StepSummaryOption = None,
    dry_run: DryRun = False,
) -> None:
    """Tear down the preview environment for a deleted branch, or redeploy it if kept elsewhere."""
    target = Target(chart=chart, context=context, registry=registry)
    report = Report(github_output, step_summary)
    options = DeployOptions(dataset_version, domain, offline, dry_run, report)
    with _timed("down", report) as stopwatch:
        with stopwatch.stage("resolve"):
            services = _or_exit(ServiceRegistry.load, services_file)
            deleted = _or_exit(services.by_repo, repo).name
            sharing = _or_exit(_sharing, services, deleted, branch, branch_in or [], offline)
            plan = _or_exit(resolve_delete, services.names, deleted, branch, sharing)
        stopwatch.environment = plan.environment
        if plan.action is Action.DOWN:
            with stopwatch.stage("teardown"):
                _run(down_commands(plan.environment, target), dry_run)
            return
        shas = _or_exit(_shas, services, plan, _or_exit(_parse_shas, sha_for or []), offline)
        _deploy(plan, services, shas, target, options, stopwatch)


@env_app.command()
def reset(
    environment: Annotated[str, typer.Option("--name")],
    services_file: ServicesFileOption = DEFAULT_SERVICES_FILE,
    context: ContextOption = None,
    github_output: GitHubOutputOption = None,
    step_summary: StepSummaryOption = None,
    dry_run: DryRun = False,
) -> None:
    """Restart the preview environment database to return it to golden data."""
    services = _or_exit(ServiceRegistry.load, services_file)
    _or_exit(validate_environment_name, environment, services.names)
    report = Report(github_output, step_summary)
    with _timed("reset", report, environment) as stopwatch, stopwatch.stage("reset"):
        _run(reset_commands(environment, Target(context=context)), dry_run)


@env_app.command("test")
def test_environment(
    environment: Annotated[str, typer.Option("--name")],
    services_file: ServicesFileOption = DEFAULT_SERVICES_FILE,
    context: ContextOption = None,
    github_output: GitHubOutputOption = None,
    step_summary: StepSummaryOption = None,
    dry_run: DryRun = False,
) -> None:
    """Run the preview environment's E2E suite (Helm test hooks) and stream its logs."""
    services = _or_exit(ServiceRegistry.load, services_file)
    _or_exit(validate_environment_name, environment, services.names)
    report = Report(github_output, step_summary)
    with _timed("test", report, environment) as stopwatch, stopwatch.stage("e2e"):
        _run(helm_test_commands(environment, Target(context=context)), dry_run)
