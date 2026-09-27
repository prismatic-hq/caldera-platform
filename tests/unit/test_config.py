import aws_cdk as cdk
import pytest

from caldera.config import PlatformConfig


def config(**context: object) -> PlatformConfig:
    return PlatformConfig.from_context(cdk.App(context=context).node)


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
