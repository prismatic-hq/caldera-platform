import json
from pathlib import Path

import pytest
import yaml
from jsonschema import Draft202012Validator, FormatChecker

CONTRACTS = Path(__file__).resolve().parents[2] / "contracts" / "events"
FR_9_1_TYPES = {
    "vent.environment.requested.v1",
    "vent.environment.ready.v1",
    "vent.environment.failed.v1",
    "vent.environment.cooled.v1",
    "data.golden.built.v1",
    "test.e2e.completed.v1",
}


def load_json(path: Path) -> dict:
    return json.loads(path.read_text())


REGISTRY = yaml.safe_load((CONTRACTS / "registry.v1.yaml").read_text())
ENTRIES = {entry["type"]: entry for entry in REGISTRY["types"]}


def validator(schema: dict) -> Draft202012Validator:
    Draft202012Validator.check_schema(schema)
    return Draft202012Validator(schema, format_checker=FormatChecker())


def test_registry_matches_its_schema() -> None:
    validator(load_json(CONTRACTS / "registry.v1.schema.json")).validate(REGISTRY)


def test_registry_holds_exactly_the_fr_9_1_types() -> None:
    assert set(ENTRIES) == FR_9_1_TYPES
    assert len(REGISTRY["types"]) == len(FR_9_1_TYPES)


@pytest.mark.parametrize("folder", ["data", "examples"])
def test_no_unregistered_contract_files(folder: str) -> None:
    key = "schema" if folder == "data" else "example"
    registered = {entry[key] for entry in ENTRIES.values()}

    assert {f"{folder}/{path.name}" for path in (CONTRACTS / folder).iterdir()} == registered


@pytest.mark.parametrize("event_type", sorted(FR_9_1_TYPES))
def test_example_validates_against_envelope_and_data_schema(event_type: str) -> None:
    entry = ENTRIES[event_type]
    example = load_json(CONTRACTS / entry["example"])

    validator(load_json(CONTRACTS / "envelope.v1.schema.json")).validate(example)
    validator(load_json(CONTRACTS / entry["schema"])).validate(example["data"])
    assert example["type"] == f"{REGISTRY['typePrefix']}.{event_type}"
    assert example["dataschema"].endswith(entry["schema"])
    assert example["subject"].split("/")[0] == entry["subject"].split("/")[0]
