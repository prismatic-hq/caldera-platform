import subprocess
from pathlib import Path

import setup


class RecordingRunner:
    def __init__(self, fail_on: str | None = None) -> None:
        self.commands: list[list[str]] = []
        self.fail_on = fail_on

    def __call__(self, command: list[str]) -> None:
        self.commands.append(command)
        if self.fail_on and self.fail_on in " ".join(command):
            raise subprocess.CalledProcessError(3, command)


def test_finds_mise_on_path_first(tmp_path: Path) -> None:
    local = tmp_path / "mise"
    local.touch()
    assert setup.find_mise(lambda _: "/usr/bin/mise", local) == "/usr/bin/mise"


def test_falls_back_to_the_installer_location(tmp_path: Path) -> None:
    local = tmp_path / "mise"
    local.touch()
    assert setup.find_mise(lambda _: None, local) == str(local)


def test_reports_mise_missing(tmp_path: Path) -> None:
    assert setup.find_mise(lambda _: None, tmp_path / "mise") is None


def test_steps_trust_install_then_run_the_init_task() -> None:
    steps = setup.steps("/bin/mise")
    assert [step.command for step in steps] == [
        ["/bin/mise", "trust"],
        ["/bin/mise", "install"],
        ["/bin/mise", "run", "init"],
    ]


def test_skips_the_installer_when_mise_exists(tmp_path: Path) -> None:
    runner = RecordingRunner()
    code = setup.bootstrap(runner, lambda _: "/bin/mise", tmp_path / "mise")
    assert code == 0
    assert setup.INSTALL_MISE.command not in runner.commands
    assert runner.commands[0] == ["/bin/mise", "trust"]


def test_installs_mise_when_missing(tmp_path: Path) -> None:
    local = tmp_path / "mise"
    runner = RecordingRunner()

    def install_then_record(command: list[str]) -> None:
        runner(command)
        if command == setup.INSTALL_MISE.command:
            local.touch()

    code = setup.bootstrap(install_then_record, lambda _: None, local)
    assert code == 0
    assert runner.commands[0] == setup.INSTALL_MISE.command
    assert runner.commands[1] == [str(local), "trust"]


def test_fails_clearly_when_the_installer_leaves_no_mise(tmp_path: Path, capsys) -> None:
    code = setup.bootstrap(RecordingRunner(), lambda _: None, tmp_path / "mise")
    assert code == 1
    assert "mise not found" in capsys.readouterr().err


def test_names_the_failing_step(tmp_path: Path, capsys) -> None:
    code = setup.bootstrap(RecordingRunner(fail_on="install"), lambda _: "/bin/mise", tmp_path)
    assert code == 1
    assert "failed at step 'install pinned tools'" in capsys.readouterr().err
