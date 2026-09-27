import aws_cdk as cdk

from caldera.platform import build_platform

EXPECTED_STACKS = [
    "CalderaNetwork",
    "CalderaCluster",
    "CalderaRegistry",
    "CalderaDns",
    "CalderaCiAccess",
    "CalderaAddons",
]


def test_platform_defines_exactly_the_eks_design_stacks() -> None:
    stacks = build_platform(cdk.App())

    assert [stack.stack_name for stack in stacks.values()] == EXPECTED_STACKS


def test_stack_dependencies_follow_the_deploy_order() -> None:
    stacks = build_platform(cdk.App())

    assert stacks["Network"] in stacks["Cluster"].dependencies
    assert {stacks["Cluster"], stacks["Dns"]} <= set(stacks["Addons"].dependencies)
    assert {stacks["Cluster"], stacks["Registry"]} <= set(stacks["CiAccess"].dependencies)


def test_platform_synthesizes() -> None:
    app = cdk.App()
    build_platform(app)

    assembly = app.synth()

    assert {stack.stack_name for stack in assembly.stacks} == set(EXPECTED_STACKS)
