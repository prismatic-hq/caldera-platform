from collections.abc import Iterator

import boto3
import pytest
from botocore.stub import Stubber

from cdk.stacks.ci_access import push_role_output
from scripts.put_role_arns import main, role_arn_settings

STACK = "CalderaCiAccess"
TREMOR_ARN = "arn:aws:iam::117051123612:role/CalderaCiAccess-Pushtremorapi026E0BE2-9lkqFk8ImMYr"
STEWARD_ARN = "arn:aws:iam::117051123612:role/CalderaCiAccess-PushstewardapiF656CAE1-Rbdtj6cua6JT"


class RecordingGh:
    def __init__(self) -> None:
        self.calls: list[tuple[list[str], str]] = []

    def __call__(self, args: list[str], value: str) -> None:
        self.calls.append((args, value))


@pytest.fixture
def cloudformation() -> Iterator[tuple[object, Stubber]]:
    client = boto3.Session(
        region_name="us-east-2", aws_access_key_id="test", aws_secret_access_key="test"
    ).client("cloudformation")
    with Stubber(client) as stubber:
        yield client, stubber
        stubber.assert_no_pending_responses()


def expect_outputs(stubber: Stubber, outputs: dict[str, str], stack: str = STACK) -> None:
    stubber.add_response(
        "describe_stacks",
        {
            "Stacks": [
                {
                    "StackName": stack,
                    "CreationTime": "2026-09-27T00:00:00Z",
                    "StackStatus": "UPDATE_COMPLETE",
                    "Outputs": [{"OutputKey": k, "OutputValue": v} for k, v in outputs.items()],
                }
            ]
        },
        {"StackName": stack},
    )


def test_output_keys_are_cloudformation_safe_names_per_repo() -> None:
    assert push_role_output("tremor-api") == "TremorApiPushRoleArn"
    assert push_role_output("steward-api") == "StewardApiPushRoleArn"


def test_each_repo_gets_its_own_push_role_arn(cloudformation) -> None:
    client, stubber = cloudformation
    expect_outputs(
        stubber,
        {
            push_role_output("tremor-api"): TREMOR_ARN,
            push_role_output("steward-api"): STEWARD_ARN,
        },
    )

    settings = role_arn_settings(client, STACK, ["tremor-api", "steward-api"])

    assert {repo: (s.kind, s.name, s.value) for repo, s in settings.items()} == {
        "tremor-api": ("variable", "AWS_ROLE_ARN", TREMOR_ARN),
        "steward-api": ("variable", "AWS_ROLE_ARN", STEWARD_ARN),
    }


def test_missing_output_names_the_stack_and_the_fix(cloudformation) -> None:
    client, stubber = cloudformation
    expect_outputs(stubber, {push_role_output("tremor-api"): TREMOR_ARN})

    with pytest.raises(LookupError, match=f"{STACK} has no StewardApiPushRoleArn output.*deploy"):
        role_arn_settings(client, STACK, ["tremor-api", "steward-api"])


def test_missing_stack_is_actionable(cloudformation) -> None:
    client, stubber = cloudformation
    stubber.add_client_error(
        "describe_stacks", "ValidationError", f"Stack with id {STACK} does not exist"
    )

    with pytest.raises(LookupError, match=f"cannot read stack {STACK}"):
        role_arn_settings(client, STACK, ["tremor-api"])


def test_rejects_an_output_that_is_not_a_role_arn(cloudformation) -> None:
    client, stubber = cloudformation
    expect_outputs(stubber, {push_role_output("tremor-api"): "not-an-arn"})

    with pytest.raises(ValueError, match="not an IAM role ARN"):
        role_arn_settings(client, STACK, ["tremor-api"])


def test_main_sets_the_variable_on_every_service_repo(cloudformation, monkeypatch, capsys) -> None:
    client, stubber = cloudformation
    expect_outputs(
        stubber,
        {
            push_role_output("tremor-api"): TREMOR_ARN,
            push_role_output("steward-api"): STEWARD_ARN,
        },
    )
    gh = RecordingGh()
    monkeypatch.setattr("scripts.put_role_arns.boto3.client", lambda service: client)
    monkeypatch.setattr("scripts.put_role_arns.run_gh", gh)

    assert main([]) == 0
    assert sorted(gh.calls) == [
        (["variable", "set", "AWS_ROLE_ARN", "--repo", "prismatic-hq/steward-api"], STEWARD_ARN),
        (["variable", "set", "AWS_ROLE_ARN", "--repo", "prismatic-hq/tremor-api"], TREMOR_ARN),
    ]
    assert "prismatic-hq/tremor-api: variable AWS_ROLE_ARN" in capsys.readouterr().out


def test_main_writes_nothing_when_an_output_is_missing(cloudformation, monkeypatch, capsys) -> None:
    client, stubber = cloudformation
    expect_outputs(stubber, {push_role_output("tremor-api"): TREMOR_ARN})
    gh = RecordingGh()
    monkeypatch.setattr("scripts.put_role_arns.boto3.client", lambda service: client)
    monkeypatch.setattr("scripts.put_role_arns.run_gh", gh)

    assert main([]) == 2
    assert gh.calls == []
    assert "StewardApiPushRoleArn" in capsys.readouterr().err


def test_stack_name_is_overridable(cloudformation, monkeypatch) -> None:
    client, stubber = cloudformation
    expect_outputs(
        stubber,
        {
            push_role_output("tremor-api"): TREMOR_ARN,
            push_role_output("steward-api"): STEWARD_ARN,
        },
        stack="DevCiAccess",
    )
    monkeypatch.setattr("scripts.put_role_arns.boto3.client", lambda service: client)
    monkeypatch.setattr("scripts.put_role_arns.run_gh", RecordingGh())

    assert main(["--stack", "DevCiAccess"]) == 0
