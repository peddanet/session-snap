"""Tests of session_snap.selfrestart (template of the dev-conventions bundle)."""
from __future__ import annotations

import importlib
import stat
import sys
from pathlib import Path
from types import ModuleType

import pytest


def load() -> ModuleType:
    """Import the module from the package.

    Returns:
        The module.
    """
    return importlib.import_module("session_snap.selfrestart")


def fake_python(tmp_path: Path, output: str, code: int = 0) -> str:
    """Write a fake interpreter that answers ``-m <pkg> --version``.

    Args:
        tmp_path: Temporary directory.
        output: Text printed on standard output.
        code: Exit code.

    Returns:
        Path of the fake interpreter.
    """
    path = tmp_path / "python"
    path.write_text(f"#!/bin/sh\necho '{output}'\nexit {code}\n")
    path.chmod(path.stat().st_mode | stat.S_IXUSR)
    return str(path)


def test_command_drops_markers_and_old_log() -> None:
    """Markers and an earlier --continue-log are replaced, other args kept."""
    restart = load().SelfRestart(["run", "/data", "-y", "--continue-log", "old.log",
                                  "--jobs", "2", "--continue-log=x"], "new.log",
                                 python="/venv/bin/python", package="demo")
    assert restart.command() == ["/venv/bin/python", "-m", "demo", "run", "/data", "--jobs",
                                 "2", "--foreground", "-y", "--continue-log", "new.log"]


def test_installed_version_answers(tmp_path: Path) -> None:
    """A release that prints '<tool> X.Y.Z' reports X.Y.Z.

    Args:
        tmp_path: Temporary directory (fixture).
    """
    restart = load().SelfRestart([], "l", python=fake_python(tmp_path, "demo 1.3.0"))
    assert restart.installed_version() == "1.3.0"


@pytest.mark.parametrize("output,code", [("broken", 0), ("demo 1.3.0", 1)])
def test_installed_version_rejects(tmp_path: Path, output: str, code: int) -> None:
    """Unexpected output or a non-zero exit code means 'do not switch'.

    Args:
        tmp_path: Temporary directory (fixture).
        output: Printed text (parameter).
        code: Exit code (parameter).
    """
    restart = load().SelfRestart([], "l", python=fake_python(tmp_path, output, code))
    assert restart.installed_version() is None and "exit code" in restart.last_error


def test_missing_release_and_failing_exec(tmp_path: Path) -> None:
    """A vanished interpreter is reported; execute raises OSError instead of exiting.

    Args:
        tmp_path: Temporary directory (fixture).
    """
    restart = load().SelfRestart([], "l", python=str(tmp_path / "gone"))
    assert restart.installed_version() is None and "gone" in restart.last_error
    with pytest.raises(OSError):
        restart.execute()


def test_default_interpreter_is_running_one() -> None:
    """Without an explicit interpreter the running one is used (same venv)."""
    module = load()
    assert module.SelfRestart([], "l").python == sys.executable
    assert module.PACKAGE == "session_snap"
