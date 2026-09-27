import os
import subprocess
import sys

from golden_seeder.fixtures import dataset
from golden_seeder.render import literal, render


def run_seeder(hash_seed: str) -> bytes:
    env = {**os.environ, "PYTHONHASHSEED": hash_seed}
    return subprocess.run(
        [sys.executable, "-m", "golden_seeder.cli"], env=env, check=True, capture_output=True
    ).stdout


def test_two_runs_produce_identical_output() -> None:
    first, second = run_seeder("1"), run_seeder("2")

    assert first
    assert first == second


def test_render_is_stable_in_process() -> None:
    assert render(dataset()) == render(dataset())


def test_rows_have_unique_uuid5_keys_and_fixed_timestamps() -> None:
    for table in dataset():
        ids = [row[0] for row in table.rows]
        assert len(ids) == len(set(ids))
        assert all(row[-1].tzinfo is not None and row[-1].year == 2026 for row in table.rows)


def test_literal_escapes_quotes_and_nulls() -> None:
    assert literal("O'Hare") == "'O''Hare'"
    assert literal(None) == "NULL"
