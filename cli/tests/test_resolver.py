import pytest

from preview_cli.resolver import (
    Action,
    EnvironmentNameError,
    PreviewPlan,
    resolve_delete,
    resolve_push,
)

TWO = ("tremor", "steward")
THREE = ("tremor", "steward", "magma")


@pytest.mark.parametrize(
    ("services", "pushed", "branch", "sharing", "existing", "expected"),
    [
        pytest.param(
            TWO,
            "tremor",
            "feature/quake-alerts",
            set(),
            set(),
            PreviewPlan(
                "quake-alerts", Action.UP, {"tremor": "feature/quake-alerts", "steward": "main"}
            ),
            id="A: feature branch in tremor only",
        ),
        pytest.param(
            TWO,
            "steward",
            "fix-crew-sync",
            set(),
            set(),
            PreviewPlan(
                "steward-fix-crew-sync", Action.UP, {"tremor": "main", "steward": "fix-crew-sync"}
            ),
            id="B: other branch in steward only",
        ),
        pytest.param(
            TWO,
            "tremor",
            "feature/quake-alerts",
            {"steward"},
            set(),
            PreviewPlan(
                "quake-alerts",
                Action.UP,
                {"tremor": "feature/quake-alerts", "steward": "feature/quake-alerts"},
            ),
            id="C1: same feature branch in both repos",
        ),
        pytest.param(
            TWO,
            "steward",
            "feature/quake-alerts",
            {"tremor"},
            {"quake-alerts"},
            PreviewPlan(
                "quake-alerts",
                Action.UP,
                {"tremor": "feature/quake-alerts", "steward": "feature/quake-alerts"},
                joins_existing=True,
            ),
            id="C1: second push joins the existing preview environment",
        ),
        pytest.param(
            TWO,
            "tremor",
            "feature/tsunami",
            set(),
            {"lava-flow"},
            PreviewPlan("tsunami", Action.UP, {"tremor": "feature/tsunami", "steward": "main"}),
            id="C2: tremor feature/tsunami",
        ),
        pytest.param(
            TWO,
            "steward",
            "feature/lava-flow",
            set(),
            {"tsunami"},
            PreviewPlan("lava-flow", Action.UP, {"tremor": "main", "steward": "feature/lava-flow"}),
            id="C2: steward feature/lava-flow",
        ),
        pytest.param(
            TWO,
            "tremor",
            "Bugfix/Sensor_Drift",
            {"steward"},
            set(),
            PreviewPlan(
                "tremor-bugfix-sensor-drift",
                Action.UP,
                {"tremor": "Bugfix/Sensor_Drift", "steward": "main"},
            ),
            id="non-feature branch is slugged and never shared",
        ),
        pytest.param(
            THREE,
            "magma",
            "feature/quake-alerts",
            {"tremor"},
            set(),
            PreviewPlan(
                "quake-alerts",
                Action.UP,
                {
                    "tremor": "feature/quake-alerts",
                    "steward": "main",
                    "magma": "feature/quake-alerts",
                },
            ),
            id="three services: shares with every repo that has the branch",
        ),
    ],
)
def test_resolve_push(
    services: tuple[str, ...],
    pushed: str,
    branch: str,
    sharing: set[str],
    existing: set[str],
    expected: PreviewPlan,
) -> None:
    plan = resolve_push(services, pushed, branch, frozenset(sharing), frozenset(existing))

    assert plan == expected


@pytest.mark.parametrize(
    ("services", "deleted", "branch", "sharing", "expected"),
    [
        pytest.param(
            TWO,
            "tremor",
            "feature/quake-alerts",
            {"steward"},
            PreviewPlan(
                "quake-alerts",
                Action.UP,
                {"tremor": "main", "steward": "feature/quake-alerts"},
                joins_existing=True,
            ),
            id="feature deleted, other repo keeps it: redeploy with deleted service on main",
        ),
        pytest.param(
            TWO,
            "tremor",
            "feature/quake-alerts",
            set(),
            PreviewPlan("quake-alerts", Action.DOWN),
            id="feature deleted, no branch left: cool",
        ),
        pytest.param(
            TWO,
            "steward",
            "fix-crew-sync",
            {"tremor"},
            PreviewPlan("steward-fix-crew-sync", Action.DOWN),
            id="non-feature branch deleted: cool",
        ),
        pytest.param(
            THREE,
            "tremor",
            "feature/quake-alerts",
            {"magma"},
            PreviewPlan(
                "quake-alerts",
                Action.UP,
                {
                    "tremor": "main",
                    "steward": "main",
                    "magma": "feature/quake-alerts",
                },
                joins_existing=True,
            ),
            id="three services: keep the preview environment while any repo has the branch",
        ),
        pytest.param(
            TWO,
            "tremor",
            "feature/quake-alerts",
            {"tremor"},
            PreviewPlan("quake-alerts", Action.DOWN),
            id="deleted repo listed as sharing is ignored",
        ),
    ],
)
def test_resolve_delete(
    services: tuple[str, ...], deleted: str, branch: str, sharing: set[str], expected: PreviewPlan
) -> None:
    assert resolve_delete(services, deleted, branch, frozenset(sharing)) == expected


@pytest.mark.parametrize(
    ("services", "pushed", "branch", "message"),
    [
        pytest.param(
            TWO, "tremor", "feature/" + "a" * 56, "DNS allows 63", id="hostname label over 63"
        ),
        pytest.param(TWO, "steward", "x" * 60, "DNS allows 63", id="prefixed name over 63"),
        pytest.param(TWO, "tremor", "feature/" + "a" * 46, "Helm allows 53", id="release over 53"),
        pytest.param(
            ("tremor", "a-very-long-service-name"),
            "tremor",
            "feature/" + "a" * 40,
            "DNS allows 63",
            id="longest service name sets the limit",
        ),
        pytest.param(TWO, "tremor", "main", "baseline", id="main"),
        pytest.param(TWO, "tremor", "feature/---", "not a valid DNS label", id="empty slug"),
        pytest.param(TWO, "magma", "feature/x", "unknown service 'magma'", id="unknown service"),
    ],
)
def test_invalid_plans_fail_with_clear_error(
    services: tuple[str, ...], pushed: str, branch: str, message: str
) -> None:
    with pytest.raises(ValueError, match=message):
        resolve_push(services, pushed, branch, frozenset())


def test_environment_name_errors_are_value_errors() -> None:
    assert issubclass(EnvironmentNameError, ValueError)


def test_longest_valid_name_fits_every_dns_label() -> None:
    plan = resolve_push(TWO, "steward", "feature/" + "a" * 45, frozenset())

    assert all(len(f"{service}-{plan.environment}") <= 63 for service in TWO)
