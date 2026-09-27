import json
import os
import shlex
import subprocess
from typing import Annotated

import typer

from caldera_cli.commands import Command, Target, down_commands, reset_commands, up_commands
from caldera_cli.github import GitHub
from caldera_cli.resolver import (
    Action,
    Service,
    VentPlan,
    feature_name,
    resolve_delete,
    resolve_push,
    validate_vent_name,
)

app = typer.Typer(no_args_is_help=True)
vent_app = typer.Typer(no_args_is_help=True, help="Create, update, cool and reset vents.")
app.add_typer(vent_app, name="vent")

RepoOption = Annotated[str, typer.Option("--repo", help="Service repo, e.g. tremor-api")]
BranchOption = Annotated[str, typer.Option("--branch")]
OtherHasBranch = Annotated[
    bool | None,
    typer.Option(
        "--other-has-branch/--other-missing-branch",
        help="Skip the GitHub lookup for the same branch in the other repo",
    ),
]
OtherShaOption = Annotated[
    str | None, typer.Option("--other-sha", help="Commit of the other service's ref")
]
DryRun = Annotated[bool, typer.Option("--dry-run", help="Print commands without running them")]
ContextOption = Annotated[str | None, typer.Option("--context", help="kubeconfig context")]
ChartOption = Annotated[str, typer.Option("--chart")]
RegistryOption = Annotated[
    str | None, typer.Option("--registry", envvar="CALDERA_REGISTRY", help="ECR registry host")
]
DatasetOption = Annotated[str, typer.Option("--dataset-version", envvar="CALDERA_DATASET_VERSION")]


def _github() -> GitHub:
    return GitHub(os.getenv("GITHUB_TOKEN"))


def _other_has_branch(service: Service, branch: str, given: bool | None) -> bool:
    if given is not None or feature_name(branch) is None:
        return bool(given)
    return _github().branch_exists(service.other.repo, branch)


def _existing_vents(target: Target, dry_run: bool) -> frozenset[str]:
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
    return frozenset(name.removeprefix("vent-") for name in names)


def _shas(plan: VentPlan, known: dict[Service, str | None]) -> dict[Service, str]:
    return {
        service: known.get(service) or _github().head_sha(service.repo, plan.refs[service])
        for service in Service
    }


def _run(commands: list[Command], dry_run: bool) -> None:
    for command in commands:
        typer.echo(shlex.join(command))
        if not dry_run:
            subprocess.run(command, check=True)


def _plan_json(plan: VentPlan) -> str:
    return json.dumps(
        {
            "vent": plan.vent,
            "release": plan.release,
            "action": plan.action,
            "refs": {str(service): ref for service, ref in plan.refs.items()},
            "joins_existing": plan.joins_existing,
        },
        sort_keys=True,
    )


def _resolve_or_exit(function, *args):
    try:
        return function(*args)
    except ValueError as error:
        typer.echo(f"error: {error}", err=True)
        raise typer.Exit(code=2) from error


@vent_app.command()
def resolve(
    repo: RepoOption, branch: BranchOption, other_has_branch: OtherHasBranch = None
) -> None:
    """Print the vent plan for a push as JSON."""
    service = _resolve_or_exit(Service.from_repo, repo)
    has_branch = _other_has_branch(service, branch, other_has_branch)
    typer.echo(_plan_json(_resolve_or_exit(resolve_push, service, branch, has_branch)))


@vent_app.command()
def up(
    repo: RepoOption,
    branch: BranchOption,
    sha: Annotated[str, typer.Option("--sha", help="Pushed commit")],
    dataset_version: DatasetOption = "latest",
    other_has_branch: OtherHasBranch = None,
    other_sha: OtherShaOption = None,
    context: ContextOption = None,
    chart: ChartOption = "charts/vent",
    registry: RegistryOption = None,
    dry_run: DryRun = False,
) -> None:
    """Create or update the vent for a pushed branch."""
    target = Target(chart=chart, context=context, registry=registry)
    service = _resolve_or_exit(Service.from_repo, repo)
    has_branch = _other_has_branch(service, branch, other_has_branch)
    plan = _resolve_or_exit(
        resolve_push, service, branch, has_branch, _existing_vents(target, dry_run)
    )
    shas = _shas(plan, {service: sha, service.other: other_sha})
    _run(up_commands(plan, shas, dataset_version, target), dry_run)


@vent_app.command()
def down(
    repo: RepoOption,
    branch: BranchOption,
    dataset_version: DatasetOption = "latest",
    other_has_branch: OtherHasBranch = None,
    main_sha: Annotated[
        str | None, typer.Option("--main-sha", help="Commit of main in the deleted branch's repo")
    ] = None,
    other_sha: OtherShaOption = None,
    context: ContextOption = None,
    chart: ChartOption = "charts/vent",
    registry: RegistryOption = None,
    dry_run: DryRun = False,
) -> None:
    """Cool the vent for a deleted branch, or redeploy it on main if the other repo keeps it."""
    target = Target(chart=chart, context=context, registry=registry)
    service = _resolve_or_exit(Service.from_repo, repo)
    has_branch = _other_has_branch(service, branch, other_has_branch)
    plan = _resolve_or_exit(resolve_delete, service, branch, has_branch)
    if plan.action is Action.DOWN:
        _run(down_commands(plan.vent, target), dry_run)
        return
    shas = _shas(plan, {service: main_sha, service.other: other_sha})
    _run(up_commands(plan, shas, dataset_version, target), dry_run)


@vent_app.command()
def reset(
    vent: Annotated[str, typer.Option("--vent")],
    context: ContextOption = None,
    dry_run: DryRun = False,
) -> None:
    """Restart the vent database to return it to golden data."""
    _resolve_or_exit(validate_vent_name, vent)
    _run(reset_commands(vent, Target(context=context)), dry_run)
