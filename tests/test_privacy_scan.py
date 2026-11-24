"""Tests of tools/privacy_scan.py: a gate must never pass because its input is missing."""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

SCAN = Path(__file__).resolve().parents[1] / "tools" / "privacy_scan.py"


def run_scan(tmp_path: Path, deny: str | None, ci: bool = False,
             content: str = "lorem ipsum\n") -> subprocess.CompletedProcess:
    """Run the scan on a one-commit repository with its own config and state folders.

    Args:
        tmp_path: Temporary folder of the test.
        deny: Text of the deny list; None = no deny list file.
        ci: Set CI=true like GitHub Actions.
        content: Content of the committed file.

    Returns:
        The finished process.
    """
    repo = tmp_path / "repo"
    (repo / "tools").mkdir(parents=True)
    source = SCAN.read_text(encoding="utf-8")
    (repo / "tools" / "privacy_scan.py").write_text(source, encoding="utf-8")
    (repo / "notes.txt").write_text(content, encoding="utf-8")
    env = {"PATH": os.environ["PATH"], "HOME": str(tmp_path / "home"),
           "XDG_CONFIG_HOME": str(tmp_path / "config"),
           "XDG_STATE_HOME": str(tmp_path / "state"),
           "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@example.org",
           "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@example.org"}
    if ci:
        env["CI"] = "true"
    for cmd in (["git", "init", "-q"], ["git", "add", "-A"], ["git", "commit", "-q", "-m", "x"]):
        subprocess.run(cmd, cwd=repo, env=env, check=True)
    if deny is not None:
        folder = tmp_path / "config" / "session-snap" / "privacy"
        folder.mkdir(parents=True)
        (folder / "denylist.txt").write_text(deny, encoding="utf-8")
    return subprocess.run([sys.executable, "tools/privacy_scan.py"], cwd=repo, env=env,
                          capture_output=True, text=True, check=False)


def test_missing_deny_list_fails(tmp_path: Path) -> None:
    """No deny list on a developer machine: exit 2 with a reason.

    Args:
        tmp_path: Temporary folder of the test.
    """
    run = run_scan(tmp_path, None)
    assert run.returncode == 2
    assert "FAILED: no local terms" in run.stderr


def test_empty_deny_list_fails(tmp_path: Path) -> None:
    """A deny list with only comments counts as empty.

    Args:
        tmp_path: Temporary folder of the test.
    """
    run = run_scan(tmp_path, "# nothing yet\n\n")
    assert run.returncode == 2


def test_ci_without_list_is_skipped(tmp_path: Path) -> None:
    """In CI there is no local list by design: skipped, exit 0, and it says so.

    Args:
        tmp_path: Temporary folder of the test.
    """
    run = run_scan(tmp_path, None, ci=True)
    assert run.returncode == 0
    assert "skipped in CI" in run.stdout


def test_term_found_and_clean(tmp_path: Path) -> None:
    """A listed term in a committed file is a finding; a clean repository passes.

    Args:
        tmp_path: Temporary folder of the test.
    """
    found = run_scan(tmp_path / "a", "quokka\n", content="the quokka smiles\n")
    assert found.returncode == 1
    assert "notes.txt" in found.stdout
    clean = run_scan(tmp_path / "b", "quokka\n")
    assert clean.returncode == 0
    assert "0 finding(s)" in clean.stdout
