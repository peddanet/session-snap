#!/usr/bin/env python3
"""Refuse a push that contains terms which allow conclusions about the user's data.

The terms are never part of the repository - a list of them would itself
reveal what it protects. They come from local sources only:

* the deny list ``~/.config/session-snap/privacy/denylist.txt``
  (``$XDG_CONFIG_HOME`` honoured): one term per line, ``#`` starts a comment;
* the labels of the user's own snapshots (``session-snap save -l ...``), read
  from the state folder ``~/.local/state/session-snap/snapshots`` and, while
  it still exists, the folder of versions up to 1.8.0
  (``~/scripts/session-snap.d/snapshots``).

Allowed phrases come from the allow list next to the deny list (same
format). They are blanked out before the search, so a listed term stays
forbidden everywhere except inside an allowed phrase.

Scanned are, because the repository is public with its whole history: the
contents of every file in every commit reachable from any branch or tag
(PDFs via ``pdftotext`` if installed), every file path that ever existed,
all commit messages and all tag annotations, and the identities: author and
committer of every commit, tagger of every tag. Matching ignores case; a term
must not be part of a longer word.

Identities are checked twice: their names and addresses against the terms,
and every e-mail address against the rule that only no-reply addresses may
be public (``...@users.noreply.github.com``, ``noreply@github.com``,
``noreply@anthropic.com``) unless the address is on the allow list. A real
address in the global git configuration therefore stops the push even if it
is not on the deny list.

Without any local term (deny list missing or empty, no snapshot labels) the
scan fails, because a gate must never pass only because its input is missing.
In CI (environment variable ``CI=true``, set by GitHub Actions) there is no
local list by design: there the scan is skipped and says so.

Exit codes: 0 clean (or skipped in CI), 1 terms found, 2 no local terms.
Runs as a normal user; reads only the repository and the user's own files.
"""
from __future__ import annotations

import fnmatch
import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

MIN_LENGTH = 3
# e-mail addresses that may appear in the public history
PUBLIC_EMAILS = ("*@users.noreply.github.com", "noreply@github.com", "noreply@anthropic.com")


class PrivacyScan:
    """Collects local terms and searches the repository and its history for them.

    Attributes:
        repo: Repository root.
        terms: Terms to search for (lower case).
        pattern: Compiled alternation of all terms, or None.
        allowed: Compiled alternation of the allowed phrases, or None.
        allowed_count: Number of allowed phrases.
        findings: ``(location, term)`` pairs.
    """

    def __init__(self, repo: str, terms: list[str], allowed: list[str] | None = None):
        """Prepare the search.

        Args:
            repo: Repository root.
            terms: Terms to search for.
            allowed: Phrases in which a term is permitted.
        """
        self.repo = repo
        self.terms = sorted({t.strip().lower() for t in terms if len(t.strip()) >= MIN_LENGTH},
                            key=len, reverse=True)
        self.pattern = re.compile(
            r"(?<![a-z0-9])(" + "|".join(re.escape(t) for t in self.terms) + r")(?![a-z0-9])",
            re.IGNORECASE) if self.terms else None
        phrases = sorted({a.strip() for a in allowed or [] if a.strip()}, key=len, reverse=True)
        self.allowed = re.compile("|".join(self._phrase(a) for a in phrases),
                                  re.IGNORECASE) if phrases else None
        self.allowed_count = len(phrases)
        # an allow-list line that is an e-mail address permits that address as identity
        self.allowed_emails = {a.lower() for a in phrases
                               if re.fullmatch(r"[^\s@<>]+@[^\s@<>]+", a)}
        self.findings: list[tuple[str, str]] = []

    @staticmethod
    def _phrase(text: str) -> str:
        """Build the regular expression of one allowed phrase.

        Word boundaries are required only where the phrase begins or ends
        with a letter or digit, so ``top-level`` does not cover ``stop-level``
        while ``key=`` may be followed by anything.

        Args:
            text: Allowed phrase.

        Returns:
            Regular expression.
        """
        start = r"(?<![a-z0-9])" if text[0].isalnum() else ""
        end = r"(?![a-z0-9])" if text[-1].isalnum() else ""
        return start + re.escape(text) + end

    @staticmethod
    def config_dir() -> Path:
        """Return the folder of the privacy lists.

        Returns:
            ``$XDG_CONFIG_HOME/session-snap/privacy`` (default ``~/.config/...``).
        """
        base = os.environ.get("XDG_CONFIG_HOME", "")
        root = Path(base) if os.path.isabs(base) else Path.home() / ".config"
        return root / "session-snap" / "privacy"

    @staticmethod
    def read_list(name: str) -> list[str]:
        """Read a local list.

        Args:
            name: ``denylist.txt`` or ``allowlist.txt``.

        Returns:
            Entries without comments and empty lines; empty if the file is missing.
        """
        try:
            with open(PrivacyScan.config_dir() / name, encoding="utf-8") as handle:
                return [entry for entry in (line.split("#", 1)[0].strip() for line in handle)
                        if entry]
        except FileNotFoundError:
            return []

    @staticmethod
    def snapshot_labels() -> list[str]:
        """Read the labels of the user's own snapshots.

        Returns:
            Labels of all snapshot files found (archived ones included).
        """
        base = os.environ.get("XDG_STATE_HOME", "")
        state = Path(base) if os.path.isabs(base) else Path.home() / ".local" / "state"
        folders = [state / "session-snap" / "snapshots",
                   Path.home() / "scripts" / "session-snap.d" / "snapshots"]
        labels = []
        for folder in folders:
            for path in folder.rglob("session-snap_*.json") if folder.is_dir() else []:
                try:
                    label = json.loads(path.read_text(encoding="utf-8")).get("label")
                except (OSError, ValueError, AttributeError):
                    continue
                if isinstance(label, str) and label.strip():
                    labels.append(label.strip())
        return labels

    def _git(self, *args: str) -> str:
        """Run a git command in the repository.

        Args:
            *args: git arguments.

        Returns:
            Standard output.
        """
        git = shutil.which("git") or "git"
        return subprocess.run([git, "-C", self.repo, *args], capture_output=True, text=True,
                              errors="surrogateescape", check=True).stdout

    def _text_of(self, name: str, data: bytes) -> str | None:
        """Return the searchable text of a file content.

        Args:
            name: File path (decides the PDF conversion).
            data: Content.

        Returns:
            Text, or None for binary content that cannot be converted.
        """
        if name.lower().endswith(".pdf"):
            tool = shutil.which("pdftotext")
            if tool is None:
                print(f"warning: pdftotext missing, not scanned: {name}", file=sys.stderr)
                return None
            return subprocess.run([tool, "-", "-"], input=data, capture_output=True,
                                  check=False).stdout.decode("utf-8", errors="replace")
        if b"\0" in data:
            return None
        return data.decode("utf-8", errors="replace")

    def blobs(self) -> dict[str, str]:
        """List every file content of the whole history once.

        Returns:
            Blob hash -> one path under which it appears.
        """
        out: dict[str, str] = {}
        for line in self._git("rev-list", "--all", "--objects").splitlines():
            parts = line.split(" ", 1)
            if len(parts) == 2 and parts[1]:
                out.setdefault(parts[0], parts[1])
        return out

    def read_blobs(self, hashes: list[str]) -> dict[str, bytes]:
        """Read the contents of objects with one ``git cat-file --batch``.

        Args:
            hashes: Object names.

        Returns:
            Object name -> content (blobs only).
        """
        git = shutil.which("git") or "git"
        res = subprocess.run([git, "-C", self.repo, "cat-file", "--batch"],
                             input="".join(h + "\n" for h in hashes).encode(),
                             capture_output=True, check=True)
        data, pos, out = res.stdout, 0, {}
        while pos < len(data):
            end = data.index(b"\n", pos)
            head = data[pos:end].decode().split()
            pos = end + 1
            if len(head) < 3 or head[1] == "missing":
                continue
            size = int(head[2])
            if head[1] == "blob":
                out[head[0]] = data[pos:pos + size]
            pos += size + 1
        return out

    def _search(self, location: str, text: str) -> None:
        """Record every term found in a text.

        Args:
            location: Where the text comes from.
            text: Text to search.
        """
        if self.pattern is None:
            return
        for number, line in enumerate(text.splitlines(), 1):
            if self.allowed is not None:
                line = self.allowed.sub(lambda m: " " * len(m.group(0)), line)
            for match in self.pattern.finditer(line):
                self.findings.append((f"{location}:{number}", match.group(1)))

    def identities(self) -> list[tuple[str, str, str]]:
        """Authors, committers and taggers of the whole history.

        Returns:
            ``(where, name, email)`` for every distinct identity.
        """
        seen: dict[tuple[str, str], str] = {}
        fmt = "%h%x00%an%x00%ae%x00%cn%x00%ce"
        for line in self._git("log", "--all", f"--format={fmt}").splitlines():
            parts = line.split("\0")
            if len(parts) == 5:
                seen.setdefault((parts[1], parts[2]), f"author of commit {parts[0]}")
                seen.setdefault((parts[3], parts[4]), f"committer of commit {parts[0]}")
        tags = self._git("for-each-ref", "refs/tags",
                         "--format=%(refname:short)%00%(taggername)%00%(taggeremail)")
        for line in tags.splitlines():
            parts = line.split("\0")
            if len(parts) == 3 and parts[1]:
                seen.setdefault((parts[1], parts[2].strip("<>")), f"tagger of {parts[0]}")
        return [(where, name, email) for (name, email), where in sorted(seen.items())]

    def check_identities(self, allowed_emails: set[str]) -> None:
        """Search identities for terms and report e-mail addresses that are not public.

        Args:
            allowed_emails: Addresses from the allow list (lower case).
        """
        for where, name, email in self.identities():
            self._search(f"identity ({where})", f"{name} <{email}>")
            low = email.lower()
            if low in allowed_emails or any(fnmatch.fnmatch(low, p) for p in PUBLIC_EMAILS):
                continue
            self.findings.append((f"identity ({where})",
                                  f"e-mail address {email} is not a no-reply address"))

    def run(self) -> int:
        """Scan contents, paths, commit messages and tags of the whole history.

        Returns:
            0 if clean (or skipped in CI), 1 if terms were found, 2 without local terms.
        """
        if self.pattern is None:
            where = f"{self.config_dir() / 'denylist.txt'}, snapshot labels"
            if os.environ.get("CI") == "true":
                print(f"privacy scan skipped in CI: no local terms ({where})")
                return 0
            print(f"privacy scan FAILED: no local terms ({where}) - the scan would check "
                  "nothing. Create the deny list (one term per line).", file=sys.stderr)
            return 2
        blobs = self.blobs()
        paths = sorted(set(blobs.values()))
        for path in paths:
            self._search(f"path {path}", path)
        contents = self.read_blobs(list(blobs))
        for sha, data in contents.items():
            text = self._text_of(blobs[sha], data)
            if text is not None:
                self._search(f"{blobs[sha]} (blob {sha[:10]})", text)
        self._search("commit messages", self._git("log", "--all", "--format=commit %h%n%B"))
        self._search("tag annotations",
                     self._git("for-each-ref", "refs/tags", "--format=%(refname) %(contents)"))
        self.check_identities(self.allowed_emails)
        for location, term in self.findings:
            print(f"{location}: {term}")
        print(f"privacy scan: {len(self.terms)} term(s), {self.allowed_count} allowed "
              f"phrase(s), {len(contents)} file version(s) in the history, "
              f"{len(self.findings)} finding(s)")
        return 1 if self.findings else 0


def main() -> int:
    """Command-line entry point.

    Returns:
        Exit code.
    """
    repo = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
    terms = PrivacyScan.read_list("denylist.txt") + PrivacyScan.snapshot_labels()
    return PrivacyScan(repo, terms, PrivacyScan.read_list("allowlist.txt")).run()


if __name__ == "__main__":
    sys.exit(main())
