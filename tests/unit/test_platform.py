import aws_cdk as cdk

from caldera_platform.platform import build_platform

EXPECTED_STACKS = [
    "CalderaNetwork",
    "CalderaCluster",
    "CalderaData",
    "CalderaDns",
    "CalderaRegistry",
    "CalderaCiAccess",
    "CalderaAddonIdentity",
    "CalderaGitOpsBridge",
]


def test_platform_defines_all_stacks_in_bridge_order() -> None:
    stacks = build_platform(cdk.App())

    assert [stack.stack_name for stack in stacks] == EXPECTED_STACKS


def test_each_stack_depends_on_the_previous_one() -> None:
    stacks = build_platform(cdk.App())

    for previous, current in zip(stacks, stacks[1:], strict=False):
        assert previous in current.dependencies


def test_platform_synthesizes() -> None:
    app = cdk.App()
    build_platform(app)

    assembly = app.synth()

    assert {stack.stack_name for stack in assembly.stacks} == set(EXPECTED_STACKS)
