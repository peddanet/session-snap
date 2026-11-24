# Contributing to session-snap

session-snap follows the author's dev-conventions (handover v6): git-flow with `main` and
`develop`, releases only through `tools/release.sh`, seven quality gates, installation from
release tags, `session-snap update`. This file holds what a contributor needs; the developer
guide [docs/developer.md](docs/developer.md) explains the design.

## Rules of the project

- **Normal user only.** The tool refuses to run as root; so do the tests and `tools/check.sh`.
- **User interface German, code and documentation English** (identifiers, docstrings in
  Google style, comments, `docs/`).
- **Standard library only** for the program; the check tools are development dependencies
  (extra `dev`).
- **Nothing loose in the home directory.** Deliveries and hand-over archives go to
  `~/Downloads`, temporary work into a folder from `mktemp -d`.
- **Every delivered change has its own version**, set only by `tools/release.sh`.

## Branches

| Branch | From | Into | Purpose |
|---|---|---|---|
| `main` | - | - | releases only; every release is an annotated tag `vX.Y.Z` |
| `develop` | `main` | `main` | integration of finished features |
| `feature/<name>` | `develop` | `develop` (rebase + `--no-ff`) | one change |
| `hotfix/<name>` | `main` | `main` + `develop` | urgent fix of the released version, always a patch |

`main` and `develop` are protected on GitHub (pull requests, required checks
`gates (3.12)` and `gates (3.14)`, branches up to date); they change only through
`tools/release.sh`.

## Development set-up (once per clone, normal user)

~~~bash
sudo apt install shellcheck python3-venv
cd ~/Projects/session-snap && python3 -m venv .venv && .venv/bin/pip install -e '.[dev]' && git config core.hooksPath .githooks
~~~

- `sudo` only for the two packages, because apt writes to `/usr`.
- `.venv` holds the check tools (pytest, pytest-cov, pylint, mypy, pycodestyle, bandit) and
  the package in editable mode; it is ignored by git and never the installed program.
- `git config core.hooksPath .githooks` makes git run all gates before every push.

### Local files of a developer

Everything needed to build, test and release is in the repository; the only files you
create yourself are the two privacy lists. Samples of every file that lives outside the
clone are in `examples/`:

| File on your machine | Who writes it | Sample in the repository | Needed for |
|---|---|---|---|
| `~/.config/session-snap/privacy/denylist.txt` | **you, once** (mode 600) | `examples/privacy/denylist.sample.txt` | privacy gate: without a term it fails |
| `~/.config/session-snap/privacy/allowlist.txt` | you, when needed | `examples/privacy/allowlist.sample.txt` | privacy gate: phrases in which a denied term is allowed |
| `~/.config/session-snap/config.ini` | `session-snap config`, then you | `examples/config.ini.sample` (identical to the built-in template) | optional settings |
| `~/.config/session-snap/programs.ini` | session-snap (menu "Programme waehlen") | `examples/programs.ini.sample` | program choice for videos and images |
| `~/.local/state/session-snap/` | session-snap | `examples/demo-snapshot.json` (Lorem ipsum) | snapshots, logs, Alt-Tab order |
| `~/.local/share/applications/session-snap.desktop`, `~/.local/share/icons/hicolor/scalable/apps/session-snap.svg` | `session-snap desktop-entry` | `src/session_snap/data/session-snap.svg` | menu entry and icon |

Create the privacy lists from the samples, then replace the sample terms with your own
(real name, user name, host name, e-mail addresses, private folder names). An e-mail
address on its own line in the allow list may appear as author or committer:

~~~bash
mkdir -p ~/.config/session-snap/privacy && cp -n examples/privacy/denylist.sample.txt ~/.config/session-snap/privacy/denylist.txt && cp -n examples/privacy/allowlist.sample.txt ~/.config/session-snap/privacy/allowlist.txt && chmod 600 ~/.config/session-snap/privacy/*.txt
~~~

- Normal user: the files are yours and lie below `~/.config`.
- `cp -n` never overwrites lists you already have.
- The full folder layout (XDG base directories, what lives where and why) is in
  [docs/user.md, folder layout](docs/user.md#sec-layout).

## Quality gates

`tools/check.sh` (`--fast` skips the tests) runs locally, in the pre-push hook and in CI:

| Gate | Tool | Pass criterion |
|---|---|---|
| privacy | `tools/privacy_scan.py` | no local term in any file, path, commit message, tag or identity (author, committer, tagger) of the whole history; every e-mail address of an identity is a no-reply address or on the allow list; fails if the local deny list is missing or empty (skipped only in CI, `CI=true`) |
| shell | shellcheck | no finding |
| format | pycodestyle | no finding, line length 100 |
| lint | pylint | 10.00/10 incl. Google-style docstrings |
| types | mypy | no error |
| security | bandit | no finding |
| tests | pytest + coverage | all pass, branch coverage at least 90 % |

**Each tree is checked in full once.** One release calls `tools/check.sh` up to five
times for only two different trees (feature, release). After a full green run the script
stores the tree of the working copy, the Python version and a hash of the installed packages
in `.git/checked-trees`; for a stored tree only `privacy` and `shell` run again - the privacy
scan also reads commit messages and tags, which change without the tree - and the script says
that the other gates were skipped. `CHECK_FULL=1 tools/check.sh` forces a full run; CI always
runs in full.

## Privacy

This repository is public. It must never contain real snapshots, logs, configuration, user
names, home directories or names of people; example data are invented (Lorem ipsum), paths
use `/home/USER`. The privacy gate reads the local deny list
`~/.config/session-snap/privacy/denylist.txt` and the allow list next to it (one term per
line, `#` starts a comment) plus the labels of your own snapshots; the lists are never part
of the repository.

## Integrating and releasing (`tools/release.sh`)

All steps run as a normal user. `tools/release.sh` looks at the current
branch and does what fits it; the quality gates always run before anything
is merged, and nothing is pushed or installed without a question.

| Current branch   | What the script does                                                   |
|------------------|------------------------------------------------------------------------|
| `feature/<name>` | rebase onto `develop`, gates, merge `--no-ff` into `develop`, then asks "Release jetzt bauen?" |
| `develop`        | release of the collected features: asks the part (default minor) and the release message |
| `hotfix/<name>`  | patch release into `main` and back into `develop`                       |
| `main`           | refused                                                                |

A release computes the version from the newest tag on `main` and the part
(`patch` 1.1.2 -> 1.1.3, `minor` -> 1.2.0, `major` -> 2.0.0; never edit
`__version__` by hand), sets the CHANGELOG heading, runs the gates, merges
into `main` with the message `session-snap X.Y.Z: <release message>`, tags
`vX.Y.Z` with the same annotation, brings `develop` up to `main`, checks
with `tools/same-code.sh` that `main`, `develop` and the tag are
bit-identical and then asks before the push and before `./install.sh vX.Y.Z`.

### Feature (normal case)

~~~bash
cd ~/Projects/session-snap
git switch develop && git pull --ff-only && git switch -c feature/<short-name>
# ... change code and tests, add a CHANGELOG line under [Unreleased] ...
tools/check.sh && git add -A && git commit -m "<what and why>"
tools/release.sh
~~~

Answer `n` to "Release jetzt bauen?" to collect further features on
`develop` (optionally push `develop`); answer `j` to release at once. To
release the collected features later:

~~~bash
git switch develop && tools/release.sh
~~~

### Hotfix

A hotfix starts from `main`, so that it never ships unreleased features of
`develop`; the script refuses a hotfix branch that contains them.

~~~bash
git switch main && git pull --ff-only && git switch -c hotfix/<short-name>
# ... fix, test, CHANGELOG line under [Unreleased] ...
tools/check.sh && git add -A && git commit -m "Fix: <what>"
tools/release.sh -m "<release message>"
~~~

If `develop` already holds changes that conflict with the fix (often the
CHANGELOG), the script stops after tagging, leaves `main` and the tag local
and prints how to finish the merge into `develop` by hand.

### Without questions

~~~bash
tools/release.sh minor -m "<release message>" --yes
~~~

`--yes` answers every question with yes (release, push, install) and needs
`-m`. `tools/release.sh --help` lists all options.

`tools/same-code.sh A B [C ...]` answers JA or NEIN for any branches, tags
or commits: equal tree hashes mean identical files, even though the commit
hashes of merges differ.

### Which part?

| Part | The change ... |
|---|---|
| `patch` | corrects wrong behaviour; only tests, scripts or documentation |
| `minor` | adds something usable; everything that worked before still works |
| `major` | makes something that worked before stop working or behave differently |

The effect decides, not the place of the change; several rows: the highest.

### Patches from outside

Patches delivered by Claude come as archive `session-snap-<kind>-<name>.tar.gz`
with the patches and `patch.info` (branch, base and result hashes, part,
release message), never with a version number. `session-snap update` first
installs a newer release if there is one, then asks once whether to apply
the archive, checks the hashes, applies it with `git am` on its branch and
runs `tools/release.sh` with the part and message from `patch.info`.


### Gates before every push

`tools/check.sh` runs all gates, including `shell` (shellcheck over every
shell script; `sudo apt install shellcheck` once). It activates the
development venv `.venv` by itself, so no `source .venv/bin/activate` is
needed before it, before `git push` (pre-push hook) or before
`tools/release.sh`.


<a id="history"></a>

## History

session-snap was developed from 14 October 2026 and delivered as single files until 1.7.11.
The repository history was built afterwards from the delivered files that were still
available: each such version is one commit on `main` with its original date and an annotated
tag. Versions whose files were not available are described in [CHANGELOG.md](CHANGELOG.md)
only. Test data in historical commits were cleaned of personal names before the import.
2.0.0 is the first release built with `tools/release.sh`.
