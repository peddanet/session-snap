"""Replace a running job by the newly installed release, in the same process.

Template of the dev-conventions bundle (handover chapter 13), unchanged except
for :data:`PACKAGE`. session-snap uses it for the background process
``watch`` (see ``session_snap.cli.WatchRestart``); reference implementation:
mediatools, ``shrink``.
"""
from __future__ import annotations

import os
import subprocess
import sys

PACKAGE = "session_snap"


class SelfRestart:
    """Restarts the current command with the installed release (``os.execv``).

    The caller decides *when*: only at a safe point, after the last work
    unit is stored and all files are closed. :meth:`installed_version`
    probes the installed release first; the caller switches only if it
    answers with a version different from the running one. The new process
    keeps the PID and runs the same arguments plus ``--foreground`` and
    ``-y`` (already confirmed, already in the background) and
    ``--continue-log LOG``; adapt :attr:`DROP` and :meth:`command` if the
    tool uses other options.

    Attributes:
        argv: Arguments of the running command, without the program name.
        log_path: Log file the new process continues.
        python: Interpreter of the installed release (the running one,
            because the release is reinstalled into the same venv).
        package: Package started with ``python -m``.
        last_error: Reason of the last failed probe.
    """

    TIMEOUT = 60.0
    DROP = ("--foreground", "-y", "--yes")

    def __init__(self, argv: list[str], log_path: str, python: str | None = None,
                 package: str = PACKAGE) -> None:
        """Store what the new process needs.

        Args:
            argv: Arguments of the running command, without the program name.
            log_path: Log file to continue.
            python: Interpreter; the running one if None.
            package: Package to start with ``-m``.
        """
        self.argv = list(argv)
        self.log_path = log_path
        self.python = python or sys.executable
        self.package = package
        self.last_error = ""

    def command(self) -> list[str]:
        """Build the command line of the new process.

        Returns:
            Argument vector for :func:`os.execv` (element 0 is the program).
        """
        args: list[str] = []
        skip = False
        for arg in self.argv:
            if skip:
                skip = False
            elif arg == "--continue-log":
                skip = True
            elif arg not in self.DROP and not arg.startswith("--continue-log="):
                args.append(arg)
        return [self.python, "-m", self.package, *args, "--foreground", "-y",
                "--continue-log", self.log_path]

    def installed_version(self) -> str | None:
        """Ask the installed release for its version.

        Returns:
            Version string such as ``1.3.0``, or None if the release does not
            start or answers unexpectedly (reason in :attr:`last_error`).
        """
        self.last_error = ""
        try:
            res = subprocess.run([self.python, "-m", self.package, "--version"],  # nosec B603
                                 capture_output=True, text=True, check=False,
                                 timeout=self.TIMEOUT)
        except (OSError, subprocess.TimeoutExpired) as err:
            self.last_error = f"{self.python}: {err}"
            return None
        words = res.stdout.split()
        if res.returncode != 0 or len(words) < 2:
            tail = (res.stderr.strip().splitlines() or [res.stdout.strip()])[-1:]
            self.last_error = f"exit code {res.returncode}: {' '.join(tail)[:300]}"
            return None
        return words[1]

    def execute(self) -> None:
        """Replace the current process by the new one; returns only on failure.

        Raises:
            OSError: If the program cannot be started.
        """
        sys.stdout.flush()
        sys.stderr.flush()
        cmd = self.command()
        os.execv(cmd[0], cmd)  # nosec B606
