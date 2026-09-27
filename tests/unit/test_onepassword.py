import subprocess

import pytest

from scripts.onepassword import OpError, op_reference, read_op


def test_reference_names_vault_item_and_field() -> None:
    assert op_reference("Ops", "App", "pem") == "op://Ops/App/pem"


def test_read_op_explains_a_missing_cli(monkeypatch) -> None:
    def no_op(*args, **kwargs):
        raise FileNotFoundError("op")

    monkeypatch.setattr("scripts.onepassword.subprocess.run", no_op)
    with pytest.raises(OpError, match="mise install"):
        read_op("op://Prismatic/prismatic-hq GitHub App/pem")


def test_read_op_explains_a_failed_read(monkeypatch) -> None:
    def failed(*args, **kwargs):
        raise subprocess.CalledProcessError(1, ["op"], stderr="[ERROR] not currently signed in\n")

    monkeypatch.setattr("scripts.onepassword.subprocess.run", failed)
    with pytest.raises(OpError, match="op signin") as raised:
        read_op("op://Prismatic/prismatic-hq GitHub App/pem")
    assert "Integrate with 1Password CLI" in str(raised.value)
