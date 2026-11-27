# Changelog

All notable changes of session-snap. Dates are those of the delivery; versions before 1.8.0
were delivered as single files and imported into the repository afterwards (see
[CONTRIBUTING.md](CONTRIBUTING.md#history)). The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/); the project uses
[Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Added

- `kill`: single image windows are closed through COSMIC (request `close` of
  `zcosmic_toplevel_manager_v1`). An image viewer that also shows images which were not chosen
  or are not in the snapshot keeps running, and only the chosen image windows close; a viewer
  whose open images are all chosen ends as a whole. Without COSMIC window control the viewer
  ends as before and the side effects name the other images.

### Fixed

- Tests T44 and T56 failed on a desktop with an image viewer open: the capture saw the
  real viewer, which took over the windows of the test's dummy viewer (T44) and added an
  image the kill dialog asked about (T56). The suite now scans only its own dummies, also
  in every child (`SESSION_SNAP_ONLY_PATH`). The failures were taken for an effect of high
  system load before.
- `kill` counted image viewer processes instead of images: a snapshot with 19 images in two
  viewer processes showed "Bilder (2/2)". Images are now offered and counted one by one.

- `tools/release.sh` (dev-conventions bundle v20) counts only the required checks of a pull
  request. When `develop` held nothing new, `merge/vX.Y.Z` was the tagged commit and the
  finished tag run on it ended the wait before the checks of the pull request had started
  (2.1.4: the merge back into `develop` was refused five times).

## [2.1.4] - 2026-11-26

### Changed

- Development tools follow dev-conventions bundle v19. `tools/check.sh` prints the gates in
  the common console look (numbered stages, one PASS/FAIL/SKIP line each, result block) and
  runs a single gate with `--only <gate>`; `tools/release.sh` (GitHub variant), `tools/same-code.sh`
  and the new `tools/output.sh` come from the bundle, with English dialogs (`[y/N]`), a
  watcher of the GitHub checks and a time measurement of every step. While it waits for
  GitHub, `tools/release.sh` shows every answer of `gh` and a progress line per CI job
  (bar of the finished steps, running step, seconds since the job started); it works with
  gh 2.45 of Ubuntu 24.04, which has no `gh pr checks --json`.
- Line length 120 for pycodestyle and pylint (was 100).
- CI runs one step per gate (`gate: <name>`), so GitHub shows which gate failed; the
  triggers are unchanged (pull requests into `main`/`develop`, release check on tags).
- pytest shows a progress bar and the five slowest tests (`tools/pytest_progress.py`,
  loaded from `tests/conftest.py`; `PYTEST_PROGRESS=0` switches it off).

### Added

- `tools/review.sh <base> <branch>`: four-eyes review of two refs in Beyond Compare, both
  sides as named real copies.

## [2.1.3] - 2026-11-24

### Fixed

- Privacy gate: also checks the identities of the whole history (author and committer of
  every commit, tagger of every tag) against the deny list, and refuses any e-mail address
  that is not a no-reply address (`*@users.noreply.github.com`, `noreply@github.com`,
  `noreply@anthropic.com`) or on the allow list. A real address from the global git
  configuration no longer reaches the public history unnoticed.

## [2.1.2] - 2026-11-24

### Changed

- `tools/check.sh` runs format, lint, types, security and tests only once per tree: a tree
  that passed all gates (same Python, same packages) gets only privacy and shell again;
  `CHECK_FULL=1` forces a full run, CI always runs in full. A release now needs two full
  runs instead of five.
- The icon is a file of the package (`src/session_snap/data/session-snap.svg`) instead of a
  string in the code; `desktop-entry` copies it.
- Samples of every file outside the clone in `examples/` (`config.ini.sample`,
  `programs.ini.sample`, `privacy/denylist.sample.txt`, `privacy/allowlist.sample.txt`) and
  the social preview of GitHub with its SVG source in `docs/images/`; CONTRIBUTING explains
  where each local file goes.

## [2.1.1] - 2026-11-24

### Changed

- README: what session-snap is for, three screenshots (main menu, picking files to restore,
  progress display) made from the Lorem-ipsum demo data, requirements of the progress display.
- Overlay style `fullscreen`: the mouse pointer is hidden over the dimmed area and shown
  only over the card with the button *Abbrechen*.

### Fixed

- Overlay style `fullscreen`: the dim layer over the screens was not drawn (the window was
  marked as painted by the program itself, so GTK skipped its translucent background); only
  the card was visible.
- Privacy gate: without any local term (deny list missing or empty) the scan now fails
  instead of passing; only in CI (`CI=true`) it is skipped, as there is no local list there.
- Tests T18 and T56 (interactive menu in a pseudo-terminal) waited without limit for the
  child; now at most 30 s, then the child is killed and the test fails with the last terminal
  output and the kernel function the child waited in.

## [2.1.0] - 2026-11-24

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
