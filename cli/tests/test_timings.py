import pytest

from preview_cli.timings import Stopwatch, summary_markdown


class FakeClock:
    def __init__(self, *readings: float) -> None:
        self.readings = list(readings)

    def __call__(self) -> float:
        return self.readings.pop(0)


def test_stages_record_elapsed_seconds_in_order() -> None:
    stopwatch = Stopwatch("up", "quake-alerts", clock=FakeClock(0.0, 0.0, 1.0, 1.25, 3.5, 4.0))

    with stopwatch.stage("resolve"):
        pass
    with stopwatch.stage("helm_upgrade"):
        pass

    assert stopwatch.record() == {
        "command": "up",
        "environment": "quake-alerts",
        "stages": {"resolve": 1.0, "helm_upgrade": 2.25},
        "total_seconds": 4.0,
    }


def test_a_failing_stage_is_still_recorded() -> None:
    stopwatch = Stopwatch("test", "x", clock=FakeClock(0.0, 0.0, 2.0))

    with pytest.raises(RuntimeError), stopwatch.stage("e2e"):
        raise RuntimeError("boom")

    assert stopwatch.stages == {"e2e": 2.0}


def test_summary_markdown_is_a_stage_table() -> None:
    record = {
        "command": "up",
        "environment": "quake-alerts",
        "stages": {"resolve": 1.0, "helm_upgrade": 2.25},
        "total_seconds": 4.0,
    }

    assert summary_markdown(record) == (
        "### preview env up quake-alerts\n\n"
        "| Stage | Seconds |\n"
        "|---|---|\n"
        "| resolve | 1.00 |\n"
        "| helm_upgrade | 2.25 |\n"
        "| **total** | **4.00** |\n"
    )
