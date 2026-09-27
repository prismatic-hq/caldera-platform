import aws_cdk as cdk
import pytest

from caldera.config import PlatformConfig


def config(environ: dict[str, str] | None = None, **context: object) -> PlatformConfig:
    return PlatformConfig.from_context(
        cdk.App(context={"domain": "example.com", **context}).node, environ or {}
    )


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
    with pytest.raises(ValueError, match="CALDERA_DOMAIN"):
        PlatformConfig.from_context(cdk.App().node, {})


def test_settings_fall_back_to_caldera_environment_variables() -> None:
    settings = PlatformConfig.from_context(
        cdk.App().node,
        {
            "CALDERA_DOMAIN": "prismatic.example.net",
            "CALDERA_BUDGET_EMAIL": "ops@example.net",
            "CALDERA_NAT_GATEWAYS": "2",
            "CALDERA_PREVIEW_ALLOWLIST_CIDRS": "10.0.0.0/8",
        },
    )

    assert settings.domain == "prismatic.example.net"
    assert settings.preview_domain == "preview.prismatic.example.net"
    assert settings.budget_email == "ops@example.net"
    assert settings.nat_gateways == 2
    assert settings.allowlist_cidrs == ("10.0.0.0/8",)


def test_cdk_context_overrides_environment_variables() -> None:
    settings = config({"CALDERA_DOMAIN": "env.example.net"}, domain="cli.example.net")

    assert settings.domain == "cli.example.net"
