from pathlib import Path

import pytest

from preview_cli.registry import ServiceRegistry, ServiceSpec

REPO_ROOT = Path(__file__).resolve().parents[2]
TREMOR = {"name": "tremor", "repo": "tremor-api", "image": "tremor-api", "port": 8000}
STEWARD = {"name": "steward", "repo": "steward-api", "image": "steward-api"}


def test_platform_registry_lists_both_services() -> None:
    registry = ServiceRegistry.load(REPO_ROOT / "services.yaml")

    assert registry.names == ("tremor", "steward")


def test_port_defaults_to_8000() -> None:
    registry = ServiceRegistry.from_mapping({"services": [STEWARD]})

    assert registry.get("steward") == ServiceSpec("steward", "steward-api", "steward-api", 8000)


@pytest.mark.parametrize("repo", ["steward-api", "prismatic-hq/steward-api", "steward"])
def test_by_repo_accepts_repo_full_name_or_service_name(repo: str) -> None:
    registry = ServiceRegistry.from_mapping({"services": [TREMOR, STEWARD]})

    assert registry.by_repo(repo).name == "steward"


@pytest.mark.parametrize(
    ("data", "message"),
    [
        pytest.param({"services": []}, "at least one service", id="empty"),
        pytest.param({"services": [TREMOR, TREMOR]}, "duplicate name", id="duplicate name"),
        pytest.param(
            {"services": [TREMOR, {**STEWARD, "repo": "tremor-api"}]},
            "duplicate repo",
            id="duplicate repo",
        ),
        pytest.param(
            {"services": [{**TREMOR, "name": "Tremor"}]}, "invalid service name", id="bad name"
        ),
        pytest.param({"services": [{"name": "tremor"}]}, "missing repo", id="missing field"),
    ],
)
def test_invalid_registry_is_rejected(data: dict, message: str) -> None:
    with pytest.raises(ValueError, match=message):
        ServiceRegistry.from_mapping(data)


def test_unknown_repo_is_rejected() -> None:
    registry = ServiceRegistry.from_mapping({"services": [TREMOR]})

    with pytest.raises(ValueError, match="unknown service repo 'caldera-platform'"):
        registry.by_repo("caldera-platform")
