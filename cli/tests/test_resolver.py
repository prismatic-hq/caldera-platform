import pytest

from caldera_cli.resolver import (
    Action,
    Service,
    VentNameError,
    VentPlan,
    resolve_delete,
    resolve_push,
)

TREMOR, STEWARD = Service.TREMOR, Service.STEWARD


@pytest.mark.parametrize(
    ("service", "branch", "other_has_branch", "existing", "expected"),
    [
        pytest.param(
            TREMOR,
            "feature/quake-alerts",
            False,
            set(),
            VentPlan("quake-alerts", Action.UP, {TREMOR: "feature/quake-alerts", STEWARD: "main"}),
            id="A: feature branch in tremor only",
        ),
        pytest.param(
            STEWARD,
            "fix-crew-sync",
            False,
            set(),
            VentPlan(
                "steward-fix-crew-sync", Action.UP, {STEWARD: "fix-crew-sync", TREMOR: "main"}
            ),
            id="B: other branch in steward only",
        ),
        pytest.param(
            TREMOR,
            "feature/quake-alerts",
            True,
            set(),
            VentPlan(
                "quake-alerts",
                Action.UP,
                {TREMOR: "feature/quake-alerts", STEWARD: "feature/quake-alerts"},
            ),
            id="C1: same feature branch in both repos",
        ),
        pytest.param(
            STEWARD,
            "feature/quake-alerts",
            True,
            {"quake-alerts"},
            VentPlan(
                "quake-alerts",
                Action.UP,
                {STEWARD: "feature/quake-alerts", TREMOR: "feature/quake-alerts"},
                joins_existing=True,
            ),
            id="C1: second push joins the existing vent",
        ),
        pytest.param(
            TREMOR,
            "feature/tsunami",
            False,
            {"lava-flow"},
            VentPlan("tsunami", Action.UP, {TREMOR: "feature/tsunami", STEWARD: "main"}),
            id="C2: tremor feature/tsunami",
        ),
        pytest.param(
            STEWARD,
            "feature/lava-flow",
            False,
            {"tsunami"},
            VentPlan("lava-flow", Action.UP, {STEWARD: "feature/lava-flow", TREMOR: "main"}),
            id="C2: steward feature/lava-flow",
        ),
        pytest.param(
            TREMOR,
            "Bugfix/Sensor_Drift",
            True,
            set(),
            VentPlan(
                "tremor-bugfix-sensor-drift",
                Action.UP,
                {TREMOR: "Bugfix/Sensor_Drift", STEWARD: "main"},
            ),
            id="non-feature branch is slugged and never shared",
        ),
    ],
)
def test_resolve_push(
    service: Service, branch: str, other_has_branch: bool, existing: set[str], expected: VentPlan
) -> None:
    assert resolve_push(service, branch, other_has_branch, frozenset(existing)) == expected


@pytest.mark.parametrize(
    ("service", "branch", "other_has_branch", "expected"),
    [
        pytest.param(
            TREMOR,
            "feature/quake-alerts",
            True,
            VentPlan(
                "quake-alerts",
                Action.UP,
                {TREMOR: "main", STEWARD: "feature/quake-alerts"},
                joins_existing=True,
            ),
            id="feature deleted, other repo keeps it: redeploy with deleted service on main",
        ),
        pytest.param(
            TREMOR,
            "feature/quake-alerts",
            False,
            VentPlan("quake-alerts", Action.DOWN),
            id="feature deleted, no branch left: cool",
        ),
        pytest.param(
            STEWARD,
            "fix-crew-sync",
            True,
            VentPlan("steward-fix-crew-sync", Action.DOWN),
            id="non-feature branch deleted: cool",
        ),
    ],
)
def test_resolve_delete(
    service: Service, branch: str, other_has_branch: bool, expected: VentPlan
) -> None:
    assert resolve_delete(service, branch, other_has_branch) == expected


@pytest.mark.parametrize(
    ("service", "branch", "message"),
    [
        pytest.param(TREMOR, "feature/" + "a" * 56, "DNS allows 63", id="hostname label over 63"),
        pytest.param(STEWARD, "x" * 60, "DNS allows 63", id="prefixed name over 63"),
        pytest.param(TREMOR, "feature/" + "a" * 49, "Helm allows 53", id="release over 53"),
        pytest.param(TREMOR, "main", "baseline", id="main"),
        pytest.param(TREMOR, "feature/---", "not a valid DNS label", id="empty slug"),
    ],
)
def test_invalid_vent_names_fail_with_clear_error(
    service: Service, branch: str, message: str
) -> None:
    with pytest.raises(VentNameError, match=message):
        resolve_push(service, branch, other_has_branch=False)


def test_longest_valid_name_fits_every_dns_label() -> None:
    plan = resolve_push(STEWARD, "feature/" + "a" * 48, other_has_branch=False)

    assert all(len(f"{service}-{plan.vent}") <= 63 for service in Service)


@pytest.mark.parametrize(
    ("repo", "expected"),
    [("tremor-api", TREMOR), ("prismatic-hq/steward-api", STEWARD), ("steward", STEWARD)],
)
def test_service_from_repo(repo: str, expected: Service) -> None:
    assert Service.from_repo(repo) is expected


def test_unknown_repo_is_rejected() -> None:
    with pytest.raises(ValueError, match="unknown service repo"):
        Service.from_repo("caldera-platform")
