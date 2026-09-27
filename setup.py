"""Bootstrap a developer machine: install mise, the tools in mise.toml, then `mise run init`.

Run with any Python 3: `python3 setup.py`. Safe to re-run; each step is idempotent.
Standard library only, because it runs before mise has installed the project's Python.
"""

import os
import shutil
import subprocess
import sys
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent
LOCAL_MISE = Path.home() / ".local" / "bin" / "mise"

Runner = Callable[[list[str]], None]
Which = Callable[[str], str | None]


@dataclass(frozen=True)
class Step:
    name: str
    command: list[str]


INSTALL_MISE = Step("install mise", ["sh", "-c", "curl -fsSL https://mise.run | sh"])


def find_mise(which: Which = shutil.which, local: Path = LOCAL_MISE) -> str | None:
    return which("mise") or (str(local) if local.is_file() else None)


def steps(mise: str) -> list[Step]:
    return [
        Step("trust mise.toml", [mise, "trust"]),
        Step("install pinned tools", [mise, "install"]),
        Step("install Python dependencies and git hooks", [mise, "run", "init"]),
    ]


def run_in_repo(command: list[str]) -> None:
    subprocess.run(command, check=True, cwd=REPO_ROOT)


def run_step(run: Runner, step: Step) -> bool:
    print(f"==> {step.name}", flush=True)
    try:
        run(step.command)
    except (subprocess.CalledProcessError, FileNotFoundError) as error:
        print(
            f"setup failed at step '{step.name}' ({' '.join(step.command)}): {error}",
            file=sys.stderr,
        )
        return False
    return True


def activation_hint(which: Which, local: Path) -> str | None:
    if which("mise"):
        return None
    shell = Path(os.environ.get("SHELL", "zsh")).name
    rc = "~/.bashrc" if shell == "bash" else "~/.zshrc"
    return f"Activate mise in new shells: echo 'eval \"$({local} activate {shell})\"' >> {rc}"


def bootstrap(
    run: Runner = run_in_repo, which: Which = shutil.which, local: Path = LOCAL_MISE
) -> int:
    mise = find_mise(which, local)
    if mise is None:
        if not run_step(run, INSTALL_MISE):
            return 1
        mise = find_mise(which, local)
        if mise is None:
            print(f"mise not found on PATH or at {local} after installing it", file=sys.stderr)
            return 1
    if not all(run_step(run, step) for step in steps(mise)):
        return 1
    hint = activation_hint(which, local)
    if hint:
        print(hint)
    print("Setup complete. Try: mise run test")
    return 0


if __name__ == "__main__":
    sys.exit(bootstrap())
