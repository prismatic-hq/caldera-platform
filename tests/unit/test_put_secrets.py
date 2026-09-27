from collections.abc import Iterator
from pathlib import Path

import boto3
import pytest
from botocore.stub import Stubber

from scripts.onepassword import OpError
from scripts.put_secrets import main, put_github_app

PRIVATE_KEY = "-----BEGIN RSA PRIVATE KEY-----\nMIIE\n-----END RSA PRIVATE KEY-----\n"


@pytest.fixture
def ssm() -> Iterator[tuple[object, Stubber]]:
    client = boto3.Session(
        region_name="us-east-1", aws_access_key_id="test", aws_secret_access_key="test"
    ).client("ssm")
    with Stubber(client) as stubber:
        yield client, stubber
        stubber.assert_no_pending_responses()


def expect_put(stubber: Stubber, name: str, value: str) -> None:
    stubber.add_response(
        "put_parameter",
        {"Version": 1},
        {"Name": name, "Value": value, "Type": "SecureString", "Overwrite": True},
    )


def test_writes_the_three_parameters_external_secrets_reads(ssm) -> None:
    client, stubber = ssm
    expect_put(stubber, "/prismatic/github-app/github-app-id", "123456")
    expect_put(stubber, "/prismatic/github-app/github-app-installation-id", "7890")
    expect_put(stubber, "/prismatic/github-app/github-app-private-key", PRIVATE_KEY)

    written = put_github_app(
        client, app_id="123456", installation_id="7890", private_key=PRIVATE_KEY
    )

    assert written == [
        "/prismatic/github-app/github-app-id",
        "/prismatic/github-app/github-app-installation-id",
        "/prismatic/github-app/github-app-private-key",
    ]


@pytest.mark.parametrize(
    ("app_id", "installation_id", "private_key", "message"),
    [
        pytest.param("abc", "7890", PRIVATE_KEY, "app id must be numeric", id="non-numeric app id"),
        pytest.param(
            "123", "", PRIVATE_KEY, "installation id must be numeric", id="empty installation"
        ),
        pytest.param("123", "7890", "not a key", "not a PEM private key", id="bad private key"),
    ],
)
def test_rejects_invalid_input_before_writing(
    ssm, app_id: str, installation_id: str, private_key: str, message: str
) -> None:
    client, _ = ssm

    with pytest.raises(ValueError, match=message):
        put_github_app(
            client, app_id=app_id, installation_id=installation_id, private_key=private_key
        )


ITEM = "op://Prismatic/prismatic-hq GitHub App"
OP_VALUES = {
    f"{ITEM}/app_id": "123456\n",
    f"{ITEM}/installation_id": "7890\n",
    f"{ITEM}/pem": PRIVATE_KEY,
}


class FakeOp:
    def __init__(self, values: dict[str, str]) -> None:
        self.values = values
        self.references: list[str] = []

    def __call__(self, reference: str) -> str:
        self.references.append(reference)
        return self.values[reference]


class RecordingPut:
    def __init__(self) -> None:
        self.calls: list[dict[str, str]] = []

    def __call__(self, ssm, **values: str) -> list[str]:
        self.calls.append(values)
        return ["/prismatic/github-app/github-app-id"]


def test_main_reads_the_app_from_1password_by_default(monkeypatch, capsys) -> None:
    op = FakeOp(OP_VALUES)
    put = RecordingPut()
    monkeypatch.setattr("scripts.put_secrets.read_op", op)
    monkeypatch.setattr("scripts.put_secrets.put_github_app", put)

    assert main(["--region", "us-east-2"]) == 0
    assert op.references == [f"{ITEM}/app_id", f"{ITEM}/installation_id", f"{ITEM}/pem"]
    assert put.calls == [
        {"app_id": "123456", "installation_id": "7890", "private_key": PRIVATE_KEY}
    ]
    output = capsys.readouterr()
    assert PRIVATE_KEY not in output.out + output.err


def test_flags_bypass_1password(monkeypatch, tmp_path: Path) -> None:
    key = tmp_path / "app.pem"
    key.write_text(PRIVATE_KEY)
    op = FakeOp({})
    put = RecordingPut()
    monkeypatch.setattr("scripts.put_secrets.read_op", op)
    monkeypatch.setattr("scripts.put_secrets.put_github_app", put)

    args = ["--app-id", "1", "--installation-id", "2", "--private-key-file", str(key)]
    assert main([*args, "--region", "us-east-2"]) == 0
    assert op.references == []
    assert put.calls == [{"app_id": "1", "installation_id": "2", "private_key": PRIVATE_KEY}]


def test_vault_and_item_are_overridable(monkeypatch) -> None:
    op = FakeOp({k.replace(ITEM, "op://Ops/App"): v for k, v in OP_VALUES.items()})
    monkeypatch.setattr("scripts.put_secrets.read_op", op)
    monkeypatch.setattr("scripts.put_secrets.put_github_app", RecordingPut())

    assert main(["--region", "us-east-2", "--op-vault", "Ops", "--op-item", "App"]) == 0
    assert op.references[0] == "op://Ops/App/app_id"


def test_1password_errors_are_actionable(monkeypatch, capsys) -> None:
    def signed_out(reference: str) -> str:
        raise OpError(f"op read failed for {reference}: not signed in. Run `op signin`")

    monkeypatch.setattr("scripts.put_secrets.read_op", signed_out)

    assert main(["--region", "us-east-2"]) == 2
    assert "op signin" in capsys.readouterr().err


def test_cli_reports_a_missing_private_key_file(tmp_path: Path, capsys) -> None:
    missing = tmp_path / "app.pem"

    code = main(["--app-id", "1", "--installation-id", "2", "--private-key-file", str(missing)])

    assert code == 2
    assert f"private key file not found: {missing}" in capsys.readouterr().err
