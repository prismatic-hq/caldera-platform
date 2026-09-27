import subprocess
from pathlib import Path

import pytest

from preview_cli.registry import ServiceRegistry
from scripts.demo import (
    DEMO_BRANCHES,
    Demo,
    DemoConfig,
    DemoError,
    build_steps,
    describe,
    environment_for,
    main,
    select_steps,
    service_url,
)

REGISTRY = ServiceRegistry.load(Path(__file__).resolve().parents[2] / "services.yaml")
CONFIG = DemoConfig(
    context="caldera",
    domain="preview.example.com",
    org="prismatic-hq",
    stamp="20260927T120000",
    dry_run=False,
    auto=True,
)


def _completed(stdout: str = "", returncode: int = 0) -> subprocess.CompletedProcess:
    return subprocess.CompletedProcess([], returncode, stdout=stdout, stderr="")


class FakeClock:
    def __init__(self) -> None:
        self.now = 0.0

    def __call__(self) -> float:
        return self.now

    def sleep(self, seconds: float) -> None:
        self.now += seconds


def _demo(config: DemoConfig = CONFIG, runner=None, request=None, answers=()) -> tuple:
    output: list[str] = []
    replies = iter(answers)
    clock = FakeClock()
    demo = Demo(
        config,
        runner=runner or (lambda argv: _completed()),
        request=request or (lambda method, url, body=None: (200, {})),
        ask=lambda prompt: next(replies),
        echo=output.append,
        clock=clock,
        sleep=clock.sleep,
    )
    return demo, output


def test_steps_follow_the_ten_step_demo_plan_in_order() -> None:
    steps = build_steps(CONFIG, REGISTRY)

    assert [step.number for step in steps] == list(range(1, 11))
    assert [step.title for step in steps] == [
        "Baseline: dev runs main of both services",
        "Scenario A: feature/quake-alerts in tremor-api only",
        "Scenario B: fix-crew-sync in steward-api only",
        "Scenario C1: feature/quake-alerts in steward-api joins the environment",
        "Scenario C2: feature/tsunami and feature/lava-flow",
        "Isolation and preview env reset",
        "Headroom preemption",
        "Teardown: delete the demo branches",
        "Same preview env up on kind",
        "Code walk",
    ]


@pytest.mark.parametrize(
    ("repo", "branch", "environment"),
    [
        ("tremor-api", "feature/quake-alerts", "quake-alerts"),
        ("steward-api", "feature/quake-alerts", "quake-alerts"),
        ("steward-api", "fix-crew-sync", "steward-fix-crew-sync"),
        ("tremor-api", "feature/tsunami", "tsunami"),
        ("steward-api", "feature/lava-flow", "lava-flow"),
    ],
)
def test_environment_names_come_from_the_preview_resolver(
    repo: str, branch: str, environment: str
) -> None:
    assert environment_for(REGISTRY, repo, branch) == environment


def test_service_url_matches_the_services_chart_hostname() -> None:
    assert (
        service_url("tremor", "quake-alerts", "preview.example.com")
        == "https://tremor-quake-alerts.preview.example.com"
    )


def test_demo_branches_never_include_main() -> None:
    assert all(branch != "main" for _, branch in DEMO_BRANCHES)
    assert set(DEMO_BRANCHES) == {
        ("tremor-api", "feature/quake-alerts"),
        ("steward-api", "feature/quake-alerts"),
        ("steward-api", "fix-crew-sync"),
        ("tremor-api", "feature/tsunami"),
        ("steward-api", "feature/lava-flow"),
    }


def test_select_from_step_keeps_that_step_and_later() -> None:
    steps = build_steps(CONFIG, REGISTRY)

    assert [step.number for step in select_steps(steps, from_step=8)] == [8, 9, 10]


def test_select_only_keeps_one_step() -> None:
    steps = build_steps(CONFIG, REGISTRY)

    assert [step.number for step in select_steps(steps, only=6)] == [6]


def test_select_rejects_a_step_outside_the_plan() -> None:
    steps = build_steps(CONFIG, REGISTRY)

    with pytest.raises(DemoError, match="between 1 and 10"):
        select_steps(steps, only=11)


def test_push_is_described_as_gh_api_calls_on_the_service_repo() -> None:
    [scenario_a] = select_steps(build_steps(CONFIG, REGISTRY), only=2)

    lines = describe(scenario_a, CONFIG)

    assert "gh api repos/prismatic-hq/tremor-api/git/ref/heads/main --jq .object.sha" in lines
    assert (
        "gh api repos/prismatic-hq/tremor-api/git/refs -f ref=refs/heads/feature/quake-alerts"
        " -f 'sha=<main-sha>'" in lines
    )
    assert any("https://tremor-quake-alerts.preview.example.com/readyz" in line for line in lines)


def test_dry_run_prints_every_step_and_runs_nothing() -> None:
    def refuse(*args, **kwargs):
        raise AssertionError("dry run must not call out")

    config = DemoConfig(**{**CONFIG.__dict__, "dry_run": True})
    demo, output = _demo(config, runner=refuse, request=refuse)

    demo.run(build_steps(config, REGISTRY))

    text = "\n".join(output)
    for number in range(1, 11):
        assert f"== Step {number}/10" in text
    assert "gh api -X DELETE repos/prismatic-hq/steward-api/git/refs/heads/fix-crew-sync" in text
    assert "uv run preview env reset --name quake-alerts --context caldera" in text


def test_quit_stops_before_running_the_step() -> None:
    calls: list[list[str]] = []
    config = DemoConfig(**{**CONFIG.__dict__, "auto": False})
    demo, output = _demo(
        config, runner=lambda argv: calls.append(argv) or _completed(), answers=["q"]
    )

    demo.run(build_steps(config, REGISTRY))

    assert calls == []
    assert output[-1] == "Stopped at step 1."


def test_skip_moves_to_the_next_step_without_running() -> None:
    calls: list[list[str]] = []
    config = DemoConfig(**{**CONFIG.__dict__, "auto": False})
    demo, _ = _demo(config, runner=lambda argv: calls.append(argv) or _completed(), answers=["s"])

    demo.run(select_steps(build_steps(config, REGISTRY), only=1))

    assert calls == []


def test_wait_live_reports_seconds_until_ready() -> None:
    statuses = iter([503, 503, 200])
    runner_output = '[{"status":"in_progress","conclusion":"","url":"https://github.com/run/1"}]'
    demo, output = _demo(
        runner=lambda argv: _completed(runner_output),
        request=lambda method, url, body=None: (next(statuses), None),
    )

    demo.wait_live("tremor", "quake-alerts", "tremor-api", "feature/quake-alerts")

    assert any("live after 4s" in line for line in output)
    assert any("in_progress https://github.com/run/1" in line for line in output)


def test_wait_live_times_out_with_the_url() -> None:
    demo, _ = _demo(request=lambda method, url, body=None: (503, None))

    with pytest.raises(DemoError, match="tremor-quake-alerts.preview.example.com/readyz"):
        demo.wait_live("tremor", "quake-alerts", "tremor-api", "feature/quake-alerts")


def test_isolation_fails_when_data_leaks_into_another_environment() -> None:
    def request(method: str, url: str, body=None):
        return (201, {"id": "a1"}) if method == "POST" else (200, {"id": "a1"})

    demo, _ = _demo(request=request)
    demo.write_alert("quake-alerts")

    with pytest.raises(DemoError, match="alert a1 is visible in tsunami"):
        demo.expect_absent("tsunami")


def test_isolation_passes_when_the_alert_is_absent() -> None:
    def request(method: str, url: str, body=None):
        return (201, {"id": "a1"}) if method == "POST" else (404, None)

    demo, output = _demo(request=request)
    demo.write_alert("quake-alerts")
    demo.expect_absent("tsunami")

    assert output[-1] == "alert a1 is absent in tsunami (404)"


def test_teardown_needs_confirmation_unless_auto() -> None:
    calls: list[list[str]] = []
    config = DemoConfig(**{**CONFIG.__dict__, "auto": False})
    demo, output = _demo(
        config, runner=lambda argv: calls.append(argv) or _completed(), answers=["", "n"]
    )

    demo.run(select_steps(build_steps(config, REGISTRY), only=8))

    assert not any(argv[:3] == ["gh", "api", "-X"] for argv in calls)
    assert "Not deleting branches." in output


def test_cleanup_deletes_only_the_known_demo_branches() -> None:
    calls: list[list[str]] = []
    demo, _ = _demo(runner=lambda argv: calls.append(argv) or _completed())

    demo.delete_branches()

    assert calls == [
        ["gh", "api", "-X", "DELETE", f"repos/prismatic-hq/{repo}/git/refs/heads/{branch}"]
        for repo, branch in DEMO_BRANCHES
    ]


def test_main_dry_run_needs_no_domain_lookup(capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["--dry-run", "--auto", "--only", "10"]) == 0

    assert "cli/src/preview_cli/" in capsys.readouterr().out
