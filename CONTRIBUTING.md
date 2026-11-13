# Contributing to session-snap

## Design decisions

- **One executable file, no package.** `session-snap` installs and updates itself by copying
  this one file from the download folder into `~/scripts/`; a package or a virtual environment
  would break that. The usual rule "one class per module" therefore does not apply.
- **Standard library only** for the tool and the test suite. The check tools (pylint, mypy,
  bandit, coverage) are development dependencies only.
- **User interface German, code and documentation English** (identifiers, docstrings, comments,
  `docs/`).
- **Normal user only.** The tool refuses to run as root; so do the tests and `tools/check.sh`.

## Branches, commits and tags

| Branch | Content |
|--------|---------|
| `main` | released versions only; every release is one commit with an annotated tag `v<version>` |
| `develop` | integration branch; work happens here or in short-lived branches off `develop` |

Commit messages: first line `session-snap <version>: <summary>` for a release, otherwise a
short imperative summary (≤ 72 characters); then a blank line and the changes as a list.
Raise `VERSION` in **both** `session-snap` and `test_session_snap` (test T49 checks that they
are equal) and add an entry to `CHANGELOG.md`. Every delivered file with a different content
gets a new version number.

## Quality gates

`tools/check.sh` runs six stages; all must pass before a push (pre-push hook) and on GitHub
Actions (Python 3.12):

| Stage | Criterion |
|-------|-----------|
| `privacy` | no personal data in tracked files (see below) |
| `format` | ≤ 100 characters per line, no tabs, no trailing white space |
| `lint` | pylint 10.00/10; every `# pylint: disable=` is justified where it is used |
| `types` | mypy without findings |
| `security` | bandit without findings; every `# nosec` names the check and the reason |
| `tests` | all tests pass with `-W error::ResourceWarning`; branch coverage ≥ 60 % |

Set-up once per clone, as the normal user (only `python3-venv` needs `sudo`, because it
installs a system package):

~~~bash
sudo apt install python3-venv
cd ~/Projects/session-snap
python3 -m venv .venv-check
.venv-check/bin/python -m pip install -r tools/requirements-check.txt
git config core.hooksPath tools/hooks
~~~

- `python3 -m venv .venv-check` creates a private Python environment for the check tools inside
  the clone (ignored by git); the tool itself never uses it.
- `pip install -r tools/requirements-check.txt` installs exactly the pinned versions.
- `git config core.hooksPath tools/hooks` makes git run `tools/hooks/pre-push` before every push.

Then:

~~~bash
tools/check.sh              # all stages
tools/check.sh lint types   # selected stages
~~~

## Privacy

This repository is public. It must never contain real snapshots, logs, configuration, user
names, home directories, e-mail addresses, telephone numbers or names of people. Example data
are invented (Lorem ipsum); paths use `/home/USER`. The `privacy` stage checks generic patterns;
personal words that must never appear go into the file `.privacy-patterns` in the root of your
clone (one extended regular expression per line). That file is ignored by git and therefore
never published.

## Tests

~~~bash
python3 test_session_snap -v
~~~

The tests start only dummy processes (copies of the Python interpreter in a temporary
directory) and a simulated COSMIC compositor; real desktop programs are never recorded or
signalled. Run them as the normal user. A new feature needs a test following the existing
pattern (see [developer guide](docs/developer.md#sec-verification)).

<a id="history"></a>

## History

session-snap was developed from 14 October 2026 and delivered as single files until 1.7.11.
The repository history was built afterwards from the delivered files that were still available:
each such version is one commit on `main` with its original date and an annotated tag. Versions
whose files were not available are described in [CHANGELOG.md](CHANGELOG.md) only. Test data in
historical commits were cleaned of personal names before the import.
