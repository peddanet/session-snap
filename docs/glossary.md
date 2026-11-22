<a id="sec-glossary"></a>

# Glossary

Every technical term used in this document, with a short explanation and a link for further reading. In the PDF, terms in the text link to their glossary entry, and the index lists the pages on which each term occurs.

<a id="gl-activation"></a>**Activation (window)**  
Making a window the active one, with keyboard focus — by Alt-Tab, a click, the dock, or the COSMIC request `activate`. The switcher's order follows the activations. → [Alt-Tab order](user.md#sec-order)

<a id="gl-alttab"></a>**Alt-Tab switcher**  
The window switcher of COSMIC, provided by the pop-launcher plugin `cosmic_toplevel`; it lists windows in [MRU](#gl-mru) order. → [[poplauncher]](#ref-poplauncher)

<a id="gl-appwindow"></a>**App window**  
A website installed as an application in Chrome (here: Claude), shown in its own window without browser controls. It runs inside the Chrome [browser process](#gl-browserproc) of its profile; its launcher entry carries `--app-id=…`. → [[chromiumudd]](#ref-chromiumudd)

<a id="gl-appid"></a>**app_id**  
Identifier a window reports to the compositor, usually the program or desktop-file name (e.g. `mpv`, `org.gnome.eog`); for X11 programs under XWayland the window class (e.g. `vlc`). Used to assign new windows to started programs. → [[wlinfo]](#ref-wlinfo)

<a id="gl-atomic"></a>**Atomic replace**  
Writing a file under a temporary name and renaming it over the target with `os.replace()`; readers see either the old or the complete new file. → [[pyos]](#ref-pyos)

<a id="gl-autostartentry"></a>**Autostart entry**  
A desktop entry in `~/.config/autostart/` that the desktop session starts at login; on systemd systems through `xdg-desktop-autostart.target`. → [[autostart]](#ref-autostart), [[xdgautostartgen]](#ref-xdgautostartgen)

<a id="gl-watcher"></a>**Background process (watcher)**  
`session-snap watch`: runs from the login, records every window activation and writes the current Alt-Tab order to `order.json`. → [Alt-Tab order](user.md#sec-order)

<a id="gl-browserproc"></a>**Browser process**  
The main process of a browser profile. All windows of that profile, normal windows and app windows, are served by it, and a signal to it ends all of them. → [[chromiumudd]](#ref-chromiumudd)

<a id="gl-category"></a>**Category**  
Classification of an entry: `vlc` (videos), `image`, `browser` or `rest`. → [classification](developer.md#sec-classification)

<a id="gl-compositor"></a>**Compositor**  
The display server of a Wayland desktop; it draws all windows and alone decides about their size and position (here: cosmic-comp). → [[wikiwayland]](#ref-wikiwayland), [[cosmiccomp]](#ref-cosmiccomp)

<a id="gl-cosmic"></a>**COSMIC**  
Desktop environment developed by System76 in Rust; default desktop of Pop!_OS 24.04, Wayland-native. → [[lwn]](#ref-lwn), [[register]](#ref-register)

<a id="gl-curses"></a>**curses**  
Library for text user interfaces in a terminal; Python's `curses` module wraps ncurses. → [[pycurses]](#ref-pycurses)

<a id="gl-dbus"></a>**D-Bus**  
The message bus of the desktop session through which programs call each other's services, e.g. the notification service `org.freedesktop.Notifications`. session-snap uses the command-line client `gdbus` (GLib) for it. → [[notifyspec]](#ref-notifyspec)

<a id="gl-decckm"></a>**DECCKM (application cursor keys)**  
Terminal mode in which cursor keys send `ESC O A/B/C/D` instead of `ESC [ A/B/C/D`. → [[ctlseqs]](#ref-ctlseqs)

<a id="gl-desktop"></a>**Desktop entry (.desktop file)**  
INI-style file under `applications/` in the XDG data directories that describes a launchable program (`Name`, `Exec`, `MimeType`, `NoDisplay`, …). → [[desktopkeys]](#ref-desktopkeys), [[basedir]](#ref-basedir)

<a id="gl-desktopid"></a>**Desktop file ID**  
Identifier of a desktop entry: its path below `applications/` with `/` replaced by `-`, e.g. `mpv.desktop`. → [[desktopspec]](#ref-desktopspec)

<a id="gl-notification"></a>**Desktop notification**  
A message box shown by the session's notification service above all windows; it can carry buttons ("actions") and can stay until it is closed (`expire_timeout` 0). Used by session-snap as the busy overlay. → [[notifyspec]](#ref-notifyspec), [[cosmicnotif]](#ref-cosmicnotif)

<a id="gl-dryrun"></a>**Dry run**  
Mode in which a command only prints what it would do (`--dry-run`). → [use case UC8](user.md#sec-usecases)

<a id="gl-inputrepeat"></a>**Endless loop (VLC)**  
`--input-repeat=N` makes VLC play the same input N more times by seeking back to its start; unlike `--loop`, a paused start is not repeated on every pass. → [[vlcinput]](#ref-vlcinput)

<a id="gl-envvar"></a>**Environment variable**  
Named value that a process inherits from its parent (e.g. `PATH`, `GTK_THEME`); readable for the owner in `/proc/<pid>/environ`. → [appearance](user.md#sec-look)

<a id="gl-escdelay"></a>**ESCDELAY**  
ncurses variable: milliseconds curses waits after an Esc byte for an escape sequence; session-snap uses 25 ms. → [[escdelay]](#ref-escdelay)

<a id="gl-exec"></a>**exec (process replacement)**  
System call that replaces the running program by another one in the same process; used after an update to continue with the new version. → [[pysubprocess]](#ref-pysubprocess)

<a id="gl-exitcode"></a>**Exit code**  
Numeric result of a process (0 = success); session-snap uses 0, 1, 2 and 130. → [[bashstartup]](#ref-bashstartup)

<a id="gl-fieldcode"></a>**Field code**  
Placeholder in the `Exec` key of a desktop entry, e.g. `%f` (one file) or `%U` (list of URLs), replaced when the program is started. → [[desktopspec]](#ref-desktopspec)

<a id="gl-fd"></a>**File descriptor (fd)**  
Kernel handle for an open file; visible to the owner as a symbolic link in `/proc/<pid>/fd/`. → [[procfd]](#ref-procfd)

<a id="gl-flatpak"></a>**Flatpak**  
Sandboxed application format; inside the sandbox `/.flatpak-info` names the application ID and the instance. → [[flatpakmeta]](#ref-flatpakmeta)

<a id="gl-flock"></a>**flock**  
Advisory file lock of the kernel, tied to an open file; released automatically when the process ends. Used to detect whether the background process runs. → [[flock2]](#ref-flock2)

<a id="gl-fnmatch"></a>**fnmatch pattern**  
Shell-style wildcard pattern (`*`, `?`, `[seq]`) used in `config.ini`. → [[pyfnmatch]](#ref-pyfnmatch)

<a id="gl-viewerfullscreen"></a>**Fullscreen mode of a viewer**  
The borderless view an image viewer shows after a double-click or F11; only the viewer itself can switch it on (eog and nomacs: `--fullscreen`). The compositor's fullscreen only enlarges the window. → [[eogsrc]](#ref-eogsrc), [[nomacssrc]](#ref-nomacssrc)

<a id="gl-gtktheme"></a>**GTK_THEME**  
Environment variable that forces a GTK theme, e.g. `Adwaita:dark`. → [[gtktheme]](#ref-gtktheme)

<a id="gl-devconventions"></a>**dev-conventions**  
The engineering conventions of the author's own command-line tools (handover v6): XDG layout, git-flow with `main`/`develop`, `tools/release.sh`, seven quality gates, `install.sh` from release tags, `<tool> update`, live update of long-running processes. → [installing](user.md#sec-install)

<a id="gl-idempotent"></a>**Idempotent**  
An operation with the same effect however often it runs; here: the one-time move of the old layout and `install.sh` never create duplicates. → [moving](user.md#sec-migration)

<a id="gl-inotify"></a>**inotify**  
Linux interface for watching files and folders for changes; the watched objects of a process are listed in `/proc/<pid>/fdinfo`. → [[inotify7]](#ref-inotify7), [[procfdinfo]](#ref-procfdinfo)

<a id="gl-label"></a>**Label**  
Free-text name of a snapshot (`-l`), stored inside the JSON file; it does not change the file name. → [label](user.md#sec-label)

<a id="gl-localstate"></a>**Local State (Chrome)**  
JSON file in Chrome's user data directory with browser-wide settings, among them the profiles that are open (`profile.last_active_profiles`) and their display names. → [[chromeprefs]](#ref-chromeprefs)

<a id="gl-loginshell"></a>**Login shell**  
Shell started at login (or with `bash -l`); it reads `~/.profile`. Interactive non-login shells read `~/.bashrc`. → [[bashstartup]](#ref-bashstartup)

<a id="gl-migration"></a>**Migration**  
Conversion of an installation of version 1.5 or older (`session-snap.py`, data folder `session-snap/`) into the layout of 1.6. → [migration](user.md#sec-migration)

<a id="gl-mime"></a>**MIME type**  
Standardised name of a file type, e.g. `video/x-matroska` for `.mkv` or `image/png`; determined from the file extension via `/etc/mime.types`. → [[pymimetypes]](#ref-pymimetypes), [[mediatypes]](#ref-mediatypes)

<a id="gl-mru"></a>**MRU (most recently used)**  
Ordering by the time of last use; the window used last comes first in the switcher. → [[poplauncher]](#ref-poplauncher)

<a id="gl-origin"></a>**Origin (launcher entry)**  
The desktop entry a program was started from, determined from `GIO_LAUNCHED_DESKTOP_FILE` or from the systemd unit name; informational only. → [[glibsrc]](#ref-glibsrc), [[systemdde]](#ref-systemdde)

<a id="gl-path"></a>**PATH**  
Environment variable with the directories in which the shell searches for commands. → [[bashstartup]](#ref-bashstartup)

<a id="gl-pid"></a>**PID**  
Process identifier; assigned by the kernel and **reused** after the process has ended. → [[procstat]](#ref-procstat)

<a id="gl-playeroptions"></a>**Player options**  
Start options that session-snap inserts for videos on restore, per player (`[player-options]` in `config.ini`): mpv `--pause`, VLC `--start-paused --no-one-instance-when-started-from-file`. → [[mpvsrc]](#ref-mpvsrc)

<a id="gl-prefsjs"></a>**prefs.js / user.js (Firefox)**  
`prefs.js` holds the settings of a Firefox profile and is rewritten by Firefox; `user.js` is read at every start and overrides it. session-snap writes one value into `prefs.js` while Firefox is closed. → [[ffstartup]](#ref-ffstartup)

<a id="gl-procfs"></a>**Proc filesystem (procfs, /proc)**  
Virtual file system through which the Linux kernel exposes process information as files. → [[procstat]](#ref-procstat), [[procfd]](#ref-procfd)

<a id="gl-identity"></a>**Process identity**  
The pair (PID, start time from field 22 of `/proc/<pid>/stat`); distinguishes a process from a later one with a reused PID. → [identity](developer.md#sec-identity)

<a id="gl-programsini"></a>**programs.ini**  
File in the storage directory with the chosen programs for videos and images, written by the tool. → [data model](developer.md#sec-datamodel)

<a id="gl-protected"></a>**Protected chain**  
The script's own process and all its ancestors (shell, terminal, session); `kill` never signals them. → [identity](developer.md#sec-identity)

<a id="gl-pty"></a>**Pseudo-terminal (pty)**  
Pair of virtual devices that emulates a terminal; used by test T18 to drive the curses interface with real key codes. → [[pypty]](#ref-pypty)

<a id="gl-ptrace"></a>**ptrace access check**  
Kernel permission check for sensitive `/proc/<pid>` entries such as `fd/`; it succeeds for processes of the same user. → [[procfd]](#ref-procfd)

<a id="gl-pyflakes"></a>**pyflakes**  
Static checker for Python source code. → [[pyflakes]](#ref-pyflakes)

<a id="gl-xbel"></a>**recently-used.xbel**  
File `~/.local/share/recently-used.xbel` in which GTK programs record opened files, with the registering program. → [[xbel]](#ref-xbel)

<a id="gl-relaunch"></a>**Relaunch**  
Restart of an app window (e.g. Claude) by session-snap after the browser process containing it has been terminated by `kill`. → [browsers](user.md#sec-browser)

<a id="gl-restorelast"></a>**--restore-last-session**  
Chrome start switch: every profile Chrome opens at startup restores its last session; it has no effect on a Chrome that is already running. → [[chromestartup]](#ref-chromestartup), [[chromerestore]](#ref-chromerestore)

<a id="gl-resumeonce"></a>**resume_session_once**  
Firefox setting `browser.sessionstore.resume_session_once`: if true, the last session is restored at the next start; Firefox resets it afterwards. → [[ffstartup]](#ref-ffstartup), [[ffsessionstore]](#ref-ffsessionstore)

<a id="gl-seccontext"></a>**Security context (Wayland)**  
Marker with which a sandbox (e.g. Flatpak) connects its applications to the compositor; cosmic-comp withholds the toplevel protocols from such clients. → [[cosmiccomp]](#ref-cosmiccomp)

<a id="gl-session"></a>**Session (new session), double fork**  
`setsid()` creates a session without controlling terminal; a second `fork()` re-parents the program to the session's subreaper or init, so it survives the terminal and session-snap and never becomes a zombie of session-snap. → [[pysubprocess]](#ref-pysubprocess), [[kill2]](#ref-kill2)

<a id="gl-sessionrestore"></a>**Session restore (browser)**  
The browser's own function to reopen the windows and tabs of the last session at start-up; used by session-snap instead of recording addresses. → [browsers](user.md#sec-browser)

<a id="gl-sigkill"></a>**SIGKILL**  
Signal 9; terminates a process immediately; it cannot be caught, blocked or ignored. → [[signal7]](#ref-signal7), [[kill2]](#ref-kill2)

<a id="gl-signature"></a>**Signature**  
PID-independent identity of an entry: (category, files) for videos and images, (category, program, arguments) otherwise. → [identity](developer.md#sec-identity)

<a id="gl-sigterm"></a>**SIGTERM**  
Signal 15; polite request to terminate, which a program may handle or ignore. → [[signal7]](#ref-signal7), [[kill2]](#ref-kill2)

<a id="gl-singleinstance"></a>**Single-instance application**  
A program that runs only once per user: a second start passes its file to the running process, which opens another window (e.g. the GNOME image viewer). → [single-instance viewers](user.md#sec-single)

<a id="gl-snapshot"></a>**Snapshot**  
One record of the desktop state: a JSON file `session-snap_<date>_<time>.json` in `~/.local/state/session-snap/snapshots/`. → [storage](developer.md#sec-storage)

<a id="gl-staticversion"></a>**Static version reading**  
Reading the `VERSION = "…"` line from a program's source text instead of running the program. → [update](user.md#sec-update)

<a id="gl-surrogateescape"></a>**surrogateescape**  
Python error handler that maps undecodable bytes in file names to surrogate code points and back. → [[pep383]](#ref-pep383)

<a id="gl-toplevel"></a>**Toplevel**  
A top-level application window in Wayland terms; the COSMIC toplevel protocols report and manage these windows. → [[wlinfo]](#ref-wlinfo), [[wlmgmt]](#ref-wlmgmt)

<a id="gl-tree"></a>**Tree selection**  
The selection screen of restore and terminate since 1.7.1/1.7.6: root "Alle Programme", categories, programs; arrow keys fold and move, the space bar switches an entry or a whole group. → [interface](user.md#sec-ui)

<a id="gl-unittest"></a>**unittest / unittest.mock**  
Python's standard test framework and its library for temporarily replacing objects during a test. → [[pyunittest]](#ref-pyunittest)

<a id="gl-wayland"></a>**Wayland**  
Display server protocol; clients cannot inspect or control the windows of other clients. → [[wikiwayland]](#ref-wikiwayland)

<a id="gl-wire"></a>**Wayland wire protocol**  
The binary message format between Wayland clients and the compositor: object ID, size and opcode, followed by 32-bit aligned arguments, over a Unix socket. session-snap implements the small part it needs itself. → [[wikiwayland]](#ref-wikiwayland)

<a id="gl-title"></a>**Window title**  
Text a window reports to the compositor; image viewers and players show the file name in it. → [[wlinfo]](#ref-wlinfo)

<a id="gl-wireplumber"></a>**WirePlumber**  
The session manager of PipeWire on Pop!_OS; among other things it stores volume and mute state per application and restores them for new audio streams. → [[wpstream]](#ref-wpstream)

<a id="gl-seat"></a>**wl_seat**  
Wayland object for a group of input devices (keyboard, pointer); the COSMIC `activate` request names the seat that receives the focus. → [[wlmgmt]](#ref-wlmgmt)

<a id="gl-xwayland"></a>**Xwayland**  
X11 server that runs as a Wayland client, so that X11 applications work on a Wayland desktop. → [[alternativeto]](#ref-alternativeto), [[wikiwayland]](#ref-wikiwayland)

<a id="gl-zombie"></a>**Zombie**  
Process that has exited but has not yet been reaped by its parent (state `Z`); treated as ended. → [[procstat]](#ref-procstat)


## References

Checked between 14 October and 1 November 2026; **[V]** content verified, **[I]** found in the search index, **[X]** not retrievable (kept for documentation).

- <a id="ref-poplauncher"></a>**[poplauncher]** System76: *pop-launcher* source code, plugin `plugins/src/cosmic_toplevel/mod.rs` (activated window moved to the end of the list). <https://github.com/pop-os/launcher> — **[V]** (repository cloned on 28 Oct 2026, commit `86c2639b`)
- <a id="ref-chromiumudd"></a>**[chromiumudd]** Chromium docs: *User Data Directory* — two running instances cannot share a user data directory; default `~/.config/google-chrome`. <https://chromium.googlesource.com/chromium/src/+show/HEAD/docs/user_data_dir.md> — **[V]**
- <a id="ref-wlinfo"></a>**[wlinfo]** wayland.app: *COSMIC toplevel info* (`cosmic-toplevel-info-unstable-v1`). <https://wayland.app/protocols/cosmic-toplevel-info-unstable-v1> — **[V]**
- <a id="ref-pyos"></a>**[pyos]** Python documentation: *Generic Operating System Services* (index of `os`, incl. `os.replace`). <https://docs.python.org/3/library/allos.html> — **[I]**
- <a id="ref-autostart"></a>**[autostart]** freedesktop.org: *Desktop Application Autostart Specification* — user autostart directory `~/.config/autostart/`. <https://specifications.freedesktop.org/autostart-spec/latest> — **[V]**
- <a id="ref-xdgautostartgen"></a>**[xdgautostartgen]** systemd: *systemd-xdg-autostart-generator(8)* — services generated from autostart entries, started through `xdg-desktop-autostart.target`. <https://man7.org/linux/man-pages/man8/systemd-xdg-autostart-generator.8.html> — **[V]**
- <a id="ref-wikiwayland"></a>**[wikiwayland]** Wikipedia: *Wayland (protocol)*. <https://en.wikipedia.org/wiki/Wayland_(protocol)> — **[I]**
- <a id="ref-cosmiccomp"></a>**[cosmiccomp]** System76: *cosmic-comp* source code. <https://github.com/pop-os/cosmic-comp> — **[V]** (repository cloned on 20 Oct 2026; `src/state.rs`, `src/wayland/protocols/toplevel_info.rs`; tags `epoch-1.0.0`, `epoch-1.5.0` and main branch commit `742b800b` compared; built and run)
- <a id="ref-lwn"></a>**[lwn]** LWN.net: *Pop!_OS 24.04 LTS released* (Dec 2025). <https://lwn.net/Articles/1050129> — **[V]**
- <a id="ref-register"></a>**[register]** The Register: *Pop OS 24.04 LTS with COSMIC Epoch 1 emerges* (22 Dec 2025). <https://www.theregister.com/2025/12/22/popos_2404_cosmic_epoch_1/> — **[I]**
- <a id="ref-pycurses"></a>**[pycurses]** Python documentation: *curses*. <https://docs.python.org/3/library/curses> — **[I]**
- <a id="ref-notifyspec"></a>**[notifyspec]** freedesktop.org: *Desktop Notifications Specification — D-BUS Protocol* (`Notify` with `replaces_id`, `actions`, `hints`, `expire_timeout`; signal `ActionInvoked`). <https://specifications.freedesktop.org/notification-spec/latest/ar01s09.html> — **[I]** (direct retrieval refused by the build machine's allowlist; content confirmed from the search-index excerpt)
- <a id="ref-ctlseqs"></a>**[ctlseqs]** T. Dickey: *XTerm Control Sequences*. <https://www.invisible-island.net/xterm/ctlseqs/ctlseqs.html> — **[I]**
- <a id="ref-desktopkeys"></a>**[desktopkeys]** freedesktop.org: *Desktop Entry Specification 1.5 — Recognized keys* (`NoDisplay`, `Hidden`, `TryExec`, `Exec`). <https://specifications.freedesktop.org/desktop-entry/1.5/recognized-keys.html> — **[V]**
- <a id="ref-basedir"></a>**[basedir]** freedesktop.org: *XDG Base Directory Specification*. <https://specifications.freedesktop.org/basedir/latest/> — **[V]**
- <a id="ref-desktopspec"></a>**[desktopspec]** freedesktop.org: *Desktop Entry Specification* (single page) — desktop file ID rule, Exec field codes. <https://specifications.freedesktop.org/desktop-entry/latest-single/> — **[V]**
- <a id="ref-cosmicnotif"></a>**[cosmicnotif]** System76: *cosmic-notifications* source code — capabilities `body`, `icon-static`, `persistence`, `actions` (`src/subscriptions/notifications.rs`); timeout 0 = no expiry (`src/app.rs`). <https://github.com/pop-os/cosmic-notifications> — **[V]** (repository cloned on 1 Nov 2026)
- <a id="ref-vlcinput"></a>**[vlcinput]** VideoLAN: VLC 3.0.20 source, `src/input/input.c` (`MainLoopTryRepeat()`: `input-repeat` seeks to the start of the same input). <https://github.com/videolan/vlc/blob/3.0.20/src/input/input.c> — **[V]** (raw, 1 Nov 2026)
- <a id="ref-escdelay"></a>**[escdelay]** ncurses: *curs_variables(3x)* (`ESCDELAY`). <https://www.man7.org/linux/man-pages/man3/curs_variables.3x.html> — **[I]**
- <a id="ref-pysubprocess"></a>**[pysubprocess]** Python documentation: *subprocess*. <https://docs.python.org/3/library/subprocess.html> — **[I]**
- <a id="ref-bashstartup"></a>**[bashstartup]** GNU Bash Reference Manual: *Bash Startup Files*. <https://gnu.org/s/bash/manual/html_node/Bash-Startup-Files.html> — **[I]**
- <a id="ref-procfd"></a>**[procfd]** Linux man-pages: *proc_pid_fd(5)* — links to open files, `PTRACE_MODE_READ_FSCREDS`. <https://man7.org/linux/man-pages/man5/proc_pid_fd.5.html> — **[V]**
- <a id="ref-flatpakmeta"></a>**[flatpakmeta]** Debian manpages: *flatpak-metadata(5)* — `/.flatpak-info`, `[Instance]`, `instance-id`. <https://manpages.debian.org/buster/flatpak/flatpak-metadata.5.en.html> — **[V]**
- <a id="ref-flock2"></a>**[flock2]** Linux man-pages: *flock(2)* — locks belong to the open file description; `LOCK_NB`; released when all descriptors are closed. <https://man7.org/linux/man-pages/man2/flock.2.html> — **[V]**
- <a id="ref-pyfnmatch"></a>**[pyfnmatch]** Python documentation: *fnmatch*. <https://docs.python.org/3.12/library/fnmatch.html> — **[I]**
- <a id="ref-eogsrc"></a>**[eogsrc]** GNOME: *eog* 45.3 source — `src/main.c` (option `--fullscreen`), `src/eog-application.c` (startup flags passed to the running instance as platform data), `src/eog-window.c` (`eog_window_run_fullscreen()`). <https://github.com/GNOME/eog/blob/45.3/src/main.c> — **[V]** (raw, 1 Nov 2026)
- <a id="ref-nomacssrc"></a>**[nomacssrc]** nomacs: `ImageLounge/src/main.cpp` — option `-f`/`--fullscreen` "Start in fullscreen" (tag 3.16.224 and main branch). <https://github.com/nomacs/nomacs/blob/master/ImageLounge/src/main.cpp> — **[V]** (raw, 1 Nov 2026)
- <a id="ref-gtktheme"></a>**[gtktheme]** Baeldung on Linux: *dark theme for GTK applications* — `GTK_THEME` forces a theme for one application, e.g. `GTK_THEME=Adwaita:dark gnome-calculator`. <https://www.baeldung.com/linux/gtk-applications-dark-theme> — **[V]**
- <a id="ref-inotify7"></a>**[inotify7]** Linux man-pages: *inotify(7)* — watches can be viewed via `/proc/<pid>/fdinfo`. <https://man7.org/linux/man-pages/man7/inotify.7.html> — **[V]**
- <a id="ref-procfdinfo"></a>**[procfdinfo]** Linux man-pages: *proc_pid_fdinfo(5)* — inotify lines `wd`, `ino` (inode, hexadecimal), `sdev` (device, hexadecimal). <https://man7.org/linux/man-pages/man5/proc_pid_fdinfo.5.html> — **[V]**
- <a id="ref-chromeprefs"></a>**[chromeprefs]** Chromium: `chrome/common/pref_names.h` — `kProfilesLastActive = "profile.last_active_profiles"`. <https://github.com/chromium/chromium/blob/main/chrome/common/pref_names.h> — **[V]** (raw, 1 Nov 2026)
- <a id="ref-pymimetypes"></a>**[pymimetypes]** Python documentation: *mimetypes* — `knownfiles` incl. `/etc/mime.types`, `guess_type(strict=False)`. <https://docs.python.org/library/mimetypes.html> — **[V]**
- <a id="ref-mediatypes"></a>**[mediatypes]** Local observation: `/etc/mime.types` belongs to package `media-types` on Ubuntu 24.04.5 (`dpkg -S`); `.mkv` → `video/x-matroska`. Inspected on 15 Oct 2026. — **[V]** (local)
- <a id="ref-glibsrc"></a>**[glibsrc]** GLib 2.80.0 source code, Ubuntu source package `glib2.0_2.80.0-6ubuntu3.9`: `gio/gdesktopappinfo.c` line 2941, `gio/gio-launch-desktop.c` line 249. Inspected on 20 Oct 2026. — **[V]** (local)
- <a id="ref-systemdde"></a>**[systemdde]** systemd: *Desktop Environments* (naming of application units), mirror of `docs/DESKTOP_ENVIRONMENTS.md`. <https://git.alternativebit.fr/picnoir/Systemd/raw/commit/77169ed09bb088d9b931a42788c3cbfda9078a92/docs/DESKTOP_ENVIRONMENTS.md> — **[V]**
- <a id="ref-procstat"></a>**[procstat]** Linux man-pages: *proc_pid_stat(5)* — field 4 `ppid`, field 22 `starttime`, state `Z`. <https://man7.org/linux/man-pages/man5/proc_pid_stat.5.html> — **[V]**
- <a id="ref-mpvsrc"></a>**[mpvsrc]** mpv 0.37.0 source code, Ubuntu source package `mpv_0.37.0-1ubuntu4` (`apt-get source`): `DOCS/man/options.rst` (`--pause`, `--window-maximized`), `video/out/wayland_common.c` (`xdg_toplevel_set_maximized`). Inspected on 20 Oct 2026. — **[V]** (local)
- <a id="ref-ffstartup"></a>**[ffstartup]** Mozilla: `browser/components/sessionstore/SessionStartup.sys.mjs` — "set the browser.sessionstore.resume_session_once pref to true, and the session will be restored the next time the browser starts". <https://github.com/mozilla/gecko-dev/blob/master/browser/components/sessionstore/SessionStartup.sys.mjs> — **[V]** (raw, 1 Nov 2026)
- <a id="ref-pypty"></a>**[pypty]** Python documentation: *pty*. <https://docs.python.org/3/library/pty.html> — **[I]**
- <a id="ref-pyflakes"></a>**[pyflakes]** PyPI: *pyflakes*. <https://pypi.org/project/pyflakes/> — **[V]** (HTTP 200)
- <a id="ref-xbel"></a>**[xbel]** freedesktop.org: *Desktop Bookmark Specification* (XBEL; replaces the Recent Files Storage Specification; `recently-used.xbel`; `application` element with `name` and `exec`). <https://wiki.freedesktop.org/www/Specifications/desktop-bookmark-spec/> — **[V]**
- <a id="ref-chromestartup"></a>**[chromestartup]** Chromium: `chrome/browser/ui/startup/startup_browser_creator.cc` — `ShouldRestoreLastSession()`, `pref.type = SessionStartupPref::LAST` with `--restore-last-session`, `LaunchBrowserForLastProfiles()`. <https://github.com/chromium/chromium/blob/main/chrome/browser/ui/startup/startup_browser_creator.cc> — **[V]** (raw, 1 Nov 2026)
- <a id="ref-chromerestore"></a>**[chromerestore]** Chromium code review 2469363002 — the switch is documented as "restored on startup"; behaviour when a Chrome process already runs. <https://codereview.chromium.org/2469363002> — **[V]**
- <a id="ref-ffsessionstore"></a>**[ffsessionstore]** Mozilla: `browser/components/sessionstore/SessionStore.sys.mjs` — `resume_session_once` is set back to `false` after the session was resumed. <https://github.com/mozilla/gecko-dev/blob/master/browser/components/sessionstore/SessionStore.sys.mjs> — **[V]** (raw, 1 Nov 2026)
- <a id="ref-kill2"></a>**[kill2]** Linux man-pages: *kill(2)*. <https://man7.org/linux/man-pages/man2/kill.2.html> — **[I]**
- <a id="ref-signal7"></a>**[signal7]** Linux man-pages: *signal(7)* (Ubuntu manpages). <https://manpages.ubuntu.com/manpages/bionic/man7/signal.7.html> — **[I]**
- <a id="ref-pep383"></a>**[pep383]** PEP 383: *Non-decodable Bytes in System Character Interfaces*. <https://peps.python.org/pep-0383> — **[I]**
- <a id="ref-wlmgmt"></a>**[wlmgmt]** wayland.app: *COSMIC toplevel management* (`cosmic-toplevel-management-unstable-v1`), incl. compositor support table. <https://wayland.app/protocols/cosmic-toplevel-management-unstable-v1> — **[V]**
- <a id="ref-pyunittest"></a>**[pyunittest]** Python documentation: *unittest*. <https://docs.python.org/3/library/unittest.html> — **[I]**
- <a id="ref-wpstream"></a>**[wpstream]** WirePlumber documentation: *Stream configuration* — volume and mute state of streams are remembered and restored when the same application appears. <https://pipewire.pages.freedesktop.org/wireplumber/daemon/configuration/stream.html> — **[V]**
- <a id="ref-alternativeto"></a>**[alternativeto]** AlternativeTo: *Pop!_OS 24.04 LTS beta launches COSMIC desktop* (Sep 2025): COSMIC is Wayland-native, X11 apps via Xwayland. <https://www.alternativeto.net/news/2025/9/pop-_os-24-04-lts-beta-launches-cosmic-desktop-and-new-native-apps/> — **[X]** direct retrieval failed (client error); statement confirmed from the search-index excerpt.
