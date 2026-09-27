"""Read fields and files from 1Password with `op read`."""

import subprocess

OP_VAULT = "Prismatic"
OP_ITEM = "prismatic-hq GitHub App"


class OpError(RuntimeError):
    """A 1Password CLI read that failed; the message never contains the secret."""


def op_reference(vault: str, item: str, field: str) -> str:
    return f"op://{vault}/{item}/{field}"


def read_op(reference: str) -> str:
    try:
        result = subprocess.run(
            ["op", "read", reference], check=True, capture_output=True, text=True
        )
    except FileNotFoundError as error:
        raise OpError(
            "1Password CLI `op` not found: run `mise install` in this repo (it pins 1password)"
        ) from error
    except subprocess.CalledProcessError as error:
        detail = (error.stderr or "").strip() or f"exit code {error.returncode}"
        raise OpError(
            f"op read failed for {reference}: {detail}. Enable Settings > Developer > "
            "Integrate with 1Password CLI in the desktop app (docs/DEVELOPMENT.md), run "
            "`op signin`, and check the vault and item names (--op-vault, --op-item)"
        ) from error
    return result.stdout
