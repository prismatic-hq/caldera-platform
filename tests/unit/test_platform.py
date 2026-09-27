import compileall
from pathlib import Path

from cdk.constructs.cleanup import HANDLERS_DIR
from cdk.platform import build_platform

EXPECTED_STACKS = [
    "CalderaNetwork",
    "CalderaCluster",
    "CalderaRegistry",
    "CalderaDns",
    "CalderaCiAccess",
    "CalderaAddons",
]


def test_platform_defines_exactly_the_eks_design_stacks(new_app) -> None:
    stacks = build_platform(new_app())

    assert [stack.stack_name for stack in stacks.values()] == EXPECTED_STACKS


def test_stack_dependencies_follow_the_deploy_order(new_app) -> None:
    stacks = build_platform(new_app())

    assert stacks["Network"] in stacks["Cluster"].dependencies
    assert {stacks["Cluster"], stacks["Dns"]} <= set(stacks["Addons"].dependencies)
    assert {stacks["Cluster"], stacks["Registry"]} <= set(stacks["CiAccess"].dependencies)


def test_platform_synthesizes(new_app) -> None:
    app = new_app()
    build_platform(app)

    assembly = app.synth()

    assert {stack.stack_name for stack in assembly.stacks} == set(EXPECTED_STACKS)


def test_lambda_assets_leave_out_bytecode_caches(new_app, tmp_path: Path) -> None:
    compileall.compile_dir(HANDLERS_DIR, quiet=1)
    app = new_app(outdir=str(tmp_path))
    build_platform(app)

    app.synth()

    assert list(tmp_path.glob("asset.*"))
    assert not list(tmp_path.glob("asset.*/**/__pycache__"))
