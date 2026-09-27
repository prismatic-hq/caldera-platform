from pathlib import Path

import aws_cdk as cdk
import pytest

from caldera.config import ENVIRONMENTS_DIR, PlatformConfig


def config(**context: object) -> PlatformConfig:
    return PlatformConfig.from_context(cdk.App(context={"domain": "example.com", **context}).node)


@pytest.fixture
def environments(tmp_path: Path) -> Path:
    (tmp_path / "sandbox.yaml").write_text(
        "domain: sandbox.example.net\nbudgetEmail: ops@example.net\nnatGateways: 2\n"
        "previewAllowlistCidrs:\n  - 10.0.0.0/8\n"
    )
    return tmp_path


def from_env(environments: Path, **context: object) -> PlatformConfig:
    return PlatformConfig.from_context(cdk.App(context=context).node, environments)


@pytest.mark.parametrize(
    "cidrs",
    [
        pytest.param("10.0.0.0/8,192.168.0.0/16", id="comma-separated string from -c"),
        pytest.param(" 10.0.0.0/8 , 192.168.0.0/16 ", id="string with spaces"),
        pytest.param(["10.0.0.0/8", "192.168.0.0/16"], id="list from cdk.json"),
    ],
)
def test_allowlist_cidrs_parse_from_cli_strings_and_json_lists(cidrs: object) -> None:
    assert config(previewAllowlistCidrs=cidrs).allowlist_cidrs == ("10.0.0.0/8", "192.168.0.0/16")


def test_allowlist_is_empty_by_default() -> None:
    assert config().allowlist_cidrs == ()


def test_budget_alerts_go_to_the_platform_address_by_default() -> None:
    assert config(domain="example.org").budget_email == "platform@example.org"
    assert config(budgetEmail="ops@example.org").budget_email == "ops@example.org"


def test_domain_is_required() -> None:
    with pytest.raises(ValueError, match="ENV=<name>.yaml"):
        PlatformConfig.from_context(cdk.App().node)


def test_settings_load_from_the_named_environment_file(environments: Path) -> None:
    settings = from_env(environments, env="sandbox.yaml")

    assert settings.domain == "sandbox.example.net"
    assert settings.preview_domain == "preview.sandbox.example.net"
    assert settings.budget_email == "ops@example.net"
    assert settings.nat_gateways == 2
    assert settings.allowlist_cidrs == ("10.0.0.0/8",)


def test_cdk_context_overrides_the_environment_file(environments: Path) -> None:
    settings = from_env(environments, env="sandbox.yaml", domain="cli.example.net")

    assert settings.domain == "cli.example.net"
    assert settings.budget_email == "ops@example.net"


def test_missing_environment_file_names_the_available_ones(environments: Path) -> None:
    with pytest.raises(ValueError, match="available: sandbox.yaml"):
        from_env(environments, env="prod.yaml")


@pytest.mark.parametrize("name", ["../sandbox.yaml", "/etc/passwd", "sub/sandbox.yaml"])
def test_environment_names_cannot_escape_the_environments_directory(
    environments: Path, name: str
) -> None:
    with pytest.raises(ValueError, match="file name"):
        from_env(environments, env=name)


def test_environment_file_must_be_a_mapping(environments: Path) -> None:
    (environments / "list.yaml").write_text("- domain\n")

    with pytest.raises(ValueError, match="must be a mapping"):
        from_env(environments, env="list.yaml")


def test_committed_environment_files_define_a_domain() -> None:
    files = sorted(ENVIRONMENTS_DIR.glob("*.yaml"))

    assert files
    for path in files:
        assert PlatformConfig.from_context(cdk.App(context={"env": path.name}).node).domain
