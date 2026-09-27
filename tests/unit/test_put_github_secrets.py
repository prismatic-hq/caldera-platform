import subprocess
from pathlib import Path

import pytest

from scripts.put_github_secrets import GitHubSetting, main, put_github_settings, settings_for

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
