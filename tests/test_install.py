"""Tests of install.sh: leaving the single-file layout up to 1.8.0.

GitHub is replaced by a local repository with a release tag; the pip step is
replaced through PIP_INSTALL_CMD by a script that writes a stand-in program
into the venv, so the test needs no network.
"""
from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
FAKE_PIP = '''#!/bin/sh
# stand-in for pip: $1 = venv, $2 = tag
mkdir -p "$1/bin"
cat > "$1/bin/session-snap" <<EOF
#!/bin/sh
case "\\$1" in
  --version) echo "session-snap ${2#v}" ;;
  *) echo "called: \\$*" ;;
esac
EOF
chmod +x "$1/bin/session-snap"
'''
OLD_PROGRAM = '''#!/usr/bin/env python3
"""session-snap - save and restore a desktop session."""
VERSION = "1.8.0"
'''
OLD_TESTS = '''#!/usr/bin/env python3
"""Tests of session-snap."""
VERSION = "1.8.0"  # must equal VERSION of the program it tests (used by --install/--update)
'''


def git(*args: str, cwd: Path) -> None:
    """Run git quietly in `cwd`.

    Args:
        *args: Arguments of git.
        cwd: Working directory.
    """
    subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True)


@pytest.fixture(name="home")
def fixture_home(tmp_path: Path) -> dict[str, str]:
    """A home folder with the files of 1.8.0 and a repository with the tag v9.0.0.

    Args:
        tmp_path: Temporary folder of the test.

    Returns:
        Environment for install.sh.
    """
    repo = tmp_path / "repo"
    repo.mkdir()
    git("init", "-q", "-b", "main", cwd=repo)
    (repo / "README").write_text("x\n", encoding="utf-8")
    git("add", ".", cwd=repo)
    git("-c", "user.name=t", "-c", "user.email=t@example.invalid",
        "commit", "-q", "-m", "x", cwd=repo)
    git("tag", "v9.0.0", cwd=repo)
    home = tmp_path / "home"
    scripts = home / "scripts"
    scripts.mkdir(parents=True)
    (scripts / "session-snap").write_text(OLD_PROGRAM, encoding="utf-8")
    (scripts / "test_session_snap").write_text(OLD_TESTS, encoding="utf-8")
    autostart = home / ".config" / "autostart"
    autostart.mkdir(parents=True)
    (autostart / "session-snap-watch.desktop").write_text(
        f"[Desktop Entry]\nExec={scripts}/session-snap watch\n", encoding="utf-8")
    pip = tmp_path / "fake-pip"
    pip.write_text(FAKE_PIP, encoding="utf-8")
    pip.chmod(0o755)
    env = {k: v for k, v in os.environ.items() if not k.startswith("XDG_")}
    env.update(HOME=str(home), SESSION_SNAP_REPO=str(repo), PIP_INSTALL_CMD=str(pip))
    env.pop("BIN_DIR", None)
    return env


def test_old_files_move_with_their_version(home: dict[str, str]) -> None:
    """The old program and its tests (VERSION line with a comment) move into the backup
    folder under '<name> v<version>'; the autostart entry starts the link.

    Args:
        home: Environment of the prepared home folder.
    """
    run = subprocess.run([str(ROOT / "install.sh"), "v9.0.0"], env=home, text=True,
                         capture_output=True, check=False)
    assert run.returncode == 0, run.stdout + run.stderr
    h = Path(home["HOME"])
    backup = h / ".local" / "state" / "session-snap" / "backup"
    expected = ["session-snap v1.8.0", "test_session_snap v1.8.0"]
    assert sorted(p.name for p in backup.iterdir()) == expected
    assert not (h / "scripts" / "session-snap").exists()
    assert not (h / "scripts" / "test_session_snap").exists()
    entry = (h / ".config" / "autostart" / "session-snap-watch.desktop").read_text()
    assert f"Exec={h}/.local/bin/session-snap watch" in entry
    assert "called: watch --status" in run.stdout
    assert "called: desktop-entry" in run.stdout
