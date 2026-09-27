import subprocess
from pathlib import Path

import pytest

from scripts.put_github_secrets import (
    GitHubSetting,
    OpError,
    main,
    put_github_settings,
    read_op,
    settings_for,
)

PRIVATE_KEY = "-----BEGIN RSA PRIVATE KEY-----\nMIIE\n-----END RSA PRIVATE KEY-----\n"
CLIENT_ID = "Iv23liAbCdEf123456"


class RecordingGh:
    def __init__(self, fail_on: str | None = None) -> None:
        self.calls: list[tuple[list[str], str]] = []
        self.fail_on = fail_on

    def __call__(self, args: list[str], value: str) -> None:
        self.calls.append((args, value))
        if self.fail_on and self.fail_on in args:
            raise subprocess.CalledProcessError(1, ["gh", *args], stderr="HTTP 404: Not Found")


def test_every_repo_gets_both_app_secrets_and_the_region_variable() -> None:
    gh = RecordingGh()
    settings = settings_for(client_id=CLIENT_ID, private_key=PRIVATE_KEY, region="us-east-2")

    written = put_github_settings(gh, "prismatic-hq", ["caldera-platform", "tremor-api"], settings)

    assert written == [
        "prismatic-hq/caldera-platform: secret CALDERA_APP_CLIENT_ID",
        "prismatic-hq/caldera-platform: secret CALDERA_APP_PRIVATE_KEY",
        "prismatic-hq/caldera-platform: variable AWS_REGION",
        "prismatic-hq/tremor-api: secret CALDERA_APP_CLIENT_ID",
        "prismatic-hq/tremor-api: secret CALDERA_APP_PRIVATE_KEY",
        "prismatic-hq/tremor-api: variable AWS_REGION",
    ]
    assert gh.calls[1] == (
        ["secret", "set", "CALDERA_APP_PRIVATE_KEY", "--repo", "prismatic-hq/caldera-platform"],
        PRIVATE_KEY,
    )
    assert gh.calls[2] == (
        ["variable", "set", "AWS_REGION", "--repo", "prismatic-hq/caldera-platform"],
        "us-east-2",
    )


def test_secret_values_never_appear_in_the_command_line() -> None:
    gh = RecordingGh()
    settings = settings_for(client_id=CLIENT_ID, private_key=PRIVATE_KEY, region="us-east-2")

    put_github_settings(gh, "prismatic-hq", ["tremor-api"], settings)

    for args, _ in gh.calls:
        assert CLIENT_ID not in args
        assert PRIVATE_KEY not in args


@pytest.mark.parametrize(
    ("client_id", "private_key", "region", "message"),
    [
        ("123456", PRIVATE_KEY, "us-east-2", "client id"),
        (CLIENT_ID, "not a key", "us-east-2", "private key"),
        (CLIENT_ID, PRIVATE_KEY, "", "region"),
        (CLIENT_ID, PRIVATE_KEY, "us east 2", "region"),
    ],
)
def test_rejects_invalid_values_before_writing_anything(
    client_id: str, private_key: str, region: str, message: str
) -> None:
    with pytest.raises(ValueError, match=message):
        settings_for(client_id=client_id, private_key=private_key, region=region)


def test_a_failed_write_names_the_repo_and_setting() -> None:
    gh = RecordingGh(fail_on="prismatic-hq/tremor-api")
    settings = settings_for(client_id=CLIENT_ID, private_key=PRIVATE_KEY, region="us-east-2")

    with pytest.raises(RuntimeError, match="prismatic-hq/tremor-api: secret CALDERA_APP_CLIENT_ID"):
        put_github_settings(gh, "prismatic-hq", ["caldera-platform", "tremor-api"], settings)


def test_main_writes_platform_and_service_repos(tmp_path: Path, monkeypatch, capsys) -> None:
    key = tmp_path / "app.pem"
    key.write_text(PRIVATE_KEY)
    gh = RecordingGh()
    monkeypatch.setattr("scripts.put_github_secrets.run_gh", gh)

    code = main(["--client-id", CLIENT_ID, "--private-key-file", str(key), "--region", "us-east-2"])

    assert code == 0
    repos = {args[-1] for args, _ in gh.calls}
    assert repos == {
        "prismatic-hq/caldera-platform",
        "prismatic-hq/tremor-api",
        "prismatic-hq/steward-api",
    }
    assert "prismatic-hq/steward-api: variable AWS_REGION" in capsys.readouterr().out


def test_main_rejects_a_missing_key_file(tmp_path: Path, capsys) -> None:
    code = main(
        [
            "--client-id",
            CLIENT_ID,
            "--private-key-file",
            str(tmp_path / "x.pem"),
            "--region",
            "us-east-2",
        ]
    )

    assert code == 2
    assert "private key file not found" in capsys.readouterr().err


def test_setting_describes_itself() -> None:
    assert GitHubSetting("secret", "X", "v").label == "secret X"


class FakeOp:
    def __init__(self, values: dict[str, str]) -> None:
        self.values = values
        self.references: list[str] = []

    def __call__(self, reference: str) -> str:
        self.references.append(reference)
        return self.values[reference]


ITEM = "op://Prismatic/prismatic-hq GitHub App"


def test_main_reads_the_app_from_1password_by_default(monkeypatch, capsys) -> None:
    op = FakeOp({f"{ITEM}/client_id": CLIENT_ID, f"{ITEM}/pem": PRIVATE_KEY})
    gh = RecordingGh()
    monkeypatch.setattr("scripts.put_github_secrets.read_op", op)
    monkeypatch.setattr("scripts.put_github_secrets.run_gh", gh)

    code = main(["--region", "us-east-2"])

    assert code == 0
    assert op.references == [f"{ITEM}/client_id", f"{ITEM}/pem"]
    assert (
        ["secret", "set", "CALDERA_APP_PRIVATE_KEY", "--repo", "prismatic-hq/tremor-api"],
        PRIVATE_KEY,
    ) in gh.calls
    output = capsys.readouterr()
    assert PRIVATE_KEY not in output.out + output.err
    assert CLIENT_ID not in output.out + output.err


def test_vault_and_item_are_overridable(monkeypatch) -> None:
    op = FakeOp({"op://Ops/App/client_id": CLIENT_ID, "op://Ops/App/pem": PRIVATE_KEY})
    monkeypatch.setattr("scripts.put_github_secrets.read_op", op)
    monkeypatch.setattr("scripts.put_github_secrets.run_gh", RecordingGh())

    assert main(["--region", "us-east-2", "--op-vault", "Ops", "--op-item", "App"]) == 0
    assert op.references == ["op://Ops/App/client_id", "op://Ops/App/pem"]


def test_region_defaults_from_the_environment(monkeypatch) -> None:
    op = FakeOp({f"{ITEM}/client_id": CLIENT_ID, f"{ITEM}/pem": PRIVATE_KEY})
    gh = RecordingGh()
    monkeypatch.setattr("scripts.put_github_secrets.read_op", op)
    monkeypatch.setattr("scripts.put_github_secrets.run_gh", gh)
    monkeypatch.setenv("AWS_REGION", "us-west-2")

    assert main([]) == 0
    assert (
        ["variable", "set", "AWS_REGION", "--repo", "prismatic-hq/caldera-platform"],
        "us-west-2",
    ) in gh.calls


def test_missing_region_is_an_input_error(monkeypatch, capsys) -> None:
    monkeypatch.delenv("AWS_REGION", raising=False)
    monkeypatch.setattr("scripts.put_github_secrets.read_op", FakeOp({}))

    assert main([]) == 2
    assert "--region" in capsys.readouterr().err


def test_1password_errors_are_actionable(monkeypatch, capsys) -> None:
    def signed_out(reference: str) -> str:
        raise OpError(f"op read failed for {reference}: not signed in. Run `op signin`")

    monkeypatch.setattr("scripts.put_github_secrets.read_op", signed_out)
    monkeypatch.setattr("scripts.put_github_secrets.run_gh", RecordingGh())

    assert main(["--region", "us-east-2"]) == 2
    assert "op signin" in capsys.readouterr().err


def test_read_op_explains_a_missing_cli(monkeypatch) -> None:
    def no_op(*args, **kwargs):
        raise FileNotFoundError("op")

    monkeypatch.setattr("scripts.put_github_secrets.subprocess.run", no_op)
    with pytest.raises(OpError, match="mise install"):
        read_op(f"{ITEM}/pem")


def test_read_op_explains_a_failed_read(monkeypatch) -> None:
    def failed(*args, **kwargs):
        raise subprocess.CalledProcessError(1, ["op"], stderr="[ERROR] not currently signed in\n")

    monkeypatch.setattr("scripts.put_github_secrets.subprocess.run", failed)
    with pytest.raises(OpError, match="op signin"):
        read_op(f"{ITEM}/pem")
