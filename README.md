# session-snap

Record the open videos, images, browsers and other desktop programs of a session, restore them
after a cold boot, and terminate them on request — a lightweight replacement for hibernation on
**Pop!_OS 24.04 with the COSMIC desktop (Wayland)**.

session-snap is **one executable Python 3 file** for the normal desktop user, standard library
only. Its user interface is German; the documentation is English.

## Features

- **Snapshots** of all open videos, images, browsers and other programs, with complete file
  paths and the exact command line, stored as time-stamped JSON files.
- **Restore** everything, one category, or a selection in a keyboard-driven tree (all programs →
  categories → programs). Every file reopens with the program and the options it was shown with;
  videos start **paused**, video windows are **maximized** and images open **fullscreen** through
  the COSMIC compositor.
- **Browser sessions**: Chrome with all last profiles, windows and tabs; Firefox per profile.
  Chrome app windows (for example Claude, WhatsApp) are recorded and started again.
- **All images of single-instance viewers** (eog) are recovered from the window titles.
- **Alt-Tab order**: a small background process records window activations; a restore rebuilds
  the order.
- **Terminate** the recorded programs — never the terminal it runs in — with automatic SIGKILL
  only for players and viewers that ignore SIGTERM.
- Restored programs follow the **current light/dark theme**; a busy notice above all windows
  offers a cancel button.
- **Self-install and update** from the download folder, including the documentation of the same
  version.

## Requirements

| Item | Requirement |
|------|-------------|
| Desktop | Pop!_OS 24.04 LTS with COSMIC (Wayland). Without COSMIC, window functions are skipped. |
| Python | Python 3 of the system; tested with 3.11, 3.12 and 3.13. No third-party packages. |
| User | Runs as the normal desktop user; refuses to run as root. |

## Installation

Download `session-snap` and `test_session_snap` from the
[latest release](https://github.com/peddanet/session-snap/releases/latest), then install as
the normal user (the downloaded file is not executable yet, hence `python3`):

~~~bash
python3 ~/Downloads/session-snap --install
~~~

Or from a clone of this repository:

~~~bash
git clone https://github.com/peddanet/session-snap.git ~/Projects/session-snap
python3 ~/Projects/session-snap/session-snap --install
~~~

`--install` copies the program into `~/scripts/` (a copy, not a link), puts `~/scripts` into
`PATH`, and keeps all data in `~/scripts/session-snap.d/`. Details:
[installation](docs/user.md#sec-installation).

## Usage

~~~bash
session-snap                      # interactive main menu
session-snap save -l "Evening"    # record the session with a label
session-snap restore -s latest    # restore the newest snapshot
session-snap kill -s latest       # terminate the recorded programs
session-snap --help
~~~

A safe way to try it: [`examples/`](examples/) contains a demo snapshot and a script that
generates Lorem-ipsum images and short test videos for it.

~~~bash
python3 examples/make_demo_media.py ~/session-snap-demo
session-snap show ~/session-snap-demo/demo-snapshot.json
session-snap restore -s ~/session-snap-demo/demo-snapshot.json --dry-run
~~~

In 1.8.0, a snapshot given as a path is accepted only if at least one snapshot of your own exists
(`session-snap save` once); `--dry-run` starts nothing.

## Documentation

| Document | Content |
|----------|---------|
| [docs/user.md](docs/user.md) | User guide: installation, commands, interface, configuration, 20 use cases, troubleshooting |
| [docs/developer.md](docs/developer.md) | Developer guide: platform analysis, architecture, data model, algorithms, verification |
| [docs/glossary.md](docs/glossary.md) | Every technical term with a link for further reading |
| [CHANGELOG.md](CHANGELOG.md) | Version history |
| [CONTRIBUTING.md](CONTRIBUTING.md) | Branches, commits, quality gates |

The complete documentation and the *Code Primer* (PDF and Markdown, with rendered diagrams,
index and source listing) are attached to every
[release](https://github.com/peddanet/session-snap/releases).

## Development

The code must pass six quality gates — privacy, format, pylint 10.00/10, mypy, bandit, tests
with branch coverage — locally and on GitHub Actions:

~~~bash
tools/check.sh
~~~

See [CONTRIBUTING.md](CONTRIBUTING.md) for the set-up of the check tools and the pre-push hook.

## Licence

No licence has been chosen yet; until then, all rights are reserved by the author.
