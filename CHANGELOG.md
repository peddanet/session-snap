# Changelog

All notable changes of session-snap. Dates are those of the delivery; versions before 1.8.0
were delivered as single files and imported into the repository afterwards (see
[CONTRIBUTING.md](CONTRIBUTING.md#history)). The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/); the project uses
[Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Added

- Progress display above all windows while session-snap works: spinner, progress bar
  "n / m" and time left; blocks the keyboard (style `fullscreen` also the mouse) except Esc,
  Ctrl+C and the button *Abbrechen*, which interrupt like Ctrl+C. A Wayland layer-shell
  surface drawn by `overlay_gtk.py` with the system Python (GTK 3, gtk-layer-shell); falls
  back to the desktop notification if that is not possible.
- `session-snap desktop-entry [--remove]`: entry with icon in the application menu that
  opens the menu in a terminal (cosmic-term, else x-terminal-emulator); `install.sh` runs it.
- Log line `reorder timing` with the time spent waiting for windows and activating them.

### Changed

- Config key `overlay` takes `fullscreen` (new default), `corner`, `notify` (the
  notification of 2.0) or `off`; `yes` means `fullscreen`, `no` means `off`. The
  notification shows "(n / m)" during steps with a count.

### Fixed

- install.sh stopped when it moved the test file of 1.8.0: the comment behind its
  VERSION line became part of the backup name. Only the version number is taken now.

## [2.0.0] - 2026-11-22

### Added

- `install.sh`: installs a release tag into `~/.local/share/session-snap/venv` and links
  `~/.local/bin/session-snap`; moves the single-file program of 1.8.0 out of `~/scripts`
  and rewrites the autostart entry of the watcher.
- `session-snap update` (newer release first, then delivered patch archives) and
  `session-snap reload` (live update of the running `watch` by self-restart, same PID).
- `tools/release.sh` (GitHub variant) and `tools/same-code.sh` from the dev-conventions v6,
  `tools/after-install.sh`, pre-push hook `.githooks/pre-push`, quality gate `shell`
  (shellcheck), privacy scan over the whole history (`tools/privacy_scan.py`), CI with
  Python 3.12 and 3.14.
- Kill compares the selection with what is open now: if more is open than the snapshot
  holds, it offers to save a new snapshot first; windows that close as a side effect are
  listed and confirmed; a browser outside the chosen categories needs an explicit yes.

### Changed

- Package `session_snap` in `src/` (version in `src/session_snap/__init__.py`), tests with
  pytest and at least 90 % branch coverage, Google-style docstrings checked by pylint.
- XDG layout: configuration in `~/.config/session-snap/`, snapshots and logs in
  `~/.local/state/session-snap/`; the files of `~/scripts/session-snap.d` move once on the
  first start, never overwriting.
- `--install` and the update from the download folder are gone: `--install` explains
  `install.sh`, `--update` is an alias of `update`.

### Fixed

- Under heavy load the window list of the compositor timed out after 2 s and image viewers
  with one process for all windows looked as if they showed only their first image; the list
  is retried with 6 s, an incomplete capture is announced, and saving asks before writing it.
- The tests no longer depend on the running desktop session: without isolation they saw the
  real Wayland compositor, the real cosmic-panel and a running Chrome and failed on a
  desktop (restore checklist, app window relaunch, appearance variables).

## [1.8.0] - 2026-11-13

### Added

- Public repository with the history of the available versions as tagged commits,
  branches `main` and `develop`.
- Quality gates in `tools/check.sh`: privacy, format (100 characters), pylint 10.00/10, mypy,
  bandit, tests with branch coverage (floor 60 %, measured 64 %); pre-push hook
  `tools/hooks/pre-push`; GitHub Actions workflow with Python 3.12.
- `docs/user.md`, `docs/developer.md`, `docs/glossary.md`, `CONTRIBUTING.md`, this changelog.
- `examples/demo-snapshot.json` and `examples/make_demo_media.py` (Lorem-ipsum images, test
  videos, localised snapshot).

### Changed

- English docstrings for every function and class; type annotations where mypy needed them.
- Internal renames without change in behaviour: `interactive()` → `interactive_menu()`,
  `_Wl._send()` → `_Wl.send()`.
- Test data contain no personal names (browser profiles *Lorem*, *Ipsum*).

### Unchanged

- Behaviour, command line, file formats; 67 tests.

## [1.7.11] - 2026-11-02

- Documentation of the same version installed with the program (or reported missing)
- 67 tests

## [1.7.10] - 2026-11-01

- Complete paths in the tree selection
- Documentation and primer updated

## [1.7.9] - 2026-11-01

- Browser session restore (Chrome, Firefox)
- 66 tests

## [1.7.8] - 2026-11-01

- Chrome identity without `--profile-directory`
- 64 tests

## [1.7.7] - 2026-11-01

- Snapshot management (`manage`)
- 63 tests

## [1.7.6] - 2026-11-01

- Tree selection for restore
- Nomacs `--fullscreen`
- Background browser not "running"
- 62 tests

## [1.7.5] - 2026-11-01

- App windows via window list
- Better window matching
- SIGKILL without question for players/viewers
- Busy overlay
- 61 tests

## [1.7.4] - 2026-11-01

- Eog `--fullscreen`
- 58 tests

## [1.7.3] - 2026-11-01

- Restore menu "n von m – x laufen bereits"
- 57 tests

## [1.7.2] - 2026-11-01

- Unique version number (same content as the last 1.7.1 delivery)

## [1.7.1] - 2026-11-01

- VLC endless loop
- Images fullscreen
- Defects #20, #21
- Tree selection for terminate
- 56 tests

## [1.7.0] - 2026-10-28

- Alt-Tab order: background process `session-snap watch` with autostart, positions in snapshots, rebuild on restore with choice and fallback
- 52 tests

## [1.6.1] - 2026-10-28

- Appearance taken from the desktop session (follows light/dark)
- 50 tests

## [1.6.0] - 2026-10-28

- Everything in `~/scripts` (`session-snap`, `test_session_snap`, `session-snap.d/`)
- `--install` with migration
- Update at start and `--update`
- 49 tests

## [1.5.1] - 2026-10-28

- Appearance variables recorded and replayed
- 46 tests

## [1.5.0] - 2026-10-26

- All images of single-instance viewers from window titles
- VLC one window per video
- Windows and appearance variables in `diagnose`
- 45 tests

## [1.4.0] - 2026-10-20

- Uniform maximizing through COSMIC, built-in Wayland client, simulated compositor, 41 tests

## [1.3.0] - 2026-10-20

- Restart with original options, paused start, origin of a process, `diagnose`, 38 tests (intermediate delivery)

## [1.2.0] - 2026-10-15

- Browser category with app-window relaunch, program choice for videos/images, program-independent signature, 33 tests

## [1.1.0] - 2026-10-14

- `kill`, storage rule, installer with idempotent `PATH` block, double-fork launch, 27 tests, documentation

## [1.0.0] - 2026-10-14

- Capture (VLC, images, rest), snapshots, restore with checklist


[1.8.0]: https://github.com/peddanet/session-snap/releases/tag/v1.8.0
[1.7.11]: https://github.com/peddanet/session-snap/releases/tag/v1.7.11
[1.7.10]: https://github.com/peddanet/session-snap/releases/tag/v1.7.10
[1.7.9]: https://github.com/peddanet/session-snap/releases/tag/v1.7.9
[1.7.8]: https://github.com/peddanet/session-snap/releases/tag/v1.7.8
[1.7.7]: https://github.com/peddanet/session-snap/releases/tag/v1.7.7
[1.7.6]: https://github.com/peddanet/session-snap/releases/tag/v1.7.6
[1.7.5]: https://github.com/peddanet/session-snap/releases/tag/v1.7.5
[1.7.4]: https://github.com/peddanet/session-snap/releases/tag/v1.7.4
[1.7.3]: https://github.com/peddanet/session-snap/releases/tag/v1.7.3
[1.7.2]: https://github.com/peddanet/session-snap/releases/tag/v1.7.2
[1.7.1]: https://github.com/peddanet/session-snap/releases/tag/v1.7.1
[1.7.0]: https://github.com/peddanet/session-snap/releases/tag/v1.7.0
[1.6.1]: https://github.com/peddanet/session-snap/releases/tag/v1.6.1
[1.6.0]: https://github.com/peddanet/session-snap/releases/tag/v1.6.0
[1.5.1]: https://github.com/peddanet/session-snap/releases/tag/v1.5.1
[1.5.0]: https://github.com/peddanet/session-snap/releases/tag/v1.5.0
[1.4.0]: https://github.com/peddanet/session-snap/releases/tag/v1.4.0
[1.3.0]: https://github.com/peddanet/session-snap/releases/tag/v1.3.0
[1.2.0]: https://github.com/peddanet/session-snap/releases/tag/v1.2.0
[1.1.0]: https://github.com/peddanet/session-snap/releases/tag/v1.1.0
[1.0.0]: https://github.com/peddanet/session-snap/releases/tag/v1.0.0
