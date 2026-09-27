from dataclasses import dataclass, field

import pytest

import cfn


@dataclass
class FakeContext:
    remaining_millis: list[int]
    invoked_function_arn: str = "arn:aws:lambda:us-east-1:123456789012:function:caldera-drainer"

    def get_remaining_time_in_millis(self) -> int:
        return (
            self.remaining_millis.pop(0)
            if len(self.remaining_millis) > 1
            else self.remaining_millis[0]
        )


@dataclass
class Recorder:
    responses: list[tuple[str, str]] = field(default_factory=list)
    reinvoked: list[dict] = field(default_factory=list)


@pytest.fixture
def recorder(monkeypatch: pytest.MonkeyPatch) -> Recorder:
    recorded = Recorder()
    monkeypatch.setattr(
        cfn, "respond", lambda event, status, reason="": recorded.responses.append((status, reason))
    )
    monkeypatch.setattr(cfn, "reinvoke", lambda event, context: recorded.reinvoked.append(event))
    monkeypatch.setattr(cfn.time, "sleep", lambda seconds: None)
    return recorded


def event(request_type: str, **extra: object) -> dict:
    return {
        "RequestType": request_type,
        "LogicalResourceId": "Drainer",
        "ResourceProperties": {"TimeoutMinutes": "20"},
        **extra,
    }


def remaining(*passes: list[str]):
    queue = list(passes)
    return lambda properties: queue.pop(0) if len(queue) > 1 else queue[0]


def test_create_and_update_succeed_without_cleanup(recorder: Recorder) -> None:
    for request in ("Create", "Update"):
        cfn.run(event(request), FakeContext([900_000]), lambda p: pytest.fail("cleanup ran"))

    assert recorder.responses == [("SUCCESS", ""), ("SUCCESS", "")]


def test_delete_polls_until_nothing_is_left(recorder: Recorder) -> None:
    cfn.run(
        event("Delete"), FakeContext([900_000]), remaining(["nlb"], ["nlb"], []), now=lambda: 0.0
    )

    assert recorder.responses == [("SUCCESS", "")]


def test_delete_reinvokes_itself_when_lambda_time_runs_low(recorder: Recorder) -> None:
    cfn.run(event("Delete"), FakeContext([60_000]), remaining(["nlb"]), now=lambda: 100.0)

    assert recorder.responses == []
    assert recorder.reinvoked[0][cfn.STARTED_AT] == 100.0


def test_delete_fails_naming_leftovers_after_the_deadline(recorder: Recorder) -> None:
    started = event("Delete", **{cfn.STARTED_AT: 0.0})

    cfn.run(started, FakeContext([900_000]), remaining(["instance i-123"]), now=lambda: 1201.0)

    assert recorder.responses == [("FAILED", "still present after 20m: instance i-123")]


def test_delete_reports_handler_errors_as_failed(recorder: Recorder) -> None:
    def broken(properties: dict) -> list[str]:
        raise RuntimeError("AccessDenied on DescribeInstances")

    cfn.run(event("Delete"), FakeContext([900_000]), broken, now=lambda: 0.0)

    assert recorder.responses == [("FAILED", "RuntimeError: AccessDenied on DescribeInstances")]
