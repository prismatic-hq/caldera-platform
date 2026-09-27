import json
import os
import shlex
import subprocess
from pathlib import Path
from typing import Annotated

import typer

from preview_cli.commands import Command, Target, down_commands, reset_commands, up_commands
from preview_cli.github import GitHub
from preview_cli.registry import ServiceRegistry
from preview_cli.resolver import (
    Action,
    PreviewPlan,
    feature_name,
    resolve_delete,
    resolve_push,
    validate_environment_name,
)

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
    bool, typer.Option("--offline", help="Make no GitHub calls; pass --branch-in and --sha-for")
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
DatasetOption = Annotated[str, typer.Option("--dataset-version", envvar="PREVIEW_DATASET_VERSION")]
DEFAULT_SERVICES_FILE = Path("services.yaml")


def _github() -> GitHub:
    return GitHub(os.getenv("GITHUB_TOKEN"))


def _or_exit(function, *args):
    try:
        return function(*args)
    except (ValueError, OSError) as error:
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
    command = [
        "kubectl",
        "get",
        "namespaces",
        "-l",
        "app.kubernetes.io/part-of=vent",
        "-o",
        "jsonpath={.items[*].metadata.name}",
    ]
    if target.context:
        command += ["--context", target.context]
    names = subprocess.run(command, check=True, capture_output=True, text=True).stdout.split()
    return frozenset(name.removeprefix("preview-") for name in names)


def _run(commands: list[Command], dry_run: bool) -> None:
    for command in commands:
        typer.echo(shlex.join(command))
        if not dry_run:
            subprocess.run(command, check=True)


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
) -> None:
    """Print the preview environment plan for a push as JSON."""
    services = _or_exit(ServiceRegistry.load, services_file)
    pushed = _or_exit(services.by_repo, repo).name
    sharing = _or_exit(_sharing, services, pushed, branch, branch_in or [], offline)
    plan = _or_exit(resolve_push, services.names, pushed, branch, sharing)
    typer.echo(_plan_json(plan))


@env_app.command()
def up(
    repo: RepoOption,
    branch: BranchOption,
    sha: Annotated[str, typer.Option("--sha", help="Pushed commit")],
    dataset_version: DatasetOption = "latest",
    branch_in: BranchInOption = None,
    offline: OfflineOption = False,
    sha_for: ShaForOption = None,
    services_file: ServicesFileOption = DEFAULT_SERVICES_FILE,
    context: ContextOption = None,
    chart: ChartOption = "charts/vent",
    registry: RegistryOption = None,
    dry_run: DryRun = False,
) -> None:
    """Create or update the preview environment for a pushed branch."""
    target = Target(chart=chart, context=context, registry=registry)
    services = _or_exit(ServiceRegistry.load, services_file)
    pushed = _or_exit(services.by_repo, repo).name
    sharing = _or_exit(_sharing, services, pushed, branch, branch_in or [], offline)
    existing = _existing_environments(target, dry_run)
    plan = _or_exit(resolve_push, services.names, pushed, branch, sharing, existing)
    known = {**_or_exit(_parse_shas, sha_for or []), pushed: sha}
    shas = _or_exit(_shas, services, plan, known, offline)
    _run(up_commands(plan, services.services, shas, dataset_version, target), dry_run)


@env_app.command()
def down(
    repo: RepoOption,
    branch: BranchOption,
    dataset_version: DatasetOption = "latest",
    branch_in: BranchInOption = None,
    offline: OfflineOption = False,
    sha_for: ShaForOption = None,
    services_file: ServicesFileOption = DEFAULT_SERVICES_FILE,
    context: ContextOption = None,
    chart: ChartOption = "charts/vent",
    registry: RegistryOption = None,
    dry_run: DryRun = False,
) -> None:
    """Tear down the preview environment for a deleted branch, or redeploy it if kept elsewhere."""
    target = Target(chart=chart, context=context, registry=registry)
    services = _or_exit(ServiceRegistry.load, services_file)
    deleted = _or_exit(services.by_repo, repo).name
    sharing = _or_exit(_sharing, services, deleted, branch, branch_in or [], offline)
    plan = _or_exit(resolve_delete, services.names, deleted, branch, sharing)
    if plan.action is Action.DOWN:
        _run(down_commands(plan.environment, target), dry_run)
        return
    shas = _or_exit(_shas, services, plan, _or_exit(_parse_shas, sha_for or []), offline)
    _run(up_commands(plan, services.services, shas, dataset_version, target), dry_run)


@env_app.command()
def reset(
    environment: Annotated[str, typer.Option("--name")],
    services_file: ServicesFileOption = DEFAULT_SERVICES_FILE,
    context: ContextOption = None,
    dry_run: DryRun = False,
) -> None:
    """Restart the preview environment database to return it to golden data."""
    services = _or_exit(ServiceRegistry.load, services_file)
    _or_exit(validate_environment_name, environment, services.names)
    _run(reset_commands(environment, Target(context=context)), dry_run)
