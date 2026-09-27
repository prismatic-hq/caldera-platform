import time
from collections.abc import Callable, Iterator
from contextlib import contextmanager


class Stopwatch:
    """Wall-clock seconds per stage of one CLI command (Section 1 stages the CLI controls)."""

    def __init__(
        self, command: str, environment: str, clock: Callable[[], float] = time.monotonic
    ) -> None:
        self.command = command
        self.environment = environment
        self.stages: dict[str, float] = {}
        self._clock = clock
        self._started = clock()

    @contextmanager
    def stage(self, name: str) -> Iterator[None]:
        start = self._clock()
        try:
            yield
        finally:
            self.stages[name] = round(self._clock() - start, 3)

    def record(self) -> dict:
        return {
            "command": self.command,
            "environment": self.environment,
            "stages": dict(self.stages),
            "total_seconds": round(self._clock() - self._started, 3),
        }


def summary_markdown(record: dict) -> str:
    rows = [f"| {name} | {seconds:.2f} |" for name, seconds in record["stages"].items()]
    return "\n".join(
        [
            f"### preview env {record['command']} {record['environment']}",
            "",
            "| Stage | Seconds |",
            "|---|---|",
            *rows,
            f"| **total** | **{record['total_seconds']:.2f}** |",
            "",
        ]
    )
