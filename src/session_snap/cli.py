"""session-snap - record, restore and terminate open desktop programs.

Target platform: Pop!_OS 24.04 LTS (COSMIC, Wayland); works on any Linux with /proc.

Capture method (desktop- and compositor-independent, Wayland and X11):
  /proc/<pid>/cmdline              -> argv (file arguments, also file:// URIs)
  /proc/<pid>/cwd                  -> working directory (resolves relative paths)
  /proc/<pid>/fd/*                 -> currently open media files (VLC keeps the file open)
  /proc/<pid>/stat                 -> parent PID and start time (PID identity check)
  /proc/<pid>/root/.flatpak-info   -> Flatpak application ID and instance

Categories: vlc (videos) | image | browser | rest

Files (XDG layout, dev-conventions; see init_paths()):
  ~/.config/session-snap/          config.ini, programs.ini
  ~/.local/state/session-snap/     snapshots/, logs/, backup/, order.json, watch.lock
  ~/.local/share/session-snap/     venv/ (installed release, install.sh), releases/
  ~/.local/bin/session-snap        the command (link into the venv)
The environment variable SESSION_SNAP_DIR puts everything into one directory (tests).

Must run as the normal desktop user, never via sudo/root (see check_not_root()).
"""
from __future__ import annotations

import argparse
import fcntl
import configparser
import curses
import fnmatch
import json
import locale
import mimetypes
import logging
import os
import re
import select
import shlex
import shutil
import signal
import socket
import struct
import subprocess
import sys
import textwrap
import threading
import time
from typing import Any, Callable
from datetime import datetime
from pathlib import Path
from urllib.parse import unquote, urlparse

# Only the user's own recently-used.xbel is parsed; expat >= 2.4.1 limits entity expansion
# and ElementTree resolves no external entities.
import xml.etree.ElementTree as ET  # nosec B405

from session_snap import __version__
from session_snap.selfrestart import SelfRestart
from session_snap.update import ReleaseUpdater

VERSION = __version__
FILE_PREFIX = "session-snap_"

# --------------------------------------------------------------------------- paths
# XDG Base Directory layout (dev-conventions, chapter "Directory layout"): category first,
# then the tool. Versions up to 1.8.0 kept everything in ~/scripts/session-snap.d; the first
# start of 2.0.0 moves those files (migrate_layout()).
CONFIG_DIR = Path()
STATE_DIR = Path()
DATA_DIR = Path()
BIN_DIR = Path()
PROGRAM = Path()
OLD_DIR = Path()
SNAP_DIR = Path()
LOG_DIR = Path()
BACKUP_DIR = Path()
LOG_FILE = Path()
CONFIG_FILE = Path()
PROGRAMS_FILE = Path()


def xdg_dir(var: str, default: str) -> Path:
    """Base directory from an XDG variable.

    Args:
        var: Name of the environment variable, e.g. ``XDG_STATE_HOME``.
        default: Fallback below the home directory, e.g. ``.local/state``.

    Returns:
        The variable's value if it is an absolute path, else ``~/<default>``.
    """
    value = os.environ.get(var, "")
    return Path(value) if os.path.isabs(value) else Path.home() / default


def init_paths(base: Path | None = None) -> None:
    """Set the module-level paths.

    Args:
        base: One directory for everything (tests); None reads SESSION_SNAP_DIR and
            otherwise uses the XDG layout.
    """
    # The paths are module-level on purpose: every function uses them, the tests reset them.
    # pylint: disable=global-statement
    global CONFIG_DIR, STATE_DIR, DATA_DIR, BIN_DIR, PROGRAM, OLD_DIR, SNAP_DIR, LOG_DIR
    global BACKUP_DIR, LOG_FILE, CONFIG_FILE, PROGRAMS_FILE
    if base is None and os.environ.get("SESSION_SNAP_DIR"):
        base = Path(os.environ["SESSION_SNAP_DIR"])
    if base is not None:
        CONFIG_DIR = STATE_DIR = DATA_DIR = Path(base).expanduser()
    else:
        CONFIG_DIR = xdg_dir("XDG_CONFIG_HOME", ".config") / "session-snap"
        STATE_DIR = xdg_dir("XDG_STATE_HOME", ".local/state") / "session-snap"
        DATA_DIR = xdg_dir("XDG_DATA_HOME", ".local/share") / "session-snap"
    BIN_DIR = Path(os.environ.get("SESSION_SNAP_BIN") or Path.home() / ".local" / "bin")
    PROGRAM = BIN_DIR / "session-snap"
    OLD_DIR = Path.home() / "scripts" / "session-snap.d"
    SNAP_DIR = STATE_DIR / "snapshots"
    LOG_DIR = STATE_DIR / "logs"
    BACKUP_DIR = STATE_DIR / "backup"
    LOG_FILE = LOG_DIR / "session-snap.log"
    CONFIG_FILE = CONFIG_DIR / "config.ini"
    PROGRAMS_FILE = CONFIG_DIR / "programs.ini"


init_paths()

# --------------------------------------------------------------------------- classification
CAT_VLC, CAT_IMG, CAT_BROWSER, CAT_REST = "vlc", "image", "browser", "rest"
CAT_ORDER = [CAT_VLC, CAT_IMG, CAT_BROWSER, CAT_REST]
CAT_LABEL = {CAT_VLC: "Videos", CAT_IMG: "Bilder", CAT_BROWSER: "Browser",
             CAT_REST: "Sonstige Programme"}
CAT_SHORT = {CAT_VLC: "Videos", CAT_IMG: "Bilder", CAT_BROWSER: "Browser", CAT_REST: "Sonstige"}
# program choice ("Programme waehlen"): category -> key in programs.ini
PROGRAM_SLOTS = {CAT_VLC: "video", CAT_IMG: "image"}
SLOT_LABEL = {"video": "Videos", "image": "Bilder"}
SLOT_FALLBACK_MIMES = {"video": ["video/mp4", "video/x-matroska", "video/webm"],
                       "image": ["image/png", "image/jpeg", "image/gif"]}

IMAGE_EXT = {
    ".png", ".gif", ".jpg", ".jpeg", ".jpe", ".jfif", ".webp", ".bmp", ".tif", ".tiff",
    ".svg", ".svgz", ".heic", ".heif", ".avif", ".jxl", ".ico", ".xcf", ".psd", ".kra",
    ".dng", ".cr2", ".cr3", ".nef", ".arw", ".orf", ".rw2", ".raf", ".pnm", ".ppm", ".tga",
}
MEDIA_EXT = {
    ".mp4", ".mkv", ".avi", ".mov", ".webm", ".flv", ".wmv", ".m4v", ".mpg", ".mpeg",
    ".ts", ".m2ts", ".mts", ".3gp", ".ogv", ".vob", ".divx", ".rm", ".rmvb", ".asf",
    ".mp3", ".flac", ".ogg", ".oga", ".opus", ".wav", ".m4a", ".aac", ".wma", ".ac3",
    ".dts", ".mka", ".aiff", ".ape", ".iso", ".m3u", ".m3u8", ".pls", ".xspf", ".cue",
    ".gif",
}

# video players (category key "vlc" kept for compatibility with existing snapshots)
VIDEO_PLAYERS = {"vlc", "mpv", "celluloid", "smplayer", "haruna", "totem", "cosmic-player",
                 "parole", "dragon", "mplayer"}
VLC_FLATPAKS = {"org.videolan.VLC"}
BROWSER_NAMES = {"firefox", "firefox-esr", "firefox-bin", "chrome", "google-chrome",
                 "google-chrome-stable", "chromium", "chromium-browser", "brave", "brave-browser"}
BROWSER_FLATPAKS = {"org.mozilla.firefox", "com.google.Chrome", "org.chromium.Chromium",
                    "com.brave.Browser"}
# start options for videos on restore (paused); key = player binary name.
# mpv 0.37: --pause; VLC 3.0.20: --start-paused. Maximising is done uniformly for all
# programs through the compositor (see "window control"), not through player options.
# VLC: --no-one-instance-when-started-from-file, because VLC (default "Use only one instance
# when started from file manager" = enabled) would otherwise hand every further file to the
# first instance, which REPLACES the current item - only the last video would survive.
# VLC: --input-repeat=65535 plays the video again and again. Measured with VLC 3.0.20:
# --loop/--repeat restart the playlist item and --start-paused pauses it again on every pass;
# --input-repeat seeks back to the start inside the same input (MainLoopTryRepeat in
# src/input/input.c), so the video keeps playing after the first start.
# eog 45: --fullscreen = its own fullscreen mode (like double-click: no header bar, black
# background). The flag also reaches an already running eog instance (startup flags are sent
# as platform data, eog-application.c), so every new window opens fullscreen. Image viewer
# options are only used while images are in "fullscreen" (config) - see restore.
DEFAULT_PLAYER_OPTIONS = {"mpv": ["--pause"],
                          "vlc": ["--start-paused", "--input-repeat=65535",
                                  "--no-one-instance-when-started-from-file"],
                          "eog": ["--fullscreen"], "nomacs": ["--fullscreen"]}
# nomacs (3.16 and later): -f/--fullscreen "Start in fullscreen" (ImageLounge/src/main.cpp).
# cosmic-viewer has NO start option: it takes its first argument as the image path
# (cosmic-viewer/src/main.rs), an option would be opened as a file - never add one.
# categories whose windows are maximized / shown fullscreen after a restore
# (config keys "maximize" and "fullscreen"; a category in both is shown fullscreen)
MAXIMIZE_KEYS = {"videos": "vlc", "images": "image", "browser": "browser", "rest": "rest"}
DEFAULT_MAXIMIZE = "videos"
DEFAULT_FULLSCREEN = "images"
PLAYER_FLATPAKS = {"io.mpv.Mpv": "mpv", "org.videolan.VLC": "vlc", "org.gnome.eog": "eog",
                   "org.nomacs.ImageLounge": "nomacs"}
# browser app windows (e.g. Claude as Chrome app): recorded as own entry, relaunched after kill
APP_FLAGS = ("--app-id=", "--app=")
IMAGE_VIEWERS = {
    "eog", "eom", "loupe", "gthumb", "ristretto", "gwenview", "feh", "nomacs", "qimgv",
    "imv", "imv-wayland", "imv-x11", "sxiv", "nsxiv", "geeqie", "shotwell", "viewnior",
    "gpicview", "xviewer", "image-roll", "gimp", "gimp-2.10", "gimp-3.0", "krita",
    "inkscape", "darktable", "rawtherapee", "digikam", "showfoto", "pix", "oculante",
}
IMAGE_FLATPAKS = {
    "org.gnome.eog", "org.gnome.Loupe", "org.gnome.gThumb", "org.kde.gwenview",
    "org.nomacs.ImageLounge", "org.gimp.GIMP", "org.kde.krita", "org.inkscape.Inkscape",
    "org.darktable.Darktable", "com.github.weclaw1.ImageRoller", "io.github.woelper.Oculante",
}
# Interpreters/launchers: their basename alone says nothing about the actual application
GENERIC_NAMES = {
    "sh", "bash", "dash", "zsh", "fish", "env", "python", "python3", "perl", "ruby", "node",
    "java", "gjs", "flatpak", "sudo", "pkexec", "xdg-open", "gio", "systemd-run", "bwrap",
    "flatpak-bwrap", "xdg-dbus-proxy", "snap",
}
# Heuristic default exclusions (fnmatch patterns against binary name / Flatpak ID)
DEFAULT_EXCLUDE = [
    "cosmic-panel", "cosmic-applet*", "cosmic-app-library", "cosmic-bg", "cosmic-comp",
    "cosmic-greeter*", "cosmic-launcher", "cosmic-notifications", "cosmic-osd",
    "cosmic-session", "cosmic-settings-daemon", "cosmic-workspaces", "cosmic-idle",
    "cosmic-files-applet", "cosmic-panel-button", "gnome-shell", "xdg-desktop-portal*",
    "ibus*", "session-snap*", "polkit*", "*-daemon", "*-agent",
]
FD_IGNORE_PREFIX = ("/proc/", "/sys/", "/dev/", "/usr/", "/etc/", "/memfd:", "/var/cache/")

log = logging.getLogger("session-snap")


# =========================================================================== helpers
def check_not_root() -> None:
    """Root would see foreign processes and start programs outside the user's Wayland session."""
    if os.geteuid() == 0:
        sys.exit("session-snap: bitte als normaler Benutzer starten, nicht mit sudo/root.")


def setup_logging() -> None:
    """Open the activity log (re-initialised after the paths changed)."""
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    for h in list(log.handlers):  # re-initialisation: close the previous log file
        log.removeHandler(h)
        h.close()
    handler = logging.FileHandler(LOG_FILE, encoding="utf-8")
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)-7s %(message)s"))
    log.addHandler(handler)
    log.setLevel(logging.INFO)


def _readlink(p: str) -> str:
    try:
        return os.readlink(p)
    except OSError:
        return ""


def _uniq(seq):
    seen, out = set(), []
    for x in seq:
        if x not in seen:
            seen.add(x)
            out.append(x)
    return out


def _ext(path: str) -> str:
    return os.path.splitext(path)[1].lower()


def _match_any(names, patterns) -> bool:
    return any(fnmatch.fnmatchcase(n, pat) for n in names for pat in patterns)


def _ask_yes(question: str) -> bool:
    try:
        return input(f"{question} [j/N] ").strip().lower() in ("j", "ja", "y", "yes")
    except EOFError:
        return False


def parse_maximize(value: str) -> set:
    """'videos, images' -> {'vlc', 'image'}; 'all' -> every category; '' / 'none' -> {}.

    Args:
        value: Comma-separated category words from the configuration.

    Returns:
        Set of category keys.
    """
    words = {w.strip().lower() for w in value.replace("\n", ",").split(",") if w.strip()}
    if "all" in words:
        return set(MAXIMIZE_KEYS.values())
    return {MAXIMIZE_KEYS[w] for w in words if w in MAXIMIZE_KEYS}


def load_config() -> dict:
    """Defaults, overridden by config.ini; returns the configuration dict.

    Returns:
        Configuration with defaults for every option not set in config.ini.
    """
    cfg: dict[str, Any] = {
        "exclude": list(DEFAULT_EXCLUDE), "include": [], "protect": [],
        "image_viewers": set(IMAGE_VIEWERS), "video_players": set(VIDEO_PLAYERS),
        "delay": 0.4, "kill_timeout": 5.0, "maximize_timeout": 20.0, "order": "saved",
        "maximize": parse_maximize(DEFAULT_MAXIMIZE),
        "fullscreen": parse_maximize(DEFAULT_FULLSCREEN), "overlay": True,
        "browser_session": True,
        "player_options": {k: list(v) for k, v in DEFAULT_PLAYER_OPTIONS.items()}}
    if not CONFIG_FILE.is_file():
        return cfg
    cp = configparser.ConfigParser(interpolation=None)
    try:
        cp.read(CONFIG_FILE, encoding="utf-8")
    except configparser.Error as exc:
        print(f"Warnung: Konfiguration fehlerhaft ({exc}), verwende Standardwerte.",
              file=sys.stderr)
        return cfg
    if cp.has_section("player-options"):
        for player, opts in cp["player-options"].items():
            try:
                cfg["player_options"][player] = shlex.split(opts)
            except ValueError:
                print(f"Warnung: player-options {player} ungueltig, ignoriert.", file=sys.stderr)
    if cp.has_section("session-snap"):
        s = cp["session-snap"]

        def lst(key):
            """Read a comma- or line-separated list option.

            Args:
                key (str): Option name in the [session-snap] section.

            Returns:
                list: The non-empty, stripped values.
            """
            raw = s.get(key, "").replace("\n", ",")
            return [x.strip() for x in raw.split(",") if x.strip()]

        cfg["exclude"] += lst("exclude")
        cfg["include"] += lst("include")
        cfg["protect"] += lst("protect")
        cfg["image_viewers"] |= set(lst("image_viewers"))
        cfg["video_players"] |= set(lst("video_players"))
        if "maximize" in s:
            cfg["maximize"] = parse_maximize(s.get("maximize", ""))
        if "fullscreen" in s:
            cfg["fullscreen"] = parse_maximize(s.get("fullscreen", ""))
        if "browser_session" in s:
            cfg["browser_session"] = s.get("browser_session", "").strip().lower() not in (
                "no", "nein", "0", "false", "off")
        if "overlay" in s:
            cfg["overlay"] = s.get("overlay", "").strip().lower() not in (
                "no", "nein", "0", "false", "off")
        if s.get("order", "").strip() in ("saved", "keep"):
            cfg["order"] = s.get("order", "").strip()
        for key in ("delay", "kill_timeout", "maximize_timeout"):
            try:
                cfg[key] = float(s.get(key, cfg[key]))
            except ValueError:
                pass
    return cfg


CONFIG_TEMPLATE = """\
# session-snap configuration
# Lists are comma- or newline-separated; patterns follow fnmatch (*, ?, [..]).
[session-snap]
# Additional programs / Flatpak IDs that are NEVER recorded
exclude =
# Programs that are ALWAYS recorded as "rest", even without a .desktop file
include =
# Patterns matched against the FULL command line: such processes are never terminated by
# kill (e.g. a separate Chrome instance for Claude: *--user-data-dir=*chrome-claude*)
protect =
# Additional binary names treated as image viewers / video players
image_viewers =
video_players =
# Pause between two program starts in seconds
delay = 0.4
# Grace period after SIGTERM before a process counts as "not responding" (seconds)
kill_timeout = 5
# Windows maximized after a restore (COSMIC): videos, images, browser, rest, all, none
maximize = videos
# Windows shown fullscreen after a restore (COSMIC), same values; wins over "maximize"
fullscreen = images
# Alt-Tab order after a restore without questions (--all): saved = as recorded, keep = unchanged
order = saved
# Longest wait for the windows to appear (seconds)
maximize_timeout = 20
# Notice on top of all windows while session-snap works, with button "Abbrechen": yes / no
overlay = yes
# Browsers: restore the last session (all profiles, windows, tabs): yes / no
browser_session = yes

# Start options on restore, per program binary name (empty value = none).
# Videos: started paused. Image viewers: only used while images are in "fullscreen" above
# (eog --fullscreen = eog's own borderless fullscreen mode).
[player-options]
mpv = --pause
vlc = --start-paused --input-repeat=65535 --no-one-instance-when-started-from-file
eog = --fullscreen
nomacs = --fullscreen
"""


# =========================================================================== /proc scan
class Proc:
    """One process read from /proc (identity: PID and start time)."""
    __slots__ = ("pid", "ppid", "argv", "exe", "cwd", "start", "flatpak_id", "flatpak_inst")

    def __init__(self, pid, ppid, argv, exe, cwd, start):
        self.pid, self.ppid, self.argv = pid, ppid, argv
        self.exe, self.cwd, self.start = exe, cwd, start
        self.flatpak_id = ""
        self.flatpak_inst = ""

    @property
    def names(self) -> set:
        """Program names of the process: basename of the executable and of argv[0]."""
        a0 = self.argv[0] if self.argv else ""
        if " " in a0 and not os.path.exists(a0):  # some apps rewrite argv into one string
            a0 = a0.split(" ")[0]
        return {n for n in (os.path.basename(self.exe), os.path.basename(a0)) if n}

    @property
    def name(self) -> str:
        """Most specific single program name."""
        if self.exe:
            return os.path.basename(self.exe)
        return os.path.basename(self.argv[0]) if self.argv else "?"


def read_stat(pid: int) -> tuple[str, int, int] | None:
    """Return (state, ppid, starttime) from /proc/<pid>/stat, or None if gone.

    Args:
        pid: Process ID.

    Returns:
        (state, parent PID, start time in clock ticks), or None if the process is gone.
    """
    try:
        with open(f"/proc/{pid}/stat", encoding="utf-8", errors="replace") as fh:
            fields = fh.read().rsplit(")", 1)[1].split()
        return fields[0], int(fields[1]), int(fields[19])
    except (OSError, IndexError, ValueError):
        return None


def scan_processes() -> dict:
    """All processes of the current user except this one: {pid: Proc}.

    Returns:
        Mapping of process ID to Proc.
    """
    uid, me = os.getuid(), os.getpid()
    procs = {}
    for d in os.listdir("/proc"):
        if not d.isdigit():
            continue
        pid = int(d)
        if pid == me:
            continue
        base = f"/proc/{d}"
        try:
            if os.stat(base).st_uid != uid:
                continue
            with open(base + "/cmdline", "rb") as fh:
                raw = fh.read()
        except OSError:
            continue  # process vanished or not accessible
        if not raw:
            continue  # kernel thread / zombie
        st = read_stat(pid)
        if st is None:
            continue
        argv = [a.decode("utf-8", "surrogateescape") for a in raw.rstrip(b"\0").split(b"\0")]
        exe = _readlink(base + "/exe").removesuffix(" (deleted)")
        p = Proc(pid, st[1], argv, exe, _readlink(base + "/cwd"), st[2])
        cp = configparser.ConfigParser(interpolation=None)
        try:
            if cp.read(base + "/root/.flatpak-info", encoding="utf-8"):
                p.flatpak_id = cp.get("Application", "name", fallback="")
                p.flatpak_inst = cp.get("Instance", "instance-id", fallback="")
        except (OSError, configparser.Error, UnicodeDecodeError):
            pass
        procs[pid] = p
    return procs


def argv_files(p: Proc) -> list:
    """Existing files named on the command line (also file:// URLs).

    Args:
        p: Process.

    Returns:
        Absolute paths of the files named in the command line.
    """
    out = []
    for a in p.argv[1:]:
        if a.startswith("file://"):
            path = unquote(urlparse(a).path)
        elif a.startswith("-") or "://" in a:
            continue
        else:
            path = a if os.path.isabs(a) else os.path.join(p.cwd or "/", a)
        path = os.path.normpath(path)
        if os.path.isfile(path):
            out.append(path)
    return _uniq(out)


def argv_file_args(p: Proc) -> list:
    """[(index, path)] of the arguments in p.argv that name existing files.

    Args:
        p: Process.

    Returns:
        List of (argument index, absolute path).
    """
    out = []
    for i, a in enumerate(p.argv[1:], 1):
        if a.startswith("file://"):
            path = unquote(urlparse(a).path)
        elif a.startswith("-") or "://" in a:
            continue
        else:
            path = a if os.path.isabs(a) else os.path.join(p.cwd or "/", a)
        path = os.path.normpath(path)
        if os.path.isfile(path):
            out.append((i, path))
    return out


def file_cmd(head: list, argv: list, file_idx: set, path: str) -> list:
    """Restart command for ONE file with the ORIGINAL program and options:
    `head` replaces argv[0]; all file and URL arguments are removed and `path` is put where
    the first file argument stood (after a possible `--`), else appended.

    Args:
        head: Program part of the command (executable or launcher).
        argv: Original command line.
        file_idx: Indices of the file arguments to replace.
        path: The one file of the new entry.

    Returns:
        The restart command.
    """
    rest: list = []
    pos: int | None = None
    for i, a in enumerate(argv[1:], 1):
        if i in file_idx or "://" in a:
            if pos is None and i in file_idx:
                pos = len(rest)
            continue
        rest.append(a)
    if pos is None:
        pos = len(rest)
    return list(head) + rest[:pos] + [path] + rest[pos:]


def _read_environ(pid: int) -> dict:
    try:
        with open(f"/proc/{pid}/environ", "rb") as fh:
            raw = fh.read()
    except OSError:
        return {}
    env = {}
    for item in raw.split(b"\0"):
        k, sep, v = item.partition(b"=")
        if sep:
            env[k.decode("utf-8", "replace")] = v.decode("utf-8", "surrogateescape")
    return env


def _systemd_unit(pid: int) -> str:
    """Last path component of the unified cgroup (e.g. app-cosmic-mpv-1234.scope)."""
    try:
        with open(f"/proc/{pid}/cgroup", encoding="utf-8", errors="replace") as fh:
            for line in fh:
                if line.startswith("0::"):
                    return line.strip().rsplit("/", 1)[-1]
    except OSError:
        pass
    return ""


def launched_from(pid: int, ppid: int, desktop_ids: set | None = None) -> tuple[str, str]:
    """(desktop file ID, source) of the launcher entry this process was started from, or
    ('', ''). Sources: GLib sets GIO_LAUNCHED_DESKTOP_FILE(+_PID) when it launches a
    desktop entry; systemd-based desktops name the unit app[-<launcher>]-<ID>-<RANDOM>.scope.

    Args:
        pid: Process ID.
        ppid: Parent process ID.
        desktop_ids: Known desktop-file IDs; None reads them.

    Returns:
        (desktop-file ID, how it was found), or ("", "") if unknown.
    """
    env = _read_environ(pid)
    path = env.get("GIO_LAUNCHED_DESKTOP_FILE", "")
    if path:
        try:
            direct = int(env.get("GIO_LAUNCHED_DESKTOP_FILE_PID", "0")) in (pid, ppid)
        except ValueError:
            direct = False
        if direct or not env.get("GIO_LAUNCHED_DESKTOP_FILE_PID"):
            return os.path.basename(path), "GIO_LAUNCHED_DESKTOP_FILE"
    unit = _systemd_unit(pid)
    m = re.match(r"^app-(.+?)(?:-[0-9a-f]+\.scope|@[^.]*\.service|\.service)$", unit)
    if m:
        name = m.group(1).replace("\\x2d", "-")
        ids = desktop_ids if desktop_ids is not None else {
            a["id"] for a in desktop_apps(include_hidden=True)}
        for did in sorted(ids, key=len, reverse=True):
            stem = did[:-8] if did.endswith(".desktop") else did
            if name == stem or name.endswith("-" + stem):
                return did, f"systemd-Unit {unit}"
        return "", f"systemd-Unit {unit} (keine passende .desktop-Datei)"
    return "", ""


def fd_files(pid: int, exts: set) -> list:
    """Open files with one of the extensions `exts`, outside caches and configs.

    Args:
        pid: Process ID.
        exts: Accepted file name extensions (lower case, with dot).

    Returns:
        Absolute paths of the open files.
    """
    home = str(Path.home())
    ignore = FD_IGNORE_PREFIX + (home + "/.cache/", home + "/.config/")
    out: list = []
    d = f"/proc/{pid}/fd"
    try:
        fds = os.listdir(d)
    except OSError:
        return out
    for fd in fds:
        t = _readlink(f"{d}/{fd}")
        if not t.startswith("/") or t.endswith(" (deleted)") or t.startswith(ignore):
            continue
        if _ext(t) in exts and os.path.isfile(t):
            out.append(t)
    return _uniq(out)


def _parse_desktop(path: Path) -> dict:
    data: dict = {}
    in_main = False
    try:
        with open(path, encoding="utf-8", errors="replace") as fh:
            for line in fh:
                line = line.strip()
                if line.startswith("["):
                    in_main = line == "[Desktop Entry]"
                    continue
                if in_main and "=" in line and not line.startswith("#"):
                    k, v = line.split("=", 1)
                    data.setdefault(k.strip(), v.strip())
    except OSError:
        pass
    return data


def desktop_exec_names() -> set:
    """Binary names of all visible applications (freedesktop Desktop Entry spec).

    Returns:
        Set of executable names.
    """
    dirs = [Path(os.environ.get("XDG_DATA_HOME") or Path.home() / ".local" / "share")]
    sys_dirs = os.environ.get("XDG_DATA_DIRS") or "/usr/local/share:/usr/share"
    dirs += [Path(x) for x in sys_dirs.split(":") if x]
    names = set()
    for appdir in _app_dirs(dirs):
        for f in _desktop_files(appdir):
            e = _parse_desktop(f)
            if e.get("Type", "Application") != "Application":
                continue
            if e.get("NoDisplay", "").lower() == "true" or e.get("Hidden", "").lower() == "true":
                continue
            for key in ("TryExec", "Exec"):
                val = e.get(key)
                if not val:
                    continue
                try:
                    toks = shlex.split(val)
                except ValueError:
                    toks = val.split()
                if toks and os.path.basename(toks[0]) == "env":
                    toks = [t for t in toks[1:] if "=" not in t and not t.startswith("-")]
                if toks:
                    n = os.path.basename(toks[0])
                    if n and n not in GENERIC_NAMES:
                        names.add(n)
    return names


# =========================================================================== applications
def _app_dirs(dirs: list | None = None) -> list:
    """applications/ directories in XDG precedence order (user first). Unreadable entries
    of XDG_DATA_DIRS are skipped instead of aborting."""
    if dirs is None:
        dirs = [Path(os.environ.get("XDG_DATA_HOME") or Path.home() / ".local" / "share")]
        sys_dirs = os.environ.get("XDG_DATA_DIRS") or "/usr/local/share:/usr/share"
        dirs += [Path(x) for x in sys_dirs.split(":") if x]
    out = []
    for d in _uniq(dirs):
        try:
            if (d / "applications").is_dir():
                out.append(d / "applications")
        except OSError:
            continue
    return out


def _desktop_files(appdir: Path) -> list:
    try:
        return sorted(appdir.rglob("*.desktop"))
    except OSError:
        return []


def desktop_apps(include_hidden: bool = False) -> list:
    """Installed applications: desktop-file ID, name, Exec and supported MIME types.
    The first file with a given ID wins (user directory overrides system directories).

    Args:
        include_hidden: Also list entries with NoDisplay=true.

    Returns:
        One dict per application (id, name, exec, mimes).
    """
    apps, seen = [], set()
    for appdir in _app_dirs():
        for f in _desktop_files(appdir):
            did = str(f.relative_to(appdir)).replace("/", "-")
            if did in seen:
                continue
            seen.add(did)
            e = _parse_desktop(f)
            if e.get("Type", "Application") != "Application" or not e.get("Exec"):
                continue
            hidden = (e.get("NoDisplay", "").lower() == "true"
                      or e.get("Hidden", "").lower() == "true")
            if hidden and not include_hidden:
                continue
            mimes = {m.strip() for m in e.get("MimeType", "").split(";") if m.strip()}
            apps.append({"id": did, "name": e.get("Name", did), "exec": e["Exec"],
                         "mimes": mimes, "wmclass": e.get("StartupWMClass", "")})
    return apps


_FIELD_FILE = ("%f", "%F", "%u", "%U")


def exec_tokens(exec_line: str, path: str | None = None) -> list:
    """Expand a desktop-entry Exec line. File codes (%f %F %u %U) become `path` (or are
    dropped if path is None); other field codes are removed; %% becomes %.

    Args:
        exec_line: Exec value of the desktop entry.
        path: File to open, or "" for none.

    Returns:
        The command as a list of arguments.
    """
    try:
        toks = shlex.split(exec_line)
    except ValueError:
        toks = exec_line.split()
    out, used = [], False
    for t in toks:
        if t in _FIELD_FILE:
            if path is not None:
                out.append(path)
                used = True
            continue
        if t in ("%i", "%c", "%k"):
            continue
        for code in _FIELD_FILE:
            if code in t:
                t = t.replace(code, path or "")
                used = used or path is not None
        t = re.sub(r"%[dDnNvmick]", "", t).replace("%%", "%")
        if t:
            out.append(t)
    if path is not None and not used:
        out.append(path)
    return out


def app_flag(argv: list) -> str:
    """The --app-id=... / --app=... argument of a browser command line, or ''.

    Args:
        argv: Command line of the browser.

    Returns:
        The app argument, or "".
    """
    return next((a for a in argv[1:] if a.startswith(APP_FLAGS)), "")


def app_launch(argv: list, launcher: list, apps: list | None = None) -> tuple[str, list]:
    """Name and start command of a browser app window (e.g. Claude as Chrome app).
    Prefers the desktop entry that carries the same --app-id (= the launcher entry);
    falls back to the recorded browser command line.

    Args:
        argv: Command line of the browser process.
        launcher: Command that starts the browser.
        apps: Installed applications (desktop_apps()); None reads them.

    Returns:
        (display name, start command).
    """
    flag = app_flag(argv)
    for app in apps if apps is not None else desktop_apps(include_hidden=True):
        if flag and flag in app["exec"]:
            return app["name"], exec_tokens(app["exec"])
    keep = [a for a in argv[1:] if a.startswith(("--user-data-dir=", "--profile-directory="))]
    return "Browser-App", launcher + keep + [flag]


BROWSER_FAMILIES = ("chromium", "chrome", "brave", "msedge", "vivaldi")
# Chrome-based browsers name the Wayland window of an installed web app
# "<browser>-<app id>-<profile>" (e.g. chrome-fmgjjm...-Default); the app id is the value of
# --app-id in the launcher entry. That is used to find app windows (e.g. Claude) that were
# opened in an ALREADY RUNNING browser - their process command line has no --app-id.
APPID_PREFIX = "appid:"


def browser_family(text: str) -> str:
    """'chrome' for /opt/google/chrome/chrome, google-chrome-stable, com.google.Chrome ...

    Args:
        text: Program name or path.

    Returns:
        Family name such as "chrome" or "firefox", or "".
    """
    t = text.lower()
    return next((f for f in BROWSER_FAMILIES if f in t), "")


def _opt_value(argv: list, name: str) -> str:
    return next((a.split("=", 1)[1] for a in argv if a.startswith(name + "=")), "")


def browser_app_windows(windows: list | None, apps: list) -> list:
    """[(desktop app, exec tokens, app id)] for every open window of a browser web app.

    Args:
        windows: Open windows as (app_id, title).
        apps: Installed applications.

    Returns:
        List of (application, exec tokens, app id).
    """
    out, seen = [], set()
    for a in apps:
        toks = exec_tokens(a["exec"])
        aid = _opt_value(toks, "--app-id")
        if not aid or aid in seen or not browser_family(" ".join(toks)):
            continue
        if any(aid in app_id for app_id, _t in windows or []):
            out.append((a, toks, aid))
            seen.add(aid)
    return out


def app_window_entries(entries: list, windows: list | None, procs: dict,
                       apps: list | None = None) -> list:
    """Entries for web-app windows (e.g. Claude) found only through the window list. They
    belong to the running browser process of the same family and user-data-dir.

    Args:
        entries: Entries recorded so far.
        windows: Open windows, or None if unknown.
        procs: Running processes.
        apps: Installed applications; None reads them.

    Returns:
        Additional entries for app windows not yet recorded.
    """
    if not windows:
        return []
    have = {_opt_value(e["cmd"], "--app-id") for e in entries if e.get("app")}
    browsers = [e for e in entries if e["category"] == CAT_BROWSER and not e.get("app")]
    out = []
    for a, toks, aid in browser_app_windows(windows, apps if apps is not None
                                            else desktop_apps(include_hidden=True)):
        if aid in have:
            continue
        fam, udd = browser_family(" ".join(toks)), _opt_value(toks, "--user-data-dir")
        host = next((b for b in browsers
                     if browser_family(" ".join([b["program"]] + b["cmd"][:1])) == fam
                     and _opt_value(b["cmd"], "--user-data-dir") == udd), None)
        p = procs.get(host["pid"]) if host else None
        if p is None or host is None:
            continue
        out.append(dict(_entry(CAT_BROWSER, a["name"], toks, [], p, host.get("flatpak", ""),
                               host.get("protect", "")), app=True))
    return out


_ALIAS_CACHE: dict = {}


def desktop_aliases() -> dict:
    """{binary name: {desktop id, StartupWMClass}} - the names under which COSMIC shows the
    windows of a program (app_id = desktop id, e.g. com.system76.CosmicTerm for cosmic-term).
    Cached per XDG data directories.

    Returns:
        Mapping of executable name to the set of alternative window names.
    """
    key = (os.environ.get("XDG_DATA_HOME", ""), os.environ.get("XDG_DATA_DIRS", ""))
    if key not in _ALIAS_CACHE:
        out: dict = {}
        for a in desktop_apps(include_hidden=True):
            toks = exec_tokens(a["exec"])
            if toks and os.path.basename(toks[0]) == "env":
                toks = [t for t in toks[1:] if "=" not in t and not t.startswith("-")]
            if not toks or os.path.basename(toks[0]) in GENERIC_NAMES:
                continue
            names = {a["id"].removesuffix(".desktop")}
            if a["wmclass"]:
                names.add(a["wmclass"])
            out.setdefault(os.path.basename(toks[0]), set()).update(names)
        _ALIAS_CACHE.clear()
        _ALIAS_CACHE[key] = out
    return _ALIAS_CACHE[key]


def mime_of(path: str) -> str:
    """MIME type guessed from the file name ('' if unknown).

    Args:
        path: File name.

    Returns:
        MIME type or "".
    """
    return mimetypes.guess_type(path, strict=False)[0] or ""


def handlers_for(mimes: list, apps: list | None = None) -> list:
    """[(app, n)] - applications that open n of the given MIME types, best first.

    Args:
        mimes: MIME types of the files.
        apps: Installed applications.

    Returns:
        List of (application, number of types it opens), best first.
    """
    res = []
    for app in apps if apps is not None else desktop_apps():
        n = sum(1 for m in mimes if m and m in app["mimes"])
        if n:
            res.append((app, n))
    res.sort(key=lambda t: (-t[1], t[0]["name"].lower()))
    return res


def load_programs() -> dict:
    """Program choice per slot from programs.ini ({} if missing).

    Returns:
        Mapping of slot ("video", "image") to the chosen program.
    """
    cp = configparser.ConfigParser(interpolation=None)
    try:
        cp.read(PROGRAMS_FILE, encoding="utf-8")
    except configparser.Error:
        return {}
    if not cp.has_section("programs"):
        return {}
    return {k: v.strip() for k, v in cp["programs"].items() if v.strip()}


def save_programs(progs: dict) -> None:
    """Write the program choice to programs.ini (atomically).

    Args:
        progs: Mapping of slot to program (empty values are dropped).
    """
    cp = configparser.ConfigParser(interpolation=None)
    cp["programs"] = {k: v for k, v in progs.items() if v}
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    tmp = PROGRAMS_FILE.with_name(PROGRAMS_FILE.name + ".tmp")
    with open(tmp, "w", encoding="utf-8") as fh:
        fh.write("# written by session-snap: programs used to open videos/images on restore\n"
                 "# value = desktop-file ID (e.g. mpv.desktop) or a command (e.g. mpv)\n")
        cp.write(fh)
    os.replace(tmp, PROGRAMS_FILE)
    log.info("programs saved: %s", progs)


def program_label(spec: str | None, apps: list | None = None) -> str:
    """Readable name of a program choice ('wie aufgezeichnet' if none).

    Args:
        spec: Desktop-file ID or command, or None.
        apps: Installed applications; None reads them.

    Returns:
        Text for menus.
    """
    if not spec:
        return "wie aufgezeichnet"
    if spec.endswith(".desktop"):
        for app in apps if apps is not None else desktop_apps(include_hidden=True):
            if app["id"] == spec:
                return f"{app['name']} ({spec})"
    return spec


def program_cmd(spec: str, path: str, apps: list) -> list:
    """Start command for `path` with the chosen program (desktop ID or command).

    Args:
        spec: Desktop-file ID or command.
        path: File to open.
        apps: Installed applications; None reads them.

    Returns:
        The start command.

    Raises:
        LookupError: If a desktop-file ID is not installed.
    """
    if spec.endswith(".desktop"):
        app = next((a for a in apps if a["id"] == spec), None)
        if app is None:
            raise LookupError(f"Programm {spec} nicht gefunden - Originalprogramm verwendet")
        return exec_tokens(app["exec"], path)
    return shlex.split(spec) + [path]


def apply_programs(entries: list, progs: dict) -> tuple[list, list]:
    """Replace the start command of video/image entries by the chosen program.
    Returns (entries, warnings); entries without a choice stay unchanged.

    Args:
        entries: Entries to start.
        progs: Program choice per slot.

    Returns:
        (entries with replaced commands, warnings).
    """
    if not any(progs.get(s) for s in PROGRAM_SLOTS.values()):
        return entries, []
    apps = desktop_apps(include_hidden=True)
    out, warn = [], []
    for e in entries:
        spec = progs.get(PROGRAM_SLOTS.get(e["category"], ""), "")
        if not spec or not e["files"]:
            out.append(e)
            continue
        try:
            out.append(dict(e, cmd=program_cmd(spec, e["files"][0], apps), via=spec))
        except LookupError as exc:
            warn.append(str(exc))
            out.append(e)
    return out, _uniq(warn)


def player_of(cmd: list) -> tuple[str, int]:
    """(player name, index where options are inserted) of a start command.

    Args:
        cmd: Start command.

    Returns:
        (player name, insertion index).
    """
    if len(cmd) >= 3 and os.path.basename(cmd[0]) == "flatpak" and cmd[1] == "run":
        return PLAYER_FLATPAKS.get(cmd[2], cmd[2]), 3
    return (os.path.basename(cmd[0]), 1) if cmd else ("", 0)


def apply_player_options(entries: list, popts: dict, cats: set | None = None) -> list:
    """Insert the configured start options (e.g. paused, fullscreen) into the commands of
    the entries of the categories `cats` (default: videos). Options already present in the
    command are not added twice.

    Args:
        entries: Entries to start.
        popts: Options per player.
        cats: Categories the options apply to.

    Returns:
        The entries with options inserted (copies where changed).
    """
    cats = {CAT_VLC} if cats is None else cats
    out = []
    for e in entries:
        if e["category"] not in cats or not e["files"]:
            out.append(e)
            continue
        player, at = player_of(e["cmd"])
        have = {a.split("=", 1)[0] for a in e["cmd"]}
        add = [o for o in popts.get(player, []) if o.split("=", 1)[0] not in have]
        if add:
            e = dict(e, cmd=e["cmd"][:at] + add + e["cmd"][at:], started=" ".join(add))
        out.append(e)
    return out


# =========================================================================== entries
# Environment variables that decide how a program looks (theme, dark mode, toolkit backend,
# language). They are recorded per program and set again on restore, so that a program
# restarted from a terminal looks exactly like the one started from the launcher or the file
# manager (whose environment can differ from the terminal's). Only these variables are
# stored - never the complete environment, which may contain tokens or passwords.
ENV_PATTERNS = ("GTK_*", "GDK_*", "QT_*", "ADW_*", "GSETTINGS_*", "XCURSOR_*",
                "XDG_CURRENT_DESKTOP", "XDG_SESSION_DESKTOP", "XDG_SESSION_TYPE",
                "XDG_DATA_DIRS", "XDG_CONFIG_DIRS", "XDG_DATA_HOME", "XDG_CONFIG_HOME",
                "DESKTOP_SESSION", "LANG", "LANGUAGE", "LC_*", "NO_AT_BRIDGE")


def is_look_var(name: str) -> bool:
    """True if the environment variable belongs to the appearance.

    Args:
        name: Name of the environment variable.

    Returns:
        True for theme, scaling and colour-scheme variables.
    """
    return any(fnmatch.fnmatchcase(name, pat) for pat in ENV_PATTERNS)


def look_env(pid: int) -> dict:
    """The appearance-relevant variables of a running process (empty if unreadable).

    Args:
        pid: Process ID.

    Returns:
        Mapping of variable name to value.
    """
    return {k: v for k, v in _read_environ(pid).items() if is_look_var(k)}


# Processes of the desktop session whose environment is the "system" one - the environment
# programs get when they are started from the dock, the launcher or the file manager.
SESSION_REFS = ("cosmic-panel", "cosmic-app-list", "cosmic-files", "cosmic-launcher",
                "cosmic-session", "gnome-shell", "plasmashell", "xfce4-session")


def session_look_env() -> tuple[str, int, dict] | None:
    """(name, pid, appearance variables) of the first running session process of this user
    in SESSION_REFS order, or None (no desktop session found, e.g. over SSH).

    Returns:
        (process name, PID, variables), or None if no session process was found.
    """
    uid, found = os.getuid(), {}
    for d in os.listdir("/proc"):
        if not d.isdigit():
            continue
        try:
            if os.stat(f"/proc/{d}").st_uid != uid:
                continue
            with open(f"/proc/{d}/comm", encoding="utf-8", errors="replace") as fh:
                comm = fh.read().strip()
        except OSError:
            continue
        names = {comm, os.path.basename(_readlink(f"/proc/{d}/exe"))}
        for n in SESSION_REFS:
            if n in names and n not in found:
                found[n] = int(d)
    for n in SESSION_REFS:
        if n in found:
            env = _read_environ(found[n])
            if env:
                return n, found[n], {k: v for k, v in env.items() if is_look_var(k)}
    return None


def restore_env(saved: dict | None, ref=False) -> dict | None:
    """Environment for a restored program: the current one, with all appearance-relevant
    variables (theme, dark mode, ...) taken from the DESKTOP SESSION, so that a restored
    program always follows the current system theme - light or dark - exactly like a program
    started from the launcher. Without a desktop session the recorded variables are used;
    without both the environment stays unchanged (None). `ref`: result of
    session_look_env(), False = determine it now.

    Args:
        saved: Appearance variables stored in the snapshot, or None.
        ref (tuple | None | bool): Reference session process from session_look_env(); False queries
            it now.

    Returns:
        Environment for the new process, or None to inherit the current one.
    """
    if ref is False:
        ref = session_look_env()
    look = ref[2] if ref else saved
    if look is None:
        return None
    env = {k: v for k, v in os.environ.items() if not is_look_var(k)}
    env.update(look)
    return env


def _entry(cat, program, cmd, files, p: Proc, flatpak="", protect=""):
    e = {"category": cat, "program": program, "cmd": list(cmd), "cwd": p.cwd or "",
         "files": list(files), "pid": p.pid, "start": p.start, "flatpak": flatpak,
         "protect": protect}
    env = look_env(p.pid)
    if env:
        e["env"] = env
    return e


def _make_entries(cat, program, launcher, orig_cmd, files, p: Proc, flatpak="", protect=""):
    """Videos/images: one entry per file (individually selectable). Others: one per program."""
    if cat in (CAT_VLC, CAT_IMG) and files:
        return [_entry(cat, program, launcher + [f], [f], p, flatpak, protect) for f in files]
    return [_entry(cat, program, orig_cmd, files, p, flatpak, protect)]


def protect_reason(argv: list, cfg: dict) -> str:
    """Why kill must never touch this process ('' = not protected).

    Args:
        argv: Command line of the process.
        cfg: Configuration.

    Returns:
        Reason, or "" if the process may be terminated.
    """
    if cfg.get("protect") and _match_any([" ".join(argv)], cfg["protect"]):
        return "Schutzmuster (protect) in config.ini"
    return ""


def browser_cmd(launcher: list, argv: list) -> list:
    """Restart command for a browser: keep only the profile selection, drop app flags and
    URLs - the browser restores its own session (windows and tabs).

    Args:
        launcher: Command that starts the browser.
        argv: Recorded command line.

    Returns:
        The restart command.
    """
    keep, i = [], 1
    while i < len(argv):
        a = argv[i]
        if a.startswith(("--user-data-dir=", "--profile-directory=")):
            keep.append(a)
        elif a in ("-P", "--profile", "-profile") and i + 1 < len(argv):
            keep += [a, argv[i + 1]]
            i += 1
        i += 1
    return launcher + keep


# =========================================================================== browser sessions
# Chrome-based (chrome/browser/ui/startup/startup_browser_creator.cc): with
# --restore-last-session every profile Chrome opens at startup restores its last session
# (pref.type = SessionStartupPref::LAST); without --profile-directory Chrome opens ALL
# profiles that were open at the end (Local State: profile.last_active_profiles,
# LaunchBrowserForLastProfiles). Works only when Chrome STARTS - a running (background)
# Chrome ignores the switch. Session data is written when Chrome ends normally (SIGTERM).
# Firefox (browser/components/sessionstore): browser.sessionstore.resume_session_once = true
# restores the last session at the next start; Firefox resets it itself afterwards
# (SessionStore.sys.mjs). The profile of a running Firefox is the directory of its open
# lock file ".parentlock" (toolkit/profile/nsProfileLock.cpp).
CHROME_DATA_DIRS = {"chrome": ".config/google-chrome", "chromium": ".config/chromium",
                    "brave": ".config/BraveSoftware/Brave-Browser",
                    "msedge": ".config/microsoft-edge", "vivaldi": ".config/vivaldi"}
FIREFOX_NAMES = {"firefox", "firefox-bin", "firefox-esr", "librewolf", "librewolf-bin"}
RESUME_PREF = "browser.sessionstore.resume_session_once"


def chrome_data_dir(e: dict) -> Path | None:
    """User data directory of a Chrome-based browser entry (None if unknown).

    Args:
        e: Browser entry.

    Returns:
        The directory, or None.
    """
    udd = _opt_value(e["cmd"], "--user-data-dir")
    if udd:
        return Path(udd).expanduser()
    sub = CHROME_DATA_DIRS.get(browser_family(" ".join([e["program"]] + e["cmd"][:1])))
    return Path.home() / sub if sub else None


def chrome_profiles(data_dir: Path | None) -> list:
    """[(profile directory, display name)] of the profiles open at the moment (or at the end
    of the last run), from Chrome's 'Local State'.

    Args:
        data_dir: User data directory of the browser, or None.

    Returns:
        List of (profile directory, display name).
    """
    if data_dir is None:
        return []
    try:
        st = json.loads((data_dir / "Local State").read_text(encoding="utf-8"))
    except (OSError, ValueError, TypeError):
        return []
    prof = st.get("profile", {}) if isinstance(st, dict) else {}
    cache = prof.get("info_cache", {}) or {}
    return [(d, (cache.get(d) or {}).get("name", d))
            for d in prof.get("last_active_profiles", []) or []]


def firefox_profile_dir(pid: int) -> str:
    """Profile directory of a running Firefox: directory of its open '.parentlock'.

    Args:
        pid: Process ID of Firefox.

    Returns:
        The profile directory, or "".
    """
    try:
        fds = os.listdir(f"/proc/{pid}/fd")
    except OSError:
        return ""
    for fd in fds:
        target = _readlink(f"/proc/{pid}/fd/{fd}")
        if target.endswith("/.parentlock"):
            return os.path.dirname(target)
    return ""


def is_firefox(e: dict) -> bool:
    """True if the entry is a Firefox (also LibreWolf, Firefox flatpak).

    Args:
        e: Entry.

    Returns:
        True for Firefox and its forks.
    """
    names = {e["program"], os.path.basename(e["cmd"][0]) if e["cmd"] else ""}
    return bool(names & FIREFOX_NAMES) or e.get("flatpak") == "org.mozilla.firefox"


def annotate_browser_sessions(entries: list) -> None:
    """At save: remember which browser profiles are open (shown in 'show', used on
    restore).

    Args:
        entries: Entries about to be saved (changed in place).
    """
    for e in entries:
        if e["category"] != CAT_BROWSER or e.get("app"):
            continue
        if is_firefox(e):
            d = firefox_profile_dir(e.get("pid", 0))
            if d:
                e["browser_profiles"] = [{"dir": d, "name": os.path.basename(d)}]
        else:
            profs = chrome_profiles(chrome_data_dir(e))
            if profs:
                e["browser_profiles"] = [{"dir": d, "name": n} for d, n in profs]


def firefox_set_resume(profile_dir: str) -> str:
    """Set resume_session_once in prefs.js of a profile that is NOT running (a backup of
    prefs.js is kept). Returns '' or the reason why nothing was changed.

    Args:
        profile_dir: Profile directory.

    Returns:
        A message for the terminal.
    """
    prefs = Path(profile_dir) / "prefs.js"
    if not Path(profile_dir).is_dir():
        return f"Profil fehlt: {profile_dir}"
    for d in os.listdir("/proc"):
        if d.isdigit() and firefox_profile_dir(int(d)) == str(Path(profile_dir)):
            return "Firefox laeuft mit diesem Profil"
    text = prefs.read_text(encoding="utf-8", errors="surrogateescape") if prefs.exists() else ""
    lines = [ln for ln in text.splitlines() if f'"{RESUME_PREF}"' not in ln]
    if prefs.exists():
        b = backup_file(prefs, f"firefox-prefs {Path(profile_dir).name}", None, ext=".js")
        log.info("prefs.js backup: %s", b)
    lines.append(f'user_pref("{RESUME_PREF}", true);')
    tmp = prefs.with_name("prefs.js.session-snap-tmp")
    tmp.write_text("\n".join(lines) + "\n", encoding="utf-8", errors="surrogateescape")
    os.replace(tmp, prefs)
    log.info("firefox resume_session_once set: %s", profile_dir)
    return ""


def end_background_browser(e: dict, timeout: float = 8.0) -> bool:
    """Chrome without any window (still running in the background) would swallow
    --restore-last-session: end it (SIGTERM) before the restore. True if one was ended.

    Args:
        e: Browser entry to be restored.
        timeout: Seconds to wait for the end.

    Returns:
        True if a background process was ended.
    """
    sig = signature(e)
    fam = browser_family(" ".join([e["program"]] + e["cmd"][:1]))
    wins = list_windows()
    if wins is None:               # no window list: cannot tell "background" - never end it
        return False
    if any(a.lower().startswith(fam + "-") or app_matches(a, window_names(e))
           for a, _t in wins):
        print("Chrome hat noch Fenster (z. B. ein App-Fenster wie Claude) - wird nicht "
              "beendet; die Sitzung laesst sich dann nicht wiederherstellen")
        return False
    ended = False
    for cur in build_entries(load_config(), windows=None):
        if cur["category"] == CAT_BROWSER and not cur.get("app") and signature(cur) == sig:
            print(f"Chrome laeuft ohne Fenster im Hintergrund (PID {cur['pid']}) - "
                  "wird beendet, damit die Sitzung wiederhergestellt werden kann")
            log.info("end background browser pid %d", cur["pid"])
            _signal(cur, signal.SIGTERM)
            end = time.monotonic() + timeout
            while is_alive(cur["pid"], cur["start"]) and time.monotonic() < end:
                time.sleep(0.1)
            ended = True
    return ended


def prepare_browser_sessions(entries: list, dry_run: bool = False) -> list:
    """At restore: Chrome-based -> started without --profile-directory, with
    --restore-last-session (all last profiles with windows and tabs); Firefox -> its
    profile gets resume_session_once. Returns the entries (browser commands adjusted).

    Args:
        entries: Entries to start.
        dry_run: Only report, change nothing on disk.

    Returns:
        The entries with adapted browser commands.
    """
    out = []
    for e in entries:
        if e["category"] != CAT_BROWSER or e.get("app") or not e["cmd"]:
            out.append(e)
            continue
        if is_firefox(e):
            for prof in e.get("browser_profiles", []):
                why = "" if dry_run else firefox_set_resume(prof["dir"])
                print(f"Firefox-Sitzung {prof['name']}: "
                      + (f"nicht vorbereitet - {why}" if why else "wird wiederhergestellt"))
            out.append(e)
            continue
        if browser_family(" ".join([e["program"]] + e["cmd"][:1])) in BROWSER_FAMILIES:
            cmd = [a for a in e["cmd"] if not a.startswith("--profile-directory=")]
            if "--restore-last-session" not in cmd:
                cmd.append("--restore-last-session")
            names = ", ".join(p["name"] for p in e.get("browser_profiles", [])) or "zuletzt offene"
            print(f"Chrome-Sitzung: Profile {names} mit allen Fenstern und Tabs")
            if not dry_run:
                end_background_browser(e)
            e = dict(e, cmd=cmd)
        out.append(e)
    return out


def _flatpak_entries(procs: dict, cfg: dict) -> list:
    """Flatpak apps, grouped by sandbox instance; restarted via `flatpak run <app-id>`."""
    entries: list = []
    groups: dict = {}
    for p in procs.values():
        if p.flatpak_id and not p.names & GENERIC_NAMES:
            groups.setdefault(p.flatpak_inst or f"{p.flatpak_id}:{p.pid}", []).append(p)
    for group in groups.values():
        group.sort(key=lambda q: q.start)
        first, app = group[0], group[0].flatpak_id
        if _match_any([app], cfg["exclude"]):
            continue
        argf = _uniq([f for q in group for f in argv_files(q)])
        launcher = ["flatpak", "run", app]
        prot = protect_reason(first.argv, cfg)
        if app in BROWSER_FLATPAKS:
            entries += _make_entries(CAT_BROWSER, app, launcher, launcher, [], first, app, prot)
            if app_flag(first.argv):
                entries.append(dict(_entry(CAT_BROWSER, f"{app} (App)",
                                           launcher + [app_flag(first.argv)], [], first, app,
                                           prot), app=True))
        elif app in VLC_FLATPAKS:
            fds = [f for q in group for f in fd_files(q.pid, MEDIA_EXT | IMAGE_EXT)]
            files = _uniq(argf + fds)
            entries += _make_entries(CAT_VLC, app, launcher, launcher, files, first, app, prot)
        elif app in IMAGE_FLATPAKS or (argf and all(_ext(f) in IMAGE_EXT for f in argf)):
            fds = [f for q in group for f in fd_files(q.pid, IMAGE_EXT)]
            files = _uniq([f for f in argf if _ext(f) in IMAGE_EXT] + fds)
            entries += _make_entries(CAT_IMG, app, launcher, launcher, files, first, app, prot)
        else:
            entries += _make_entries(CAT_REST, app, launcher, launcher + argf, argf, first, app,
                                     prot)
    return entries


def _file_entries(cat, p: Proc, launcher: list, orig: list, files: list, prot: str,
                  desk_ids: set) -> list:
    """Video/image entries: one per file, restarted with the ORIGINAL program and options.
    The program binary is the recorded executable (argv[0] if exe is an interpreter)."""
    if not files:
        return _make_entries(cat, p.name, launcher, orig, [], p, "", prot)
    head = launcher if os.path.basename(launcher[0]) == os.path.basename(orig[0]) else orig[:1]
    idx = {i for i, _ in argv_file_args(p)}
    origin, source = launched_from(p.pid, p.ppid, desk_ids)
    out = []
    for f in files:
        e = _entry(cat, p.name, file_cmd(head, orig, idx, f), [f], p, "", prot)
        e.update(argv=list(orig), launched_from=origin, launched_via=source)
        out.append(e)
    return out


def _host_entries(procs: dict, cfg: dict, desk: set) -> list:
    entries, apps = [], None
    desk_ids = {a["id"] for a in desktop_apps(include_hidden=True)}
    viewers, include = cfg["image_viewers"], cfg["include"]
    for p in procs.values():
        if p.flatpak_id:
            continue
        parent = procs.get(p.ppid)
        if parent and p.exe and parent.exe == p.exe:
            continue  # child process of the same application (browser/electron renderers)
        names = p.names
        if _match_any(names, cfg["exclude"]) and not _match_any(names, include):
            continue
        launcher = [p.exe] if p.exe and os.access(p.exe, os.X_OK) else [p.argv[0]]
        orig = p.argv
        if len(orig) == 1 and " " in orig[0] and not os.path.exists(orig[0]):
            orig = shlex.split(orig[0])
        if orig and "/" in orig[0] and not os.path.isabs(orig[0]) and p.cwd:
            orig = [os.path.normpath(os.path.join(p.cwd, orig[0]))] + orig[1:]
        argf = argv_files(p)
        only_images = bool(argf) and all(_ext(f) in IMAGE_EXT for f in argf)
        prot = protect_reason(p.argv, cfg)

        if names & BROWSER_NAMES:
            cmd = browser_cmd(launcher, orig)
            entries += _make_entries(CAT_BROWSER, p.name, launcher, cmd, [], p, "", prot)
            if app_flag(orig):  # app window in this browser process (e.g. Claude)
                if apps is None:
                    apps = desktop_apps(include_hidden=True)
                name, acmd = app_launch(orig, launcher, apps)
                entries.append(dict(_entry(CAT_BROWSER, name, acmd, [], p, "", prot), app=True))
        elif names & cfg.get("video_players", VIDEO_PLAYERS):
            files = _uniq(argf + fd_files(p.pid, MEDIA_EXT | IMAGE_EXT))
            entries += _file_entries(CAT_VLC, p, launcher, orig, files, prot, desk_ids)
        elif names & viewers or (only_images and (names & desk or _match_any(names, include))):
            imgs = [f for f in argf if _ext(f) in IMAGE_EXT]
            files = _uniq(imgs + fd_files(p.pid, IMAGE_EXT))
            entries += _file_entries(CAT_IMG, p, launcher, orig, files, prot, desk_ids)
        elif (names - GENERIC_NAMES) & desk or _match_any(names, include):
            entries += _make_entries(CAT_REST, p.name, launcher, orig, argf, p, "", prot)
    return entries


CAPTURE_WARNING = ""   # set by build_entries(): why the last capture may be incomplete


def build_entries(cfg: dict, procs: dict | None = None, windows=False) -> list:
    """All recordable entries. `windows`: list of (app_id, title), None (not available), or
    False (= query the compositor now).

    Args:
        cfg: Configuration.
        procs: Running processes; None scans them.
        windows (list | None | bool): Open windows, None if unknown, False to query now.

    Returns:
        The entries, sorted by category, program and file.
    """
    global CAPTURE_WARNING  # pylint: disable=global-statement  # read right after the call
    procs = procs if procs is not None else scan_processes()
    entries = _flatpak_entries(procs, cfg) + _host_entries(procs, cfg, desktop_exec_names())
    win = list_windows() if windows is False else windows
    CAPTURE_WARNING = ""
    if win is None and windows is False and any(e["category"] == CAT_IMG for e in entries):
        CAPTURE_WARNING = ("ACHTUNG: Fensterliste nicht verfuegbar (System ausgelastet?) - "
                           "von Bildbetrachtern mit mehreren Fenstern (eog ...) ist nur das "
                           "erste Bild erkannt. Zahl der Bilder unvollstaendig.")
        log.warning("capture incomplete: window list unavailable")
    entries = add_window_files(entries, win)
    entries += app_window_entries(entries, win, procs)
    seen, out = set(), []
    for e in entries:  # de-duplicate (e.g. same image opened twice)
        key = (e["category"], tuple(e["cmd"]))
        if key not in seen:
            seen.add(key)
            out.append(e)
    out.sort(key=lambda e: (CAT_ORDER.index(e["category"]), e["program"].lower(),
                            e["files"][0].lower() if e["files"] else ""))
    return out


def signature(e: dict) -> tuple:
    """Identity of an entry independent of PID. Videos/images: category + files only, so a
    file counts as open no matter which program shows it (VLC, mpv, eog, nomacs, ...).
    Others: category, program and arguments.

    Args:
        e: Entry.

    Returns:
        A hashable identity.
    """
    if e["category"] in (CAT_VLC, CAT_IMG) and e["files"]:
        return (e["category"], tuple(e["files"]))
    if e["category"] == CAT_BROWSER and not e.get("app"):
        # Chrome-based: ONE process serves all profiles; --profile-directory only says which
        # window opened first (Chrome started through an app has it, a plain start not).
        # Identity = browser family + user data directory.
        fam = browser_family(" ".join([e["program"]] + e["cmd"][:1]))
        if fam in BROWSER_FAMILIES:
            return (e["category"], fam, _opt_value(e["cmd"], "--user-data-dir"))
    rest = tuple(e["files"]) if e["files"] else tuple(e["cmd"][1:])
    return (e["category"], e["program"], rest)


# =========================================================================== snapshots
def snapshot_name(now: datetime) -> str:
    """File name of a snapshot taken at `now`.

    Args:
        now: Time of the capture.

    Returns:
        The file name.
    """
    return f"{FILE_PREFIX}{now.strftime('%Y-%m-%d_%H-%M-%S')}.json"


def save_snapshot(entries: list, label: str = "", order_source: str = "") -> Path:
    """Write the entries as a new snapshot file; returns its path.

    Args:
        entries: Entries to save.
        label: Descriptive name ("" for none).
        order_source: Where the Alt-Tab order came from ("watch", "creation" or "").

    Returns:
        Path of the new file.
    """
    SNAP_DIR.mkdir(parents=True, exist_ok=True)
    now = datetime.now()
    path, n = SNAP_DIR / snapshot_name(now), 1
    while path.exists():  # two captures within the same second
        path, n = SNAP_DIR / snapshot_name(now).replace(".json", f"_{n}.json"), n + 1
    data = {
        "format": 1, "tool": "session-snap", "version": VERSION,
        "created": now.isoformat(timespec="seconds"), "label": label,
        "host": socket.gethostname(), "user": os.environ.get("USER", ""),
        "session_type": os.environ.get("XDG_SESSION_TYPE", ""),
        "desktop": os.environ.get("XDG_CURRENT_DESKTOP", ""),
        "order_source": order_source,
        "entries": entries,
    }
    tmp = path.with_name(path.name + ".tmp")
    with open(tmp, "w", encoding="utf-8", errors="surrogateescape") as fh:
        json.dump(data, fh, ensure_ascii=False, indent=2)
        fh.write("\n")
    os.replace(tmp, path)  # atomic: a crash never leaves a half-written snapshot
    log.info("snapshot saved: %s (%d entries, label=%r)", path, len(entries), label)
    return path


def load_snapshot(path: Path) -> dict:
    """Read a snapshot file.

    Args:
        path: Snapshot file.

    Returns:
        The snapshot data.
    """
    with open(path, encoding="utf-8", errors="surrogateescape") as fh:
        return json.load(fh)


def list_snapshots() -> list:
    """Snapshot files, newest first (archived ones excluded).

    Returns:
        Paths, newest first.
    """
    if not SNAP_DIR.is_dir():
        return []
    return sorted(SNAP_DIR.glob(f"{FILE_PREFIX}*.json"), key=lambda p: p.name, reverse=True)


def archive_dir() -> Path:
    """Archived snapshots: not offered for restore/kill, kept until deleted.

    Returns:
        The archive folder.
    """
    return SNAP_DIR / "archiv"


def list_archived() -> list:
    """Archived snapshot files, newest first.

    Returns:
        Paths, newest first.
    """
    d = archive_dir()
    if not d.is_dir():
        return []
    return sorted(d.glob(f"{FILE_PREFIX}*.json"), key=lambda p: p.name, reverse=True)


def rename_snapshot(path: Path, label: str) -> None:
    """Set the label (shown in all lists); the file name (date/time) stays.

    Args:
        path: Snapshot file.
        label: New label ("" removes it).
    """
    data = load_snapshot(path)
    old, data["label"] = data.get("label", ""), label
    tmp = path.with_name(path.name + ".tmp")
    with open(tmp, "w", encoding="utf-8", errors="surrogateescape") as fh:
        json.dump(data, fh, ensure_ascii=False, indent=2)
        fh.write("\n")
    os.replace(tmp, path)
    log.info("snapshot renamed: %s %r -> %r", path.name, old, label)


def move_snapshot(path: Path, to_archive: bool) -> Path:
    """Archive (to_archive=True) or bring back; never overwrites an existing file.

    Args:
        path: Snapshot file.
        to_archive: True archives, False brings it back.

    Returns:
        The new path.

    Raises:
        OSError: If a file of that name exists at the destination.
    """
    dest_dir = archive_dir() if to_archive else SNAP_DIR
    dest_dir.mkdir(parents=True, exist_ok=True)
    dest = dest_dir / path.name
    if dest.exists():
        raise OSError(f"{dest} existiert bereits")
    os.replace(path, dest)
    log.info("snapshot %s: %s", "archived" if to_archive else "unarchived", path.name)
    return dest


def delete_snapshot(path: Path) -> None:
    """Delete a snapshot file.

    Args:
        path: Snapshot file.
    """
    path.unlink()
    log.info("snapshot deleted: %s", path)


def _counts(entries) -> str:
    c = {k: 0 for k in CAT_ORDER}
    for e in entries:
        c[e["category"]] = c.get(e["category"], 0) + 1
    return " | ".join(f"{CAT_SHORT[k]} {c[k]:>2}" for k in CAT_ORDER)


def snapshot_title(path: Path) -> str:
    """One line for menus: creation time, label and counts per category.

    Args:
        path: Snapshot file.

    Returns:
        The menu line.
    """
    try:
        d = load_snapshot(path)
    except (OSError, json.JSONDecodeError) as exc:
        return f"{path.name}  [unlesbar: {exc}]"
    created = d.get("created", "").replace("T", " ")
    label = f"  «{d['label']}»" if d.get("label") else ""
    return f"{created}  [{_counts(d.get('entries', []))}]{label}"


# =========================================================================== launching
def launch(e: dict, ref=False) -> tuple[bool, str]:
    """Start one entry detached from this terminal; returns (ok, message).

    Args:
        e: Entry to start.
        ref (tuple | None | bool): Reference session process for the appearance; False queries it
            now.

    Returns:
        (started, message).
    """
    cmd = list(e["cmd"])
    if not cmd:
        return False, "leerer Befehl"
    exe = cmd[0]
    if not (shutil.which(exe) or (os.path.isabs(exe) and os.access(exe, os.X_OK))):
        return False, f"Programm nicht gefunden: {exe}"
    missing = [f for f in e.get("files", []) if not os.path.exists(f)]
    if missing and e["category"] in (CAT_VLC, CAT_IMG):
        return False, f"Datei fehlt: {missing[0]}"
    cwd = e["cwd"] if e.get("cwd") and os.path.isdir(e["cwd"]) else str(Path.home())
    try:
        spawn_detached(cmd, cwd, restore_env(e.get("env"), ref))
    except OSError as exc:
        return False, str(exc)
    return True, "gestartet"


def spawn_detached(cmd: list, cwd: str, env: dict | None = None) -> None:
    """Start a program completely detached (double fork + setsid).

    The program is re-parented to the session's subreaper/init: it survives the end of
    session-snap and the closing of the terminal, and this process never owns it (no zombie).
    exec() errors are reported back through a pipe that closes automatically on exec.

    Args:
        cmd: Command.
        cwd: Working directory.
        env: Environment; None inherits the current one.

    Raises:
        OSError: If the program cannot be executed (message of the child process).
    """
    r, w = os.pipe()  # non-inheritable (O_CLOEXEC) since Python 3.4
    pid = os.fork()
    if pid == 0:  # intermediate child
        try:
            os.close(r)
            os.setsid()  # new session, no controlling terminal
            if os.fork() > 0:
                os._exit(0)
            os.chdir(cwd)  # grandchild = the program
            null = os.open(os.devnull, os.O_RDWR)
            for fd in (0, 1, 2):
                os.dup2(null, fd)
            if env is None:
                os.execvp(cmd[0], cmd)
            os.execvpe(cmd[0], cmd, env)
        except BaseException as exc:  # pylint: disable=broad-exception-caught  # child process
            try:
                os.write(w, f"{type(exc).__name__}: {exc}".encode()[:512])
            finally:
                os._exit(127)
    os.close(w)
    os.waitpid(pid, 0)  # reap the intermediate child immediately
    with os.fdopen(r, "rb") as fh:
        err = fh.read()
    if err:
        raise OSError(err.decode("utf-8", "replace"))


def _prefix(i: int, n: int, e: dict) -> str:
    return f"[{i:>3}/{n}] {CAT_LABEL[e['category']]:<18}"


def window_names(e: dict) -> set:
    """Names under which the window of an entry may appear (app_id candidates).

    Args:
        e: Entry.

    Returns:
        Lower-case name candidates.
    """
    names = {player_of(e["cmd"])[0], os.path.basename(e["cmd"][0]), e.get("program", "")}
    if e.get("launched_from"):
        names.add(e["launched_from"])
    names = {n for n in names if n and n not in GENERIC_NAMES and n != "flatpak"}
    if e.get("app"):           # web-app window: only its own app id, never the browser
        aid = _opt_value(e["cmd"], "--app-id")
        return {APPID_PREFIX + aid} if aid else names
    names |= {n.removesuffix("-bin") for n in names}           # firefox-bin -> firefox
    aliases = desktop_aliases()
    for n in list(names):
        names |= aliases.get(n, set())
    if e["category"] == CAT_BROWSER and browser_family(" ".join(names)) in (
            "chrome", "chromium"):
        names |= {"google-chrome", "chromium", "chromium-browser"}
    return names


def window_mode(e: dict, maximize: set | None, fullscreen: set | None) -> str | None:
    """MODE_FULL, MODE_MAX or None for the window of entry e; fullscreen wins.

    Args:
        e: Entry.
        maximize: Categories to maximize.
        fullscreen: Categories to show fullscreen.

    Returns:
        The window mode, or None.
    """
    if e["category"] in (fullscreen or set()):
        return MODE_FULL
    if e["category"] in (maximize or set()):
        return MODE_MAX
    return None


def _app_window_appears(e: dict, timeout: float) -> bool:
    """True if the window of web-app entry e is (or becomes within `timeout`) open."""
    aid = _opt_value(e["cmd"], "--app-id")
    if not aid:
        return False
    end = time.monotonic() + timeout
    while True:
        wins = list_windows()
        if wins is None:
            return False
        if any(aid in a for a, _t in wins):
            return True
        if time.monotonic() >= end:
            return False
        time.sleep(0.5)


def run_launches(entries: list, delay: float, dry_run: bool = False,
                 maximize: set | None = None, max_timeout: float = 20.0,
                 fullscreen: set | None = None) -> int:
    """Start the entries one after another, then maximize / fullscreen the windows.

    Args:
        entries: Entries to start.
        delay: Seconds between two starts.
        dry_run: Only print the commands.
        maximize: Categories whose windows are maximized.
        max_timeout: Seconds to wait for the windows.
        fullscreen: Categories whose windows are shown fullscreen.

    Returns:
        0 if every entry started, 1 otherwise.
    """
    if not dry_run and not (os.environ.get("WAYLAND_DISPLAY") or os.environ.get("DISPLAY")):
        print("Warnung: weder WAYLAND_DISPLAY noch DISPLAY gesetzt "
              "- keine grafische Sitzung erkennbar.")
        if not _ask_yes("Trotzdem starten?"):
            return 1
    modes = {id(e): window_mode(e, maximize, fullscreen) for e in entries}
    to_max = [e for e in entries if modes[id(e)]]
    maximizer = None
    if to_max and dry_run:
        n_full = sum(modes[id(e)] == MODE_FULL for e in to_max)
        print(f"DRY-RUN  danach (COSMIC): {len(to_max) - n_full} Fenster maximieren, "
              f"{n_full} im Vollbild")
    elif to_max:
        try:  # connect BEFORE starting, so every new window is seen
            maximizer = WindowMaximizer(
                need_maximize=any(modes[id(e)] == MODE_MAX for e in to_max))
        except (WaylandError, OSError) as exc:
            print(f"Hinweis: Fenster werden nicht maximiert - {exc}")
            log.info("maximize unavailable: %s", exc)
    ref = session_look_env()
    if ref:
        print(f"Darstellung (Theme, hell/dunkel) wie die Desktop-Sitzung: {ref[0]} (PID {ref[1]})")
    else:
        print("Darstellung: keine Desktop-Sitzung gefunden - wie beim Speichern")
    log.info("look environment from %s", f"{ref[0]} pid {ref[1]}" if ref else "snapshot")
    ok = fail = 0
    started_max = []
    restored_browser = False
    if not dry_run:
        busy(f"Starte {len(entries)} Programm(e) …")
    for i, e in enumerate(entries, 1):
        what = e["files"][0] if e["files"] else shlex.join(e["cmd"])
        if e.get("via"):
            what += f"  [mit {e['via']}]"
        if e.get("started"):
            what += f"  [{e['started']}]"
        if dry_run:
            print(f"{_prefix(i, len(entries), e)} DRY-RUN  {shlex.join(e['cmd'])}")
            continue
        if e.get("app") and restored_browser and _app_window_appears(e, 8.0):
            print(f"{_prefix(i, len(entries), e)} OK    {what}  [von der Browser-Sitzung "
                  "bereits geoeffnet]")
            log.info("app window already restored by the browser session: %s", e["program"])
            ok += 1
            continue
        good, msg = launch(e, ref)
        if good and "--restore-last-session" in e["cmd"]:
            restored_browser = True
        tail = "" if good else f"  -> {msg}"
        print(f"{_prefix(i, len(entries), e)} {'OK  ' if good else 'FEHL'}  {what}{tail}")
        log.info("launch %s: %s | %s", "ok" if good else "FAILED", shlex.join(e["cmd"]), msg)
        ok, fail = ok + good, fail + (not good)
        if good and e in to_max:
            started_max.append(e)
        if delay > 0 and i < len(entries):
            time.sleep(delay)
    if maximizer is not None:
        expected: dict = {}
        for e in started_max:
            key = "|".join(sorted(window_names(e)))
            expected.setdefault(key, []).append(modes[id(e)])
        if expected:
            busy(f"Maximiere / Vollbild: {len(started_max)} Fenster "
                 f"(bis {max_timeout:.0f} s)")
            res = maximizer.run(expected, max_timeout)
            for app_id, title, mode, done in res:
                state = MODE_LABEL[mode] if done else "angefordert, nicht bestaetigt"
                tag = "VOLL    " if mode == MODE_FULL else "MAX     "
                print(f"            {tag} {app_id}: {title} - {state}")
                log.info("%s %s %r: %s", "fullscreen" if mode == MODE_FULL else "maximize",
                         app_id, title, state)
            if len(res) < len(started_max):
                print(f"            Hinweis: {len(started_max) - len(res)} Fenster nicht "
                      "gefunden (z. B. mehrere Dateien in einem Fenster)")
        else:
            maximizer.close()
    if not dry_run:
        print(f"\nErgebnis: {ok} gestartet, {fail} fehlgeschlagen. Log: {LOG_FILE}")
    return 1 if fail else 0


# =========================================================================== busy overlay
# While session-snap starts, arranges or ends windows, a desktop notification stays on top
# of all windows (COSMIC: cosmic-notifications, capabilities "actions" and "persistence";
# expire_timeout 0 = stays until closed). Its button "Abbrechen" interrupts session-snap
# exactly like Ctrl+C in the terminal. Talks to org.freedesktop.Notifications with gdbus
# (GLib, part of every GTK desktop); without gdbus or a session bus nothing is shown.
NOTIFY = ("--dest", "org.freedesktop.Notifications",
          "--object-path", "/org/freedesktop/Notifications")
NOTIFY_IFACE = "org.freedesktop.Notifications"


class Overlay:
    """with Overlay("Wiederherstellen") as ov: ov.show("Starte 5 Programme ...")"""

    current: "Overlay | None" = None

    def __init__(self, title: str, enabled: bool = True):
        self.title, self.id = title, 0
        self.mon: subprocess.Popen | None = None
        self.thread: threading.Thread | None = None
        self.cancelled = False
        self.enabled = bool(enabled and os.environ.get("SESSION_SNAP_OVERLAY", "1") != "0"
                            and os.environ.get("DBUS_SESSION_BUS_ADDRESS")
                            and shutil.which("gdbus"))

    def _call(self, method: str, *args) -> str:
        r = subprocess.run(["gdbus", "call", "--session", *NOTIFY, "--method",
                            f"{NOTIFY_IFACE}.{method}", *args],
                           capture_output=True, text=True, timeout=5, check=False)
        if r.returncode:
            raise OSError(r.stderr.strip() or f"gdbus {method}: Fehler {r.returncode}")
        return r.stdout

    def _watch(self) -> None:
        """reads the D-Bus signals; the button sends ActionInvoked(id, 'cancel')"""
        if self.mon is None or self.mon.stdout is None:
            return
        for line in self.mon.stdout:
            if ("ActionInvoked" in line and f"(uint32 {self.id}," in line
                    and "'cancel'" in line):
                self.cancelled = True
                log.info("overlay: cancelled by the user")
                os.kill(os.getpid(), signal.SIGINT)        # -> KeyboardInterrupt
                return

    def show(self, text: str) -> None:
        """Show or update the notice (silently disabled on any error).

        Args:
            text: Text of the notice.
        """
        if not self.enabled:
            return
        try:
            if self.mon is None:
                # lives across show() calls, ended in close()
                self.mon = subprocess.Popen(  # pylint: disable=consider-using-with
                    ["gdbus", "monitor", "--session", *NOTIFY],
                    stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True)
                self.thread = threading.Thread(target=self._watch, daemon=True)
                self.thread.start()
            out = self._call("Notify", "session-snap", str(self.id), "system-run",
                             f"session-snap arbeitet: {self.title}",
                             f"{text}\nBitte nicht eingreifen.",
                             '["cancel", "Abbrechen"]',
                             '{"urgency": <byte 2>, "resident": <true>}', "0")
            m = re.search(r"uint32 (\d+)", out)
            if m:
                self.id = int(m.group(1))
        except (OSError, subprocess.SubprocessError) as exc:
            log.info("overlay unavailable: %s", exc)
            self.close()
            self.enabled = False

    def close(self) -> None:
        """Remove the notice and stop listening for the button."""
        if self.id:
            try:
                self._call("CloseNotification", str(self.id))
            except (OSError, subprocess.SubprocessError):
                pass
            self.id = 0
        if self.mon is not None:
            self.mon.terminate()
            try:
                self.mon.wait(timeout=2)
            except subprocess.TimeoutExpired:
                self.mon.kill()
                self.mon.wait()
            if self.mon.stdout is not None:
                self.mon.stdout.close()
            self.mon = None

    def __enter__(self):
        Overlay.current = self
        return self

    def __exit__(self, *exc):
        Overlay.current = None
        self.close()
        return False


def busy(text: str) -> None:
    """Update the busy overlay (if one is open) and print the step in the terminal.

    Args:
        text: Description of the current step.
    """
    print(f"… {text}", flush=True)
    if Overlay.current is not None:
        Overlay.current.show(text)


# =========================================================================== terminating
def protected_pids() -> set:
    """This process and all its ancestors (shell, terminal, session) - never terminated.

    Returns:
        Set of process IDs.
    """
    pids, pid = set(), os.getpid()
    while pid > 1 and pid not in pids:
        pids.add(pid)
        st = read_stat(pid)
        if st is None:
            break
        pid = st[1]
    return pids


def is_alive(pid: int, start: int) -> bool:
    """True if the PID still belongs to the same process (start time unchanged, not zombie).

    Args:
        pid: Process ID.
        start: Start time recorded with the PID.

    Returns:
        True while the same process runs.
    """
    st = read_stat(pid)
    return st is not None and st[0] != "Z" and st[2] == start


def kill_targets(cfg: dict, snap_entries: list,
                 current: list | None = None) -> tuple[list, list]:
    """Running processes matching the snapshot, merged to one target per PID.
    Returns (targets, protected) - protected targets are never signalled.
    t["relaunch"] lists app windows (e.g. Claude) that live in the target process; they are
    restarted after the process has ended, whether or not they were selected.

    Args:
        cfg: Configuration.
        snap_entries: Entries of the snapshot.
        current: Entries recorded right now; None captures them.

    Returns:
        (targets, protected targets).
    """
    wanted = {signature(e) for e in snap_entries}
    prot = protected_pids()
    current = build_entries(cfg) if current is None else current
    apps_of: dict = {}
    for e in current:
        if e.get("app"):
            apps_of.setdefault(e["pid"], []).append((e["program"], e["cmd"]))
    by_pid: dict = {}
    for e in current:
        if signature(e) not in wanted:
            continue
        t = by_pid.get(e["pid"])
        if t is None:
            t = by_pid[e["pid"]] = dict(e, files=list(e["files"]), entries=[e],
                                        relaunch=apps_of.get(e["pid"], []))
            if e.get("app"):  # name the target after the browser, not after the app window
                t["program"] = os.path.basename(e["cmd"][0])
                t["app"] = False
        else:
            t["files"] = _uniq(t["files"] + e["files"])
            t["entries"].append(e)
    targets, protected = [], []
    for pid, t in by_pid.items():
        if pid in prot:
            protected.append(dict(t, protect="eigene Prozesskette (Shell/Terminal)"))
        elif t.get("protect"):
            protected.append(t)
        else:
            targets.append(t)
    return targets, protected


def _signal(t: dict, sig: int) -> str:
    if not is_alive(t["pid"], t["start"]):
        return "bereits beendet"
    try:
        os.kill(t["pid"], sig)
    except ProcessLookupError:
        return "bereits beendet"
    except PermissionError as exc:
        return f"keine Berechtigung: {exc}"
    return "signalisiert"


# Image programs that edit files: unsaved work possible, so no SIGKILL without asking.
IMAGE_EDITORS = {"gimp", "gimp-2.10", "gimp-3.0", "krita", "inkscape", "darktable",
                 "rawtherapee", "digikam", "showfoto", "shotwell", "org.gimp.GIMP",
                 "org.kde.krita", "org.inkscape.Inkscape", "org.darktable.Darktable"}


def force_safe(t: dict) -> bool:
    """Players and pure image viewers hold no unsaved data: if they ignore SIGTERM they are
    ended with SIGKILL without asking. Browsers and other programs are asked about.

    Args:
        t: Kill target.

    Returns:
        True if SIGKILL may follow without asking.
    """
    if t["category"] == CAT_VLC:
        return True
    if t["category"] == CAT_IMG:
        names = {t["program"], os.path.basename(t["cmd"][0]) if t["cmd"] else "",
                 t.get("flatpak", "")}
        return not names & IMAGE_EDITORS
    return False


def terminate(targets: list, timeout: float, force: bool, dry_run: bool = False,
              confirm_force=None, relaunch: bool = True) -> int:
    """SIGTERM -> wait up to `timeout` -> SIGKILL only if `force` (or confirmed).
    Afterwards app windows of ended processes (e.g. Claude) are started again.

    Args:
        targets: Kill targets.
        timeout: Seconds to wait after SIGTERM.
        force: Send SIGKILL to survivors without asking.
        dry_run: Only print what would be signalled.
        confirm_force (Callable | None): Asks before SIGKILL; None never asks.
        relaunch: Start app windows of terminated browsers again.

    Returns:
        0 if all ended, 1 if some still run.
    """
    n = len(targets)
    for i, t in enumerate(targets, 1):
        what = f"{t['program']} (PID {t['pid']})"
        if dry_run:
            print(f"{_prefix(i, n, t)} DRY-RUN  SIGTERM -> {what}")
            for name, cmd in t.get("relaunch", []) if relaunch else []:
                print(f"            DRY-RUN  danach neu starten: {name}: {shlex.join(cmd)}")
            continue
        if i == 1:
            busy(f"Beende {n} Programm(e) …")
        res = _signal(t, signal.SIGTERM)
        print(f"{_prefix(i, n, t)} SIGTERM  {what}: {res}")
        log.info("kill SIGTERM pid=%d %s: %s", t["pid"], t["program"], res)
    if dry_run:
        return 0
    deadline = time.monotonic() + timeout
    alive = [t for t in targets if is_alive(t["pid"], t["start"])]
    if alive:
        busy(f"Warte bis zu {timeout:.0f} s auf {len(alive)} Programm(e) …")
    while alive and time.monotonic() < deadline:
        time.sleep(0.1)
        alive = [t for t in alive if is_alive(t["pid"], t["start"])]
    auto = [] if force else [t for t in alive if force_safe(t)]
    for t in auto:
        res = _signal(t, signal.SIGKILL)
        print(f"            SIGKILL  {t['program']} (PID {t['pid']}): {res} "
              "(Player/Bildbetrachter, keine ungespeicherten Daten)")
        log.info("kill SIGKILL (auto) pid=%d %s: %s", t["pid"], t["program"], res)
    if auto:
        time.sleep(0.5)
        alive = [t for t in alive if is_alive(t["pid"], t["start"])]
    rest = [t for t in alive if t not in auto]
    if rest and not force and confirm_force is not None:
        names = ", ".join(f"{t['program']} ({t['pid']})" for t in rest)
        force = confirm_force(f"{len(rest)} Prozess(e) reagieren nicht: {names}. "
                              "Mit SIGKILL hart beenden?")
    if rest and force:
        for t in rest:
            res = _signal(t, signal.SIGKILL)
            print(f"            SIGKILL  {t['program']} (PID {t['pid']}): {res}")
            log.info("kill SIGKILL pid=%d %s: %s", t["pid"], t["program"], res)
        time.sleep(0.5)
        alive = [t for t in alive if is_alive(t["pid"], t["start"])]
    restarted = _relaunch_apps([t for t in targets if t not in alive]) if relaunch else 0
    extra = f", {restarted} App-Fenster neu gestartet" if restarted else ""
    print(f"\nErgebnis: {n - len(alive)} beendet, {len(alive)} laufen weiter{extra}. "
          f"Log: {LOG_FILE}")
    return 1 if alive else 0


def _relaunch_apps(ended: list) -> int:
    """Start app windows (e.g. Claude) again whose browser process was just terminated."""
    count = 0
    for name, cmd in _uniq([(nm, tuple(c)) for t in ended for nm, c in t.get("relaunch", [])]):
        e = {"category": CAT_BROWSER, "program": name, "cmd": list(cmd), "files": [], "cwd": ""}
        good, msg = launch(e)
        print(f"            START    {name}: {'neu gestartet' if good else msg}")
        log.info("relaunch app %s: %s | %s", name, shlex.join(cmd), msg)
        count += good
    return count


# =========================================================================== window control
# Minimal Wayland client (pure Python, no library) for COSMIC's privileged protocols
# zcosmic_toplevel_info_v1 (version 1: toplevel, title, app_id, state, done events) and
# zcosmic_toplevel_manager_v1 (request set_maximized). cosmic-comp offers both globals to every
# client without a sandbox security context (src/state.rs: client_not_sandboxed). Protocols
# do not carry PIDs: windows are matched by app_id and by appearing after the launch.
WL_INFO, WL_MANAGER = "zcosmic_toplevel_info_v1", "zcosmic_toplevel_manager_v1"
STATE_MAXIMIZED, CAP_MAXIMIZE = 0, 3
# set_fullscreen(toplevel, output|null) = request 7; state 3 = fullscreen. cosmic-comp
# (commit 742b800b) executes the request but does not announce a capability for it.
STATE_FULLSCREEN = 3


class WaylandError(Exception):
    """The compositor is missing, refused a request or closed the connection."""


class _Wl:
    """Just enough of the Wayland wire protocol: registry, bind, sync, toplevel events."""

    def __init__(self, timeout: float = 5.0):
        disp = os.environ.get("WAYLAND_DISPLAY", "")
        if not disp:
            raise WaylandError("WAYLAND_DISPLAY nicht gesetzt (keine Wayland-Sitzung)")
        path = disp if disp.startswith("/") else os.path.join(
            os.environ.get("XDG_RUNTIME_DIR", f"/run/user/{os.getuid()}"), disp)
        self.sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM | socket.SOCK_CLOEXEC)
        self.sock.settimeout(timeout)
        try:
            self.sock.connect(path)
        except OSError as exc:
            self.sock.close()
            raise WaylandError(f"Verbindung zu {path} fehlgeschlagen: {exc}") from exc
        self.buf = b""
        self.next_id = 2
        self.types = {1: "wl_display"}
        self.globals: dict = {}          # interface -> (name, version)
        self.handles: dict = {}          # handle id -> {"app_id", "title", "states", "done"}
        self.caps: set = set()
        self.synced: set = set()
        # callback(handle id) after every complete state update
        self.on_done: Callable | None = None
        self.registry = self._new("wl_registry")
        self.send(1, 1, ("n", self.registry))           # wl_display.get_registry

    # -- encoding ------------------------------------------------------------------------
    def _new(self, typ: str) -> int:
        oid, self.next_id = self.next_id, self.next_id + 1
        self.types[oid] = typ
        return oid

    @staticmethod
    def _str(v: str) -> bytes:
        b = v.encode() + b"\0"
        return struct.pack("=I", len(b)) + b + b"\0" * (-len(b) % 4)

    def send(self, obj: int, opcode: int, *args) -> None:
        """Send one request: (kind, value) pairs, kind 's' string, else 32-bit word.

        Args:
            obj: Object id the request is sent to.
            opcode: Request number of the interface.
            *args: (kind, value) pairs of the arguments.
        """
        payload = b""
        for kind, v in args:
            payload += self._str(v) if kind == "s" else struct.pack("=I", v)
        self.sock.sendall(struct.pack("=II", obj, (8 + len(payload)) << 16 | opcode) + payload)

    def bind(self, interface: str, version: int) -> int:
        """Bind a global interface; returns the new object id.

        Args:
            interface: Interface name, e.g. "zcosmic_toplevel_info_v1".
            version: Highest version the client understands.

        Returns:
            The new object id.
        """
        name, have = self.globals[interface]
        oid = self._new(interface)
        self.send(self.registry, 0, ("u", name), ("s", interface), ("u", min(version, have)),
                  ("n", oid))
        return oid

    def roundtrip(self) -> None:
        """Wait until the compositor has handled every request sent so far."""
        cb = self._new("wl_callback")
        self.send(1, 0, ("n", cb))                         # wl_display.sync
        while cb not in self.synced:
            self.dispatch()

    # -- decoding ------------------------------------------------------------------------
    def _read_msg(self):
        while len(self.buf) < 8 or len(self.buf) < (
                struct.unpack_from("=I", self.buf, 4)[0] >> 16):
            data = self.sock.recv(65536)
            if not data:
                raise WaylandError("Compositor hat die Verbindung geschlossen")
            self.buf += data
        obj, word = struct.unpack_from("=II", self.buf)
        size, opcode = word >> 16, word & 0xFFFF
        payload, self.buf = self.buf[8:size], self.buf[size:]
        return obj, opcode, payload

    @staticmethod
    def _get_str(p: bytes, off: int):
        n = struct.unpack_from("=I", p, off)[0]
        s = p[off + 4:off + 4 + max(n - 1, 0)].decode("utf-8", "replace")
        return s, off + 4 + n + (-n % 4)

    def dispatch(self) -> None:
        """Read and handle one event.

        Raises:
            WaylandError: If the compositor reports a protocol error.
        """
        obj, op, p = self._read_msg()
        typ = self.types.get(obj, "")
        if typ == "wl_display" and op == 0:
            _oid, code = struct.unpack_from("=II", p)
            msg, _ = self._get_str(p, 8)
            raise WaylandError(f"Protokollfehler {code}: {msg}")
        if typ == "wl_registry" and op == 0:
            name = struct.unpack_from("=I", p)[0]
            iface, off = self._get_str(p, 4)
            self.globals[iface] = (name, struct.unpack_from("=I", p, off)[0])
        elif typ == "wl_callback" and op == 0:
            self.synced.add(obj)
        elif typ == WL_INFO and op == 0:                   # toplevel(new_id)
            hid = struct.unpack_from("=I", p)[0]
            self.types[hid] = "handle"
            self.handles[hid] = {"app_id": "", "title": "", "states": set(), "done": False,
                                 "closed": False}
        elif typ == WL_MANAGER and op == 0:                # capabilities(array)
            n = struct.unpack_from("=I", p)[0]
            self.caps = set(struct.unpack_from(f"={n // 4}I", p, 4))
        elif typ == "handle":
            h = self.handles.get(obj)
            if h is None:                      # handle already forgotten by the caller
                return
            if op == 0:
                h["closed"] = True
            elif op == 1:
                h["done"] = True
                if self.on_done:
                    self.on_done(obj)
            elif op in (2, 3):
                h["title" if op == 2 else "app_id"] = self._get_str(p, 0)[0]
            elif op == 8:
                n = struct.unpack_from("=I", p)[0]
                h["states"] = set(struct.unpack_from(f"={n // 4}I", p, 4))

    def wait(self, seconds: float) -> None:
        """Process events for up to `seconds` (returns early when nothing arrives).

        Args:
            seconds: Longest time to wait.
        """
        end = time.monotonic() + seconds
        while time.monotonic() < end:
            r, _, _ = select.select([self.sock], [], [], max(0.0, end - time.monotonic()))
            if not r and not self.buf:
                return
            self.dispatch()
            while len(self.buf) >= 8 and len(self.buf) >= (
                    struct.unpack_from("=I", self.buf, 4)[0] >> 16):
                self.dispatch()

    def close(self) -> None:
        """Close the connection."""
        self.sock.close()


def app_matches(app_id: str, names: set) -> bool:
    """True if a window app_id belongs to one of the program names (e.g. 'mpv',
    'vlc', 'org.gnome.Loupe' for 'loupe', 'nomacs' for 'org.nomacs.ImageLounge').

    Args:
        app_id: Application id of a window.
        names: Program names of an entry.

    Returns:
        True if the window belongs to the program.
    """
    a = app_id.lower().removesuffix(".desktop")
    a_parts = set(a.split(".")) - _ID_NOISE
    for n in names:
        if n.startswith(APPID_PREFIX):         # web-app window "<browser>-<app id>-<profile>"
            if n[len(APPID_PREFIX):].lower() in a:
                return True
            continue
        n = n.lower().removesuffix(".desktop")
        if not n:
            continue
        if a == n or n in a_parts or a in (set(n.split(".")) - _ID_NOISE):
            return True
    return False


# reverse-DNS components that say nothing about the program
_ID_NOISE = {"org", "com", "io", "net", "de", "github", "gitlab", "gnome", "kde", "app",
             "desktop", "freedesktop", "system76", ""}


MODE_MAX, MODE_FULL = "max", "full"
MODE_LABEL = {MODE_MAX: "maximiert", MODE_FULL: "Vollbild"}


class WindowMaximizer:
    """Connect BEFORE the programs are started; afterwards maximize or fullscreen every NEW
    window whose app_id belongs to the started programs (COSMIC only)."""

    def __init__(self, need_maximize: bool = True):
        self.wl = _Wl()
        self.wl.roundtrip()
        missing = [i for i in (WL_INFO, WL_MANAGER) if i not in self.wl.globals]
        if missing:
            self.wl.close()
            raise WaylandError("Compositor bietet " + ", ".join(missing) + " nicht an "
                               "(kein COSMIC oder Programm in einer Sandbox)")
        self.info = self.wl.bind(WL_INFO, 1)
        self.manager = self.wl.bind(WL_MANAGER, 1)
        self.wl.roundtrip()
        self.wl.roundtrip()
        # fullscreen is not announced as a capability (see STATE_FULLSCREEN), so only the
        # maximize capability can be checked
        if need_maximize and CAP_MAXIMIZE not in self.wl.caps:
            self.wl.close()
            raise WaylandError("Compositor erlaubt kein Maximieren (Capability fehlt)")
        self.old = set(self.wl.handles)

    def close(self) -> None:
        """Close the connection."""
        self.wl.close()

    def _request(self, hid: int, mode: str) -> None:
        """Ask the compositor to fullscreen or maximize window `hid` (once)."""
        if mode == MODE_FULL:
            if STATE_FULLSCREEN not in self.wl.handles[hid]["states"]:
                self.wl.send(self.manager, 7, ("u", hid), ("u", 0))  # set_fullscreen(, null)
        elif STATE_MAXIMIZED not in self.wl.handles[hid]["states"]:
            self.wl.send(self.manager, 3, ("u", hid))                 # set_maximized

    def run(self, expected: dict, timeout: float, settle: float = 6.0) -> list:
        """expected: '|'-joined program names -> number of windows (maximize) or list of
        modes (MODE_MAX / MODE_FULL, one per window). Waits until all expected windows are
        handled, `timeout` is reached, or no new window appeared for `settle` seconds.
        Returns [(app_id, title, confirmed)] for maximize-only calls with numbers, else
        [(app_id, title, mode, confirmed)].

        Args:
            expected: Program names (joined by "|") -> requested modes of their windows.
            timeout: Seconds to wait for the windows.
            settle: Seconds to wait for the confirmation of each request.

        Returns:
            List of (app_id, title, mode, confirmed).
        """
        legacy = all(isinstance(v, int) for v in expected.values())
        todo = {k: ([MODE_MAX] * v if isinstance(v, int) else list(v))
                for k, v in expected.items()}
        want = sum(len(v) for v in todo.values())
        done: dict = {}
        start = time.monotonic()
        last = start
        while len(done) < want:
            now = time.monotonic()
            if now - start > timeout or now - last > settle:
                break
            self.wl.wait(0.2)
            for hid, h in self.wl.handles.items():
                if hid in self.old or hid in done or not h["done"] or h["closed"]:
                    continue
                key = next((k for k, modes in todo.items()
                            if modes and app_matches(h["app_id"], set(k.split("|")))), None)
                if key is None:
                    continue
                mode = todo[key].pop(0)
                self._request(hid, mode)
                done[hid] = mode
                last = time.monotonic()
        self.wl.wait(0.5)
        self.wl.roundtrip()
        state = {MODE_MAX: STATE_MAXIMIZED, MODE_FULL: STATE_FULLSCREEN}
        result = []
        for hid, mode in done.items():
            h = self.wl.handles[hid]
            ok = state[mode] in h["states"]
            result.append((h["app_id"], h["title"], ok) if legacy
                          else (h["app_id"], h["title"], mode, ok))
        self.wl.close()
        return result


# =========================================================================== multi-window programs
# Single-instance programs (e.g. the GNOME image viewer eog) show several files in ONE process:
# only the first file is in its command line, and image files are closed after loading.
# The open files are recovered from the window titles (COSMIC toplevel info) and resolved to
# full paths via the directories the process watches (inotify), the GTK list of recently
# used files and the directories of the files already known.
_TITLE_SEP = re.compile(r"\s+[-–—|]\s+")
_TITLE_COUNTER = re.compile(r"\s*[\(\[]\d+\s*/\s*\d+[\)\]]")


# Seconds per attempt of the window list. Under heavy load (e.g. a video encode on all
# cores) the compositor may need longer than 2 s; a failed list made single-instance
# viewers look as if they showed only their first image (fixed in 2.0.0).
WINDOW_TIMEOUTS = (2.0, 6.0)


def _list_windows_once(timeout: float) -> list | None:
    """Query the window list once.

    Args:
        timeout: Seconds to wait for the compositor.

    Returns:
        [(app_id, title)] of all open windows, or None if not available.

    Raises:
        TimeoutError: If the compositor does not answer in time (retry makes sense).
    """
    try:
        wl = _Wl(timeout=timeout)
    except (WaylandError, OSError):
        return None
    try:
        wl.roundtrip()
        if WL_INFO not in wl.globals:
            return None
        wl.bind(WL_INFO, 1)
        wl.roundtrip()
        wl.roundtrip()
        return [(h["app_id"], h["title"]) for h in wl.handles.values() if not h["closed"]]
    except TimeoutError:
        raise
    except (WaylandError, OSError):
        return None
    finally:
        wl.close()


def list_windows() -> list | None:
    """Return all open windows (COSMIC), retrying with a longer timeout under load.

    Returns:
        [(app_id, title)] of all open windows, or None if not available (logged).
    """
    for timeout in WINDOW_TIMEOUTS:
        try:
            return _list_windows_once(timeout)
        except TimeoutError:
            log.warning("window list: no answer within %.0f s", timeout)
    log.warning("window list unavailable (compositor busy)")
    return None


def title_candidates(title: str) -> list:
    """File names a window title may contain: 'Foto 2.png', 'Foto 2.png - nomacs',
    '*Foto 2.png (2/5)', 'Foto 2.png (2/5) - Bildbetrachter' ... -> ['Foto 2.png', ...].
    The counter "(n/m)" is removed wherever it stands, before names with "/" are dropped.

    Args:
        title: Window title.

    Returns:
        Candidate file names, most likely first.
    """
    t = _TITLE_COUNTER.sub("", title.strip()).lstrip("*").strip()
    out = [t] + [x.strip() for x in _TITLE_SEP.split(t)]
    return [x for x in _uniq(out) if x and "/" not in x]


def recent_files() -> list:
    """[(path, {program names}, modified)] from ~/.local/share/recently-used.xbel (GTK),
    newest first. Program names come from the exec attribute ('eog %u' -> 'eog').

    Returns:
        List of (path, program names, modification time), newest first.
    """
    path = Path(os.environ.get("XDG_DATA_HOME") or Path.home() / ".local" / "share")
    ns = "{http://www.freedesktop.org/standards/desktop-bookmarks}"
    out: list = []
    try:
        root = ET.parse(path / "recently-used.xbel").getroot()  # nosec B314 - own file
    except (OSError, ET.ParseError):
        return out
    for bm in root.iter("bookmark"):
        href = bm.get("href", "")
        if not href.startswith("file://"):
            continue
        progs = set()
        for app in bm.iter(f"{ns}application"):
            try:   # GTK quotes the whole command line: exec="'eog %u'"
                toks = shlex.split(app.get("exec", ""))
            except ValueError:
                toks = []
            if len(toks) == 1:
                toks = toks[0].split()
            if toks:
                progs.add(os.path.basename(toks[0]))
        out.append((unquote(urlparse(href).path), progs, bm.get("modified", "")))
    out.sort(key=lambda r: r[2], reverse=True)
    return out


def inotify_dirs(pid: int) -> set:
    """(major, minor, inode) of all objects a process watches with inotify (fdinfo).

    Args:
        pid: Process ID.

    Returns:
        Set of (major, minor, inode) of the watched objects.
    """
    out: set = set()
    try:
        fds = os.listdir(f"/proc/{pid}/fdinfo")
    except OSError:
        return out
    for fd in fds:
        try:
            with open(f"/proc/{pid}/fdinfo/{fd}", encoding="ascii", errors="replace") as fh:
                for line in fh:
                    m = re.match(r"inotify wd:\S+ ino:([0-9a-f]+) sdev:([0-9a-f]+)", line)
                    if m:
                        ino, sdev = int(m.group(1), 16), int(m.group(2), 16)
                        out.add((sdev >> 20, sdev & 0xFFFFF, ino))   # kernel dev_t
        except OSError:
            continue
    return out


def _dir_key(d: str):
    try:
        st = os.stat(d)
    except OSError:
        return None
    return (os.major(st.st_dev), os.minor(st.st_dev), st.st_ino)


def resolve_title(title: str, program: str, known: list, cwd: str, watched: set,
                  recent: list) -> str | None:
    """Full path of the file a window title names, or None.

    Args:
        title: Window title.
        program: Name of the viewer.
        known: Files the process is known to show.
        cwd: Working directory of the process.
        watched: Objects the process watches with inotify.
        recent: Entries of the GTK list of recently used files.

    Returns:
        The full path, or None if it cannot be determined.
    """
    names = title_candidates(title)
    dirs = _uniq([os.path.dirname(f) for f in known] + [os.path.dirname(r[0]) for r in recent]
                 + ([cwd] if cwd else []))
    hot = [d for d in dirs if _dir_key(d) in watched]
    for n in names:
        for d in hot:
            if os.path.isfile(os.path.join(d, n)):
                return os.path.join(d, n)
    for n in names:
        mine = [r[0] for r in recent if os.path.basename(r[0]) == n and program in r[1]]
        other = [r[0] for r in recent if os.path.basename(r[0]) == n and program not in r[1]]
        for f in mine + other:
            if os.path.isfile(f):
                return f
        for d in _uniq([os.path.dirname(f) for f in known] + ([cwd] if cwd else [])):
            if os.path.isfile(os.path.join(d, n)):
                return os.path.join(d, n)
    return None


def add_window_files(entries: list, windows: list | None) -> list:
    """Complete image entries of single-instance viewers from their window titles.

    1. Every window title that names an already recorded image keeps that entry.
    2. Every remaining title of a viewer is resolved to a full path and added as a new entry
       of the OLDEST process of that viewer (a single-instance program hands further files
       to its first process).
    3. A recorded image whose program has windows, but none showing that image, is dropped
       (its window was closed with Ctrl+W while the process kept running).
    Without window information (no COSMIC) the entries stay as recorded.

    Args:
        entries: Entries recorded from /proc.
        windows: Open windows, or None if unknown.

    Returns:
        The completed entries.
    """
    if not windows:
        return entries
    groups: dict = {}
    for e in entries:
        if e["category"] == CAT_IMG:
            groups.setdefault(e["pid"], []).append(e)
    if not groups:
        return entries
    names = {pid: set().union(*(window_names(e) for e in g)) for pid, g in groups.items()}
    order = sorted(groups, key=lambda pid: (groups[pid][0].get("start", 0), pid))
    has_win = {pid: False for pid in groups}
    keep, rest = set(), []
    for app, title in windows:
        pids = [pid for pid in order if app_matches(app, names[pid])]
        if not pids:
            continue
        for pid in pids:
            has_win[pid] = True
        cands = title_candidates(title)
        hit = [id(e) for pid in pids for e in groups[pid]
               if e["files"] and os.path.basename(e["files"][0]) in cands]
        if hit:
            keep.update(hit)
        else:
            rest.append((pids[0], title))
    added: dict = {}
    seen = {e["files"][0] for g in groups.values() for e in g if e["files"]}
    recent = recent_files() if rest else []
    for pid, title in rest:
        tmpl = groups[pid][0]
        files = [f for e in groups[pid] for f in e["files"]]
        path = resolve_title(title, os.path.basename(tmpl["cmd"][0]), files,
                             tmpl.get("cwd", ""), inotify_dirs(pid), recent)
        if path is None or path in seen:
            continue
        seen.add(path)
        if tmpl["files"]:
            cmd = [path if x == tmpl["files"][0] else x for x in tmpl["cmd"]]
        else:
            cmd = list(tmpl["cmd"]) + [path]
        added.setdefault(pid, []).append(
            dict(tmpl, files=[path], cmd=cmd, found_via=f"Fenstertitel '{title}'"))
    out = []
    for e in entries:
        if e["category"] != CAT_IMG:
            out.append(e)
            continue
        pid = e["pid"]
        if not has_win[pid] or id(e) in keep:
            out.append(e)
        if e is groups[pid][-1]:
            new = added.pop(pid, [])
            if not new and has_win[pid] and not any(id(x) in keep for x in groups[pid]):
                out += [x for x in groups[pid] if id(x) not in keep]   # titles unusable
            out += new
    return out


# =========================================================================== window order
# The Alt-Tab switcher of COSMIC (pop-launcher plugin "cosmic_toplevel") keeps its own list:
# new windows are appended, a window that becomes active moves to the end ("most recently
# used"). The compositor does not publish that list - it only reports windows in creation
# order and which one is active. Therefore:
#   * "session-snap watch" (started at login) follows every activation and writes the
#     current order to session-snap.d/order.json;
#   * save: order from the watcher if it runs, otherwise creation order + active window;
#   * restore: activating the windows one after another (oldest first) rebuilds the order.
STATE_ACTIVATED = 2
CAP_ACTIVATE = 2
WATCH_STALE = 5.0         # order.json older than this while the watcher is dead: not used


def _order_file() -> Path:
    return STATE_DIR / "order.json"


def _lock_file() -> Path:
    return STATE_DIR / "watch.lock"


def watcher_alive(lock: Path | None = None) -> bool:
    """Tell whether a 'session-snap watch' process holds its lock.

    Args:
        lock: Lock file to test; None means the one of the current layout.

    Returns:
        True if the lock is held by a running watcher.
    """
    try:
        fd = os.open(lock or _lock_file(), os.O_RDONLY | os.O_CREAT, 0o644)
    except OSError:
        return False
    try:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        return True                 # locked by the watcher
    else:
        fcntl.flock(fd, fcntl.LOCK_UN)
        return False
    finally:
        os.close(fd)


def _ensure_wayland_env() -> None:
    """Autostart / systemd may start us without WAYLAND_DISPLAY: use the first socket."""
    if os.environ.get("WAYLAND_DISPLAY"):
        return
    rt = os.environ.get("XDG_RUNTIME_DIR", f"/run/user/{os.getuid()}")
    socks = sorted(p.name for p in Path(rt).glob("wayland-*") if not p.name.endswith(".lock"))
    if socks:
        os.environ["WAYLAND_DISPLAY"] = socks[0]


def _write_order(mru: list, handles: dict) -> None:
    data = {"pid": os.getpid(), "updated": time.time(),
            "windows": [{"app_id": handles[h]["app_id"], "title": handles[h]["title"]}
                        for h in mru if h in handles]}
    tmp = _order_file().with_name("order.json.tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")
    os.replace(tmp, _order_file())


def watch_once(wl: "_Wl", run_for: float | None = None) -> None:
    """Follow activations on an open connection; write order.json on every change.
    run_for: seconds (tests), None = until the connection ends.

    Args:
        wl: Open connection to the compositor.
        run_for: Seconds to run (tests); None runs until the connection ends.

    Raises:
        WaylandError: If the compositor offers no toplevel information (not COSMIC).
    """
    wl.roundtrip()
    if WL_INFO not in wl.globals:
        raise WaylandError("zcosmic_toplevel_info_v1 nicht verfuegbar (kein COSMIC)")
    wl.bind(WL_INFO, 1)
    wl.roundtrip()
    wl.roundtrip()
    mru = [h for h, v in wl.handles.items() if not v["closed"]]      # creation order
    active = [h for h in mru if STATE_ACTIVATED in wl.handles[h]["states"]]
    for h in active:
        mru.remove(h)
        mru.append(h)
    for h in mru:
        wl.handles[h]["was_active"] = h in active
    snap: list = [None]

    def update(h=None):
        """called for every 'done' event: the state at exactly that moment counts, so that
        fast Alt-Tab switching is not lost

        Args:
            h (int | None): Handle that changed, None after the initial burst.
        """
        if h is not None:
            v = wl.handles.get(h)
            if v is not None and not v["closed"]:
                if h not in mru:
                    mru.append(h)
                active = STATE_ACTIVATED in v["states"]
                if active and not v.get("was_active"):      # became active -> most recent
                    mru.remove(h)
                    mru.append(h)
                v["was_active"] = active
        for k in [k for k in mru if k not in wl.handles or wl.handles[k]["closed"]]:
            mru.remove(k)
        state = [(k, wl.handles[k]["app_id"], wl.handles[k]["title"]) for k in mru]
        if state != snap[0]:
            _write_order(mru, wl.handles)
            snap[0] = state

    wl.on_done = update
    update()
    end = None if run_for is None else time.monotonic() + run_for
    while end is None or time.monotonic() < end:
        wl.wait(0.5 if end is None else min(0.5, max(0.0, end - time.monotonic())))
        for k in [k for k, v in wl.handles.items() if v["closed"]]:
            del wl.handles[k]
        update()
        check_reload()   # safe point: the order of this event is stored


class WatchRestart(SelfRestart):
    """Self-restart of the background process ``watch`` (dev-conventions chapter 13).

    ``watch`` takes no options, so the new process gets exactly ``python -m session_snap
    watch``; the template's ``--foreground``/``-y``/``--continue-log`` do not apply.
    """

    def command(self) -> list[str]:
        """Build the command line of the new process.

        Returns:
            Argument vector for :func:`os.execv`.
        """
        return [self.python, "-m", self.package, "watch"]


def _restart_request() -> Path:
    return STATE_DIR / "watch.restart"


def _restart_answer() -> Path:
    return STATE_DIR / "watch.answer"


def check_reload(restarter: SelfRestart | None = None) -> None:
    """Handle a pending reload request of ``session-snap reload`` (called at a safe point).

    The installed release is probed first; a release that does not start or has the
    running version is rejected and the watcher continues. Otherwise the answer
    "accepted" is written and the process image is replaced (same PID).

    Args:
        restarter: Restart helper (tests inject a fake); None builds a WatchRestart.
    """
    req = _restart_request()
    if not req.exists():
        return
    try:
        req.unlink()
    except OSError:
        return
    restarter = restarter or WatchRestart([], "")
    version = restarter.installed_version()
    if version is None:
        answer = f"rejected: installierte Version startet nicht ({restarter.last_error})"
    elif version == VERSION:
        answer = f"rejected: Version {VERSION} laeuft bereits"
    else:
        answer = f"accepted: {VERSION} -> {version} (PID {os.getpid()})"
    _restart_answer().write_text(answer + "\n", encoding="utf-8")
    log.info("watch reload: %s", answer)
    if answer.startswith("accepted"):
        logging.shutdown()
        try:
            restarter.execute()
        except OSError as exc:
            log.error("watch reload failed: %s", exc)
            _restart_answer().write_text(f"rejected: Start fehlgeschlagen ({exc})\n",
                                         encoding="utf-8")


def cmd_reload(timeout: float = 10.0) -> int:
    """Command 'reload': ask a running ``watch`` to switch to the installed release.

    Args:
        timeout: Seconds to wait for the answer of the watcher.

    Returns:
        0 if accepted or nothing runs, 1 if rejected or unanswered.
    """
    if not watcher_alive():
        print("Kein laufender Mitschreiber (watch) - nichts neu zu laden.")
        return 0
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    _restart_answer().unlink(missing_ok=True)
    _restart_request().write_text(f"{time.time()}\n", encoding="utf-8")
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        if _restart_answer().exists():
            answer = _restart_answer().read_text(encoding="utf-8").strip()
            print(f"watch: {answer}")
            return 0 if answer.startswith("accepted") else 1
        time.sleep(0.1)
    print(f"watch hat in {timeout:.0f} s nicht geantwortet (Anfrage bleibt bestehen).")
    return 1


def cmd_watch(run_for: float | None = None) -> int:
    """Background process: record the Alt-Tab order (started by the autostart entry).

    Args:
        run_for: Seconds to run (tests); None runs until terminated.

    Returns:
        Exit code.
    """
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    fd = os.open(_lock_file(), os.O_RDWR | os.O_CREAT, 0o644)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        os.close(fd)
        print("session-snap watch laeuft bereits.")
        return 0
    os.ftruncate(fd, 0)
    os.write(fd, f"{os.getpid()}\n".encode())
    _ensure_wayland_env()
    log.info("watch started (pid %d)", os.getpid())
    end = None if run_for is None else time.monotonic() + run_for
    try:
        while end is None or time.monotonic() < end:
            try:
                wl = _Wl(timeout=5.0)
            except (WaylandError, OSError) as exc:
                log.info("watch: no compositor (%s), retry in 5 s", exc)
                time.sleep(5 if end is None else 0.2)
                continue
            try:
                watch_once(wl, None if end is None else max(0.0, end - time.monotonic()))
            except (WaylandError, OSError, struct.error) as exc:
                log.info("watch: connection lost (%s), reconnecting", exc)
                time.sleep(2 if end is None else 0.1)
            finally:
                wl.close()
    finally:
        os.close(fd)
    return 0


def cmd_watch_cli(stop: bool, status: bool, start: bool) -> int:
    """Command 'watch' with --stop, --status or --start.

    Args:
        stop: End the running watcher.
        status: Show whether it runs.
        start: Create the autostart entry and start it in the background.

    Returns:
        Exit code.
    """
    if stop:
        print("Mitschreiber beendet." if stop_watcher() else "Mitschreiber lief nicht.")
        return 0
    if status:
        alive = watcher_alive()
        state = f"laeuft (PID {watch_pid()})" if alive else "laeuft NICHT"
        print(f"Mitschreiber: {state}"
              f"; Autostart: {'ja' if autostart_file().exists() else 'nein'}")
        if alive and _order_file().exists():
            age = time.time() - _order_file().stat().st_mtime
            print(f"Reihenfolge zuletzt geschrieben vor {age:.0f} s: {_order_file()}")
        return 0
    if start:
        for line in install_watcher(start=True):
            print(line)
        return 0
    return cmd_watch()


def watch_pid() -> int | None:
    """PID written by the running background process (None if none).

    Returns:
        The process ID, or None.
    """
    try:
        return int(_lock_file().read_text().strip() or 0) or None
    except (OSError, ValueError):
        return None


def autostart_file() -> Path:
    """Autostart entry of the background process.

    Returns:
        Path of the desktop file.
    """
    return Path.home() / ".config" / "autostart" / "session-snap-watch.desktop"


def install_watcher(start: bool = True) -> list:
    """Autostart entry (the session starts it at login) and start it now.

    Args:
        start: Also (re)start the watcher now.

    Returns:
        Lines for the report.
    """
    out = []
    f = autostart_file()
    text = ("[Desktop Entry]\nType=Application\nName=session-snap watch\n"
            "Comment=Records the Alt-Tab window order for session-snap\n"
            f"Exec={PROGRAM} watch\nNoDisplay=true\nX-GNOME-Autostart-enabled=true\n")
    f.parent.mkdir(parents=True, exist_ok=True)
    if not f.exists() or f.read_text(encoding="utf-8") != text:
        f.write_text(text, encoding="utf-8")
        out.append(f"{f}: Autostart-Eintrag angelegt")
    else:
        out.append(f"{f}: Autostart-Eintrag vorhanden - unveraendert")
    if start:
        if restart_watcher():
            out.append("session-snap watch: gestartet")
        else:
            still = " (ein bereits laufender Mitschreiber laeuft unveraendert weiter)" \
                if watcher_alive() else ""
            out.append(f"session-snap watch: NICHT gestartet - {watcher_start_problem()}{still}")
    return out


def stop_watcher(timeout: float = 3.0) -> bool:
    """End the background process; True if it ran and has ended.

    Args:
        timeout: Seconds to wait for the end.

    Returns:
        True if a watcher ran and has ended.
    """
    pid = watch_pid()
    if not pid or not watcher_alive():
        return False
    try:
        os.kill(pid, signal.SIGTERM)
    except OSError:
        return False
    end = time.monotonic() + timeout
    while watcher_alive() and time.monotonic() < end:
        time.sleep(0.05)
    return True


def watcher_start_problem() -> str:
    """Why the watcher cannot be started now ('' = it can).

    Returns:
        The reason, or "" if it can be started.
    """
    if not PROGRAM.is_file():
        return f"Programm {PROGRAM} nicht installiert"
    if not (os.environ.get("WAYLAND_DISPLAY") or os.environ.get("DISPLAY")):
        return ("keine grafische Sitzung (WAYLAND_DISPLAY/DISPLAY fehlt); "
                "er startet bei der naechsten Anmeldung ueber den Autostart")
    return ""


def restart_watcher() -> bool:
    """(Re)start the watcher detached from this terminal - with the installed program.
    Returns True if it was started, False if not (see watcher_start_problem()); in that
    case a running watcher is left alone.

    Returns:
        True if it was started.
    """
    if watcher_start_problem():
        return False
    stop_watcher()
    spawn_detached([str(PROGRAM), "watch"], str(Path.home()))
    return True


def _match_windows(entries: list, windows: list) -> dict:
    """{index of entry: index of window}. Files are matched by their name in the window
    title, programs without files by their app_id. Every window is used once."""
    used, out = set(), {}
    for pass_files in (True, False):
        for i, e in enumerate(entries):
            if i in out or bool(e.get("files")) != pass_files:
                continue
            names = window_names(e)
            for j, (app, title) in enumerate(windows):
                if j in used or not app_matches(app, names):
                    continue
                if pass_files and os.path.basename(e["files"][0]) not in title_candidates(title):
                    continue
                out[i] = j
                used.add(j)
                break
    return out


def current_order(windows: list | None) -> tuple[list, str]:
    """Windows in Alt-Tab order (oldest first, most recent last) and the source.

    Args:
        windows: Open windows, or None if unknown.

    Returns:
        (windows in order, source "watch", "creation" or "").
    """
    if windows is None:
        return [], ""
    if watcher_alive():
        try:
            data = json.loads(_order_file().read_text(encoding="utf-8"))
            mru = [(w["app_id"], w["title"]) for w in data.get("windows", [])]
        except (OSError, ValueError, KeyError, TypeError):
            mru = None
        if mru is not None:
            rest = list(windows)
            ordered = []
            for w in mru:
                if w in rest:
                    rest.remove(w)
                    ordered.append(w)
            return rest + ordered, "watch"          # never-activated windows: oldest
    return list(windows), "creation"


def assign_order(entries: list, windows: list | None = None) -> str:
    """Store the Alt-Tab position (0 = oldest) in every entry that has a window.

    Args:
        entries: Entries to annotate (changed in place).
        windows: Open windows; None queries them.

    Returns:
        Source of the order, or "" if no window list is available.
    """
    if windows is None:
        windows = list_windows_states()
    if not windows:
        return ""
    plain = [(a, t) for a, t, _act in windows]
    if not watcher_alive():          # fallback: creation order, active window last
        act = [w for w in windows if w[2]]
        plain = [(a, t) for a, t, x in windows if not x] + [(a, t) for a, t, _x in act]
    ordered, source = current_order(plain)
    for i, j in _match_windows(entries, ordered).items():
        entries[i]["order"] = j
    return source


def list_windows_states() -> list | None:
    """[(app_id, title, activated)] in creation order (COSMIC), or None.

    Returns:
        List of (app_id, title, activated), or None.
    """
    try:
        wl = _Wl(timeout=2.0)
    except (WaylandError, OSError):
        return None
    try:
        wl.roundtrip()
        if WL_INFO not in wl.globals:
            return None
        wl.bind(WL_INFO, 1)
        wl.roundtrip()
        wl.roundtrip()
        return [(h["app_id"], h["title"], STATE_ACTIVATED in h["states"])
                for h in wl.handles.values() if not h["closed"]]
    except (WaylandError, OSError):
        return None
    finally:
        wl.close()


def reorder_windows(entries: list, timeout: float = 20.0, settle: float = 6.0,
                    pause: float = 0.15) -> list:
    """Activate the windows of all entries with a recorded position, oldest first, so that
    the Alt-Tab order matches the snapshot. Windows not in the snapshot stay behind.
    Waits until all windows exist (or timeout / no new window for `settle` seconds).
    Returns the activated (app_id, title) in order.

    Args:
        entries: Entries with recorded positions.
        timeout: Seconds to wait for the windows.
        settle: Seconds to wait after the last window appeared.
        pause: Seconds between two activations.

    Returns:
        The entries whose windows were activated.

    Raises:
        WaylandError: If the compositor does not offer activation.
    """
    want = [e for e in entries if "order" in e]
    if not want:
        return []
    wl = _Wl()
    try:
        wl.roundtrip()
        missing = [i for i in (WL_INFO, WL_MANAGER, "wl_seat") if i not in wl.globals]
        if missing:
            raise WaylandError("Compositor bietet " + ", ".join(missing) + " nicht an")
        wl.bind(WL_INFO, 1)
        manager = wl.bind(WL_MANAGER, 1)
        seat = wl.bind("wl_seat", 1)
        wl.roundtrip()
        wl.roundtrip()
        if CAP_ACTIVATE not in wl.caps:
            raise WaylandError("Compositor erlaubt kein Aktivieren (Capability fehlt)")
        start = last = time.monotonic()
        found, seen = {}, 0
        while True:
            live = [(h, v) for h, v in wl.handles.items() if not v["closed"] and v["done"]]
            wins = [(v["app_id"], v["title"]) for _h, v in live]
            m = _match_windows(want, wins)
            found = {i: live[j][0] for i, j in m.items()}
            if len(live) != seen:
                seen, last = len(live), time.monotonic()
            now = time.monotonic()
            if len(found) == len(want) or now - start > timeout or now - last > settle:
                break
            wl.wait(0.3)
        done = []
        for i in sorted(found, key=lambda k: want[k]["order"]):
            h = found[i]
            wl.send(manager, 2, ("u", h), ("u", seat))          # activate(toplevel, seat)
            wl.roundtrip()
            time.sleep(pause)
            done.append((wl.handles[h]["app_id"], wl.handles[h]["title"]))
        return done
    finally:
        wl.close()


# =========================================================================== layout migration
OLD_PATH_BLOCK = "# >>> session-snap: ~/scripts in PATH >>>"


def backup_file(src: Path, stem: str, version: str | None, ext: str = ".py",
                move: bool = False) -> Path | None:
    """Copy (or move) src into backup/ as '<stem> v<version><ext>'. An identical copy that
    exists already is kept (returns None); a different file with that name gets a time
    stamp appended. Nothing is ever overwritten.

    Args:
        src: File to back up.
        stem: Base name in the backup folder.
        version: Version for the name, or None.
        ext: Extension of the backup.
        move: Move instead of copy.

    Returns:
        Path of the backup, or None if an identical copy existed.
    """
    BACKUP_DIR.mkdir(parents=True, exist_ok=True)
    name = f"{stem} v{version}" if version else stem
    target = BACKUP_DIR / f"{name}{ext}"
    if target.exists() and _same(src, target):
        if move:
            src.unlink()
        return None
    stamp = datetime.fromtimestamp(src.stat().st_mtime if not version else time.time())
    n = 0
    while target.exists():
        suffix = stamp.strftime("%Y-%m-%d_%H-%M") + (f"-{n}" if n else "")
        target = BACKUP_DIR / f"{name} {suffix}{ext}"
        n += 1
    if move:
        shutil.move(str(src), str(target))
    else:
        shutil.copy2(src, target)
    return target


def _same(a, b) -> bool:
    try:
        return Path(a).read_bytes() == Path(b).read_bytes()
    except OSError:
        return False


def _move_safe(src: Path, dest: Path) -> Path | None:
    """Move src to dest without overwriting: identical -> src removed; different -> time
    stamp before the extension."""
    dest.parent.mkdir(parents=True, exist_ok=True)
    if dest.exists():
        if _same(src, dest):
            src.unlink()
            return None
        stamp = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
        dest = dest.with_name(f"{dest.stem} {stamp}{dest.suffix}")
    shutil.move(str(src), str(dest))
    return dest


def migrate_layout() -> list:
    """Move the files of the layout up to 1.8.0 into the XDG layout (rule "moving old files").

    ``~/scripts/session-snap.d/config.ini`` and ``programs.ini`` go to the configuration
    directory, ``snapshots/``, ``logs/``, ``backup/``, ``order.json`` and ``watch.lock`` to
    the state directory, ``docs/`` to the data directory. Nothing is overwritten (an identical
    target removes the source, a different one gets a time stamp), every move is reported, and
    folders that became empty are removed. The old program files in ``~/scripts`` are left to
    install.sh, which removes only what it can prove an older session-snap created.

    Returns:
        One line per action; empty if there was nothing to move.
    """
    done: list = []
    if not OLD_DIR.is_dir() or OLD_DIR.is_symlink() or OLD_DIR.resolve() == STATE_DIR.resolve():
        return done
    old_lock = OLD_DIR / "watch.lock"
    if old_lock.is_file() and watcher_alive(old_lock):
        done.append(stop_old_watcher(old_lock))
    targets = {"config.ini": CONFIG_DIR, "programs.ini": CONFIG_DIR, "docs": DATA_DIR}
    for f in sorted(OLD_DIR.rglob("*")):
        if not f.is_file() or f.is_symlink():
            continue
        rel = f.relative_to(OLD_DIR)
        dest = targets.get(rel.parts[0], STATE_DIR) / rel
        if rel.parts[0] == "watch.lock":
            f.unlink()
            continue
        r = _move_safe(f, dest)
        done.append(f"{f} -> {r or dest}" + (" (identisch, Quelle entfernt)" if r is None else ""))
    for d in sorted((x for x in OLD_DIR.rglob("*") if x.is_dir()),
                    key=lambda x: len(x.parts), reverse=True):
        try:
            d.rmdir()
        except OSError:
            pass
    try:
        OLD_DIR.rmdir()
        done.append(f"{OLD_DIR}/ leer - entfernt")
    except OSError:
        done.append(f"{OLD_DIR}/ nicht leer - bleibt (bitte ansehen)")
    return done


def stop_old_watcher(lock: Path, timeout: float = 3.0) -> str:
    """End the watcher of the layout up to 1.8.0 before its lock file moves.

    Only a process whose command line is a ``session-snap ... watch`` is signalled.

    Args:
        lock: Its lock file (holds the PID).
        timeout: Seconds to wait for the end.

    Returns:
        A line for the migration report.
    """
    try:
        pid = int(lock.read_text(encoding="ascii").strip() or 0)
        argv = Path(f"/proc/{pid}/cmdline").read_bytes().split(b"\0")
    except (OSError, ValueError):
        return f"alter Mitschreiber: PID in {lock} nicht lesbar - bitte abmelden/anmelden"
    if not any(b"session-snap" in a for a in argv) or b"watch" not in argv:
        return f"alter Mitschreiber: PID {pid} ist kein session-snap watch - nicht beendet"
    os.kill(pid, signal.SIGTERM)
    end = time.monotonic() + timeout
    while watcher_alive(lock) and time.monotonic() < end:
        time.sleep(0.05)
    return f"alter Mitschreiber (PID {pid}) beendet"


def old_path_block_note() -> str | None:
    """Hint on the PATH block the installer up to 1.8.0 wrote into ~/.profile / ~/.bashrc.

    The block is not removed: ``~/scripts`` stays the place of small unversioned scripts,
    which may rely on it being in PATH.

    Returns:
        A note naming the files with the block, or None if no file has it.
    """
    files = [rc for rc in (Path.home() / ".profile", Path.home() / ".bashrc")
             if rc.is_file() and OLD_PATH_BLOCK in rc.read_text(encoding="utf-8",
                                                                errors="replace")]
    if not files:
        return None
    return ("Hinweis: " + ", ".join(str(f) for f in files) + " enthaelt noch den Block, der "
            "~/scripts in den PATH nimmt (von session-snap bis 1.8.0). Er bleibt, weil andere "
            "Skripte in ~/scripts ihn brauchen koennen; session-snap selbst liegt jetzt in "
            f"{PROGRAM}.")


# =========================================================================== curses UI
_ESC_SEQ = {
    "[A": curses.KEY_UP, "[B": curses.KEY_DOWN, "OA": curses.KEY_UP, "OB": curses.KEY_DOWN,
    "[5~": curses.KEY_PPAGE, "[6~": curses.KEY_NPAGE, "[H": curses.KEY_HOME,
    "[F": curses.KEY_END, "OH": curses.KEY_HOME, "OF": curses.KEY_END,
    "[1~": curses.KEY_HOME, "[4~": curses.KEY_END,
    "[C": curses.KEY_RIGHT, "[D": curses.KEY_LEFT, "OC": curses.KEY_RIGHT,
    "OD": curses.KEY_LEFT,
}


def _getkey(scr) -> int:
    """getch() with fallback decoding of raw ESC sequences (terminals ignoring keypad
    mode). A lone Esc stays 27 (= abort); an unknown sequence is ignored, never aborts."""
    k = scr.getch()
    if k != 27:
        return k
    scr.nodelay(True)
    seq = ""
    try:
        for _ in range(4):
            c = scr.getch()
            if c == -1:
                break
            seq += chr(c)
            if seq in _ESC_SEQ:
                return _ESC_SEQ[seq]
    finally:
        scr.nodelay(False)
    return 27 if not seq else -1


def _ui_setup(scr) -> None:
    try:
        curses.curs_set(0)
    except curses.error:
        pass
    scr.keypad(True)
    if curses.has_colors():
        curses.start_color()
        try:
            curses.use_default_colors()
            bg = -1
        except curses.error:
            bg = curses.COLOR_BLACK
        curses.init_pair(1, curses.COLOR_CYAN, bg)    # category headers
        curses.init_pair(2, curses.COLOR_RED, bg)     # missing file
        curses.init_pair(3, curses.COLOR_GREEN, bg)   # selected entry


def _cp(n: int) -> int:
    return curses.color_pair(n) if curses.has_colors() else 0


def _put(scr, y, x, text, attr=0) -> None:
    h, w = scr.getmaxyx()
    if 0 <= y < h and 0 <= x < w - 1:
        try:
            scr.addnstr(y, x, text, w - x - 1, attr)
        except curses.error:
            pass


def _title(scr, title, w) -> None:
    text = f" session-snap {VERSION} ─ {title} ".ljust(w)
    _put(scr, 0, 0, text, curses.A_REVERSE | curses.A_BOLD)


def menu(title: str, options: list, info: list | None = None) -> int | None:
    """Single selection. Returns the option index or None (q/Esc).

    Args:
        title: Title line.
        options: Lines to choose from.
        info: Lines shown above the options.

    Returns:
        Index of the chosen option, or None.
    """
    def ui(scr):
        """Run the menu on the screen.

        Args:
            scr (curses.window): Screen.

        Returns:
            int | None: Index of the chosen option, or None.
        """
        _ui_setup(scr)
        cur = 0
        while True:
            scr.erase()
            h, w = scr.getmaxyx()
            _title(scr, title, w)
            y = 2
            for line in info or []:
                for part in textwrap.wrap(line, max(10, w - 4)) or [""]:
                    _put(scr, y, 2, part)
                    y += 1
            if info:
                y += 1
            top = max(0, cur - (h - y - 3))
            for i, opt in enumerate(options[top:], start=top):
                if y >= h - 2:
                    break
                arrow = "▶" if i == cur else " "
                attr = curses.A_REVERSE if i == cur else 0
                _put(scr, y, 2, f"{arrow} {i + 1:>2}. {opt}", attr)
                y += 1
            _put(scr, h - 1, 0, " ↑↓/j k bewegen   Enter waehlen   1-9 direkt   "
                 "q/Esc zurueck", curses.A_DIM)
            k = _getkey(scr)
            if k in (curses.KEY_UP, ord("k")):
                cur = (cur - 1) % len(options)
            elif k in (curses.KEY_DOWN, ord("j")):
                cur = (cur + 1) % len(options)
            elif k in (curses.KEY_HOME, ord("g")):
                cur = 0
            elif k in (curses.KEY_END, ord("G")):
                cur = len(options) - 1
            elif k in (10, 13, curses.KEY_ENTER):
                return cur
            elif ord("1") <= k <= ord("9") and k - ord("1") < len(options):
                return k - ord("1")
            elif k in (ord("q"), 27):
                return None
    return curses.wrapper(ui)


def _render_row(row, w, is_cur, entries, sel, running):
    """Lines (text, attr) for one checklist row; long paths are wrapped, never cut."""
    if row[0] == "hdr":
        n_sel = sum(sel[i] for i, e in enumerate(entries) if e["category"] == row[1])
        text = f"── {CAT_LABEL[row[1]]} ({n_sel}/{row[2]}) ".ljust(w - 2, "─")
        return [(text, _cp(1) | curses.A_BOLD)]
    i = row[1]
    e = entries[i]
    base = curses.A_REVERSE if is_cur else 0
    head = f"{'[x]' if sel[i] else '[ ]'} {e['program']}  (PID {e.get('pid', '?')})"
    if signature(e) in running:
        head += "  [laeuft bereits]"
    lines = [(head, base | (_cp(3) if sel[i] and not is_cur else 0) | curses.A_BOLD)]
    details = []
    for f in e["files"]:
        ok = os.path.exists(f)
        details.append(("Datei:  " + f + ("" if ok else "  [FEHLT]"), 0 if ok else _cp(2)))
    if e["category"] == CAT_REST or not e["files"]:
        details.append(("Befehl: " + shlex.join(e["cmd"]), 0))
    for text, attr in details:
        parts = textwrap.wrap(text, max(10, w - 8), subsequent_indent=" " * 8,
                              break_long_words=True, break_on_hyphens=False)
        for part in parts or [""]:
            lines.append(("      " + part, base | attr))
    return lines


def checklist(title: str, entries: list, preselect: list,
              running: set | None = None, foldable: bool = False) -> list | None:
    """Multi selection with [x] boxes. Returns the selected entries or None (abort).
    foldable=True: categories can be folded (start folded) and selected as a whole,
    see checklist_folding().

    Args:
        title: Title line.
        entries: Entries to choose from.
        preselect: Initial state of every box.
        running: Marks entries that run already.
        foldable: Show the tree of checklist_folding() instead of a flat list.

    Returns:
        The selected entries, or None.
    """
    if not entries:
        return []
    if foldable:
        return checklist_folding(title, entries, preselect, running)
    running = running or set()
    rows: list[tuple] = []  # ("hdr", category, count) | ("item", index)
    for cat in CAT_ORDER:
        idx = [i for i, e in enumerate(entries) if e["category"] == cat]
        if idx:
            rows.append(("hdr", cat, len(idx)))
            rows += [("item", i) for i in idx]
    item_rows = [r for r, row in enumerate(rows) if row[0] == "item"]
    sel = list(preselect)

    def ui(scr):
        """Run the checklist on the screen.

        Args:
            scr (curses.window): Screen.

        Returns:
            list | None: The selected entries, or None.
        """
        _ui_setup(scr)
        cur, top = 0, 0
        while True:
            scr.erase()
            h, w = scr.getmaxyx()
            _title(scr, title, w)
            body_top, body_h = 2, h - 4
            if body_h < 4 or w < 40:
                _put(scr, 2, 0, "Terminal zu klein (mind. 40x8).")
                if _getkey(scr) in (ord("q"), 27):
                    return None
                continue
            cr = item_rows[cur]
            rendered = [_render_row(r, w, i == cr, entries, sel, running)
                        for i, r in enumerate(rows)]
            heights = [len(x) for x in rendered]
            top = min(top, cr)
            if (0 < cr == top and rows[cr - 1][0] == "hdr"
                    and heights[cr - 1] + heights[cr] <= body_h):
                top = cr - 1  # keep the category header visible
            while top < cr and sum(heights[top:cr + 1]) > body_h:
                top += 1
            y = body_top
            for r in range(top, len(rows)):
                for text, attr in rendered[r]:
                    if y >= body_top + body_h:
                        break
                    _put(scr, y, 1, text.ljust(w - 2) if attr & curses.A_REVERSE else text,
                         attr)
                    y += 1
            if top > 0:
                _put(scr, 1, w - 12, " ▲ mehr ", curses.A_DIM)
            if y >= body_top + body_h and top + 1 < len(rows):
                _put(scr, h - 2, w - 12, " ▼ mehr ", curses.A_DIM)
            count = f" {sum(sel)}/{len(sel)} gewaehlt │ "
            helps = ("↑↓ j k  PgUp/PgDn  Leertaste=an/aus  a=alle n=keine "
                     "i=invertieren c=Kategorie  Enter=OK  q/Esc=Abbruch",
                     "Leer=an/aus a=alle n=keine i=inv c=Kat. Enter=OK q=Abbr.")
            help_ = helps[0] if len(count) + len(helps[0]) < w else helps[1]
            _put(scr, h - 1, 0, count + help_, curses.A_DIM)
            k = _getkey(scr)
            last = len(item_rows) - 1
            if k in (curses.KEY_UP, ord("k")):
                cur = max(0, cur - 1)
            elif k in (curses.KEY_DOWN, ord("j")):
                cur = min(last, cur + 1)
            elif k == curses.KEY_PPAGE:
                cur = max(0, cur - max(1, body_h // 3))
            elif k == curses.KEY_NPAGE:
                cur = min(last, cur + max(1, body_h // 3))
            elif k in (curses.KEY_HOME, ord("g")):
                cur = 0
            elif k in (curses.KEY_END, ord("G")):
                cur = last
            elif k == ord(" "):
                i = rows[cr][1]
                sel[i] = not sel[i]
                cur = min(last, cur + 1)
            elif k == ord("a"):
                sel[:] = [True] * len(sel)
            elif k == ord("n"):
                sel[:] = [False] * len(sel)
            elif k == ord("i"):
                sel[:] = [not s for s in sel]
            elif k == ord("c"):
                cat = entries[rows[cr][1]]["category"]
                idx = [i for i, e in enumerate(entries) if e["category"] == cat]
                new = not all(sel[i] for i in idx)
                for i in idx:
                    sel[i] = new
            elif k in (10, 13, curses.KEY_ENTER):
                return [e for i, e in enumerate(entries) if sel[i]]
            elif k in (ord("q"), 27):
                return None
    return curses.wrapper(ui)


def _fold_header(cat, count, entries, sel, is_cur, expanded):
    """One line for a category header of the folding checklist (cat None = root 'Alle')."""
    idx = [i for i, e in enumerate(entries) if cat is None or e["category"] == cat]
    n_sel = sum(sel[i] for i in idx)
    box = "[x]" if n_sel == len(idx) else ("[-]" if n_sel else "[ ]")
    names: dict = {}
    for i in idx:
        names[entries[i]["program"]] = names.get(entries[i]["program"], 0) + 1
    summary = ", ".join(f"{n} ×{c}" if c > 1 else n for n, c in names.items())
    arrow = "▾" if expanded else "▸"
    label = "Alle Programme" if cat is None else CAT_LABEL[cat]
    text = f"{arrow} {box} {label} ({n_sel}/{count} gewaehlt)"
    if cat is not None and not expanded and summary:
        text += f"  – {summary}"
    attr = _cp(1) | curses.A_BOLD
    if is_cur:
        attr = curses.A_REVERSE | curses.A_BOLD
    return [(text, attr)]


FOLD_LEGEND = (
    ("↑↓ bewegen   → aufklappen   ← zuklappen   Leertaste an/aus "
     "(auf Kategorie oder \"Alle\": ganze Gruppe)   Enter OK   q/Esc Abbruch",
     "↑↓ bewegen  → auf  ← zu  Leertaste an/aus  Enter OK  q Abbruch"),
    ("Kurztasten:  a alle an   n alle ab   i Auswahl umkehren   "
     "z alle Kategorien auf/zu",
     "a alle an  n alle ab  i umkehren  z alle auf/zu"),
)


def checklist_folding(title: str, entries: list, preselect: list,
                      running: set | None = None) -> list | None:
    """Checklist as a tree: root "Alle Programme" -> categories -> programs. Categories
    start folded (overview first); a single category starts unfolded.
    Up / Down move, Right unfolds, Left folds (on the root: all categories), Space selects /
    deselects (on a category or the root: the whole group). Short keys: a all on, n all
    off, i invert, z unfold / fold all. Enter = OK, q / Esc = abort. A two-line legend at
    the bottom explains the keys.

    Args:
        title: Title line.
        entries: Entries to choose from.
        preselect: Initial state of every box.
        running: Marks entries that run already.

    Returns:
        The selected entries, or None.
    """
    running = running or set()
    cats = [c for c in CAT_ORDER if any(e["category"] == c for e in entries)]
    count = {c: sum(e["category"] == c for e in entries) for c in cats}
    expanded = set(cats) if len(cats) == 1 else set()
    sel = list(preselect)
    root: tuple = ("root",)

    def visible():
        """List the rows that are shown now.

        Returns:
            list: Root, category headers and the items of open categories.
        """
        out = [root]
        for c in cats:
            out.append(("hdr", c, count[c]))
            if c in expanded:
                out += [("item", i) for i, e in enumerate(entries) if e["category"] == c]
        return out

    def group(row):
        """entry indices covered by a row

        Args:
            row (tuple): Root, header or item row.

        Returns:
            list: Indices of the entries.
        """
        if row == root:
            return list(range(len(entries)))
        if row[0] == "hdr":
            return [i for i, e in enumerate(entries) if e["category"] == row[1]]
        return [row[1]]

    def toggle_all():
        if expanded:
            expanded.clear()
        else:
            expanded.update(cats)

    def ui(scr):
        """Run the tree checklist on the screen.

        Args:
            scr (curses.window): Screen.

        Returns:
            list | None: The selected entries, or None.
        """
        _ui_setup(scr)
        cur_row, top = root, 0
        while True:
            rows = visible()
            cur = rows.index(cur_row) if cur_row in rows else 0
            cur_row = rows[cur]
            scr.erase()
            h, w = scr.getmaxyx()
            _title(scr, title, w)
            body_top, body_h = 2, h - 5             # two legend lines at the bottom
            if body_h < 4 or w < 40:
                _put(scr, 2, 0, "Terminal zu klein (mind. 40x9).")
                if _getkey(scr) in (ord("q"), 27):
                    return None
                continue
            rendered = []
            for j, r in enumerate(rows):
                if r == root:
                    rendered.append(_fold_header(None, len(entries), entries, sel, j == cur,
                                                 len(expanded) == len(cats)))
                elif r[0] == "hdr":
                    rendered.append(_fold_header(r[1], r[2], entries, sel, j == cur,
                                                 r[1] in expanded))
                else:    # entries are drawn 4 columns further right: wrap 4 narrower
                    rendered.append(_render_row(r, w - 4, j == cur, entries, sel, running))
            heights = [len(x) for x in rendered]
            top = min(top, cur)
            while top < cur and sum(heights[top:cur + 1]) > body_h:
                top += 1
            y = body_top
            for r in range(top, len(rows)):
                x = 1 if rows[r] == root else (3 if rows[r][0] == "hdr" else 5)
                for text, attr in rendered[r]:
                    if y >= body_top + body_h:
                        break
                    _put(scr, y, x, text.ljust(w - x - 1), attr)   # full width: no rests
                    y += 1
            if top > 0:
                _put(scr, 1, w - 12, " ▲ mehr ", curses.A_DIM)
            if y >= body_top + body_h and top + 1 < len(rows):
                _put(scr, h - 3, w - 12, " ▼ mehr ", curses.A_DIM)
            counter = f" {sum(sel)}/{len(sel)} gewaehlt │ "
            for n, (long_, short) in enumerate(FOLD_LEGEND):
                lead = counter if n == 0 else " " * len(counter)
                text = long_ if len(lead) + len(long_) < w else short
                _put(scr, h - 2 + n, 0, lead + text, curses.A_DIM)
            k = _getkey(scr)
            last = len(rows) - 1
            if k == curses.KEY_UP:
                cur_row = rows[max(0, cur - 1)]
            elif k == curses.KEY_DOWN:
                cur_row = rows[min(last, cur + 1)]
            elif k == curses.KEY_PPAGE:
                cur_row = rows[max(0, cur - max(1, body_h // 3))]
            elif k == curses.KEY_NPAGE:
                cur_row = rows[min(last, cur + max(1, body_h // 3))]
            elif k == curses.KEY_HOME:
                cur_row = rows[0]
            elif k == curses.KEY_END:
                cur_row = rows[last]
            elif k == curses.KEY_RIGHT:
                if cur_row == root:
                    expanded.update(cats)
                else:
                    expanded.add(cur_row[1] if cur_row[0] == "hdr"
                                 else entries[cur_row[1]]["category"])
            elif k == curses.KEY_LEFT:
                if cur_row == root:
                    expanded.clear()
                else:
                    cat = (cur_row[1] if cur_row[0] == "hdr"
                           else entries[cur_row[1]]["category"])
                    expanded.discard(cat)
                    cur_row = ("hdr", cat, count[cat])
            elif k == ord(" "):
                idx = group(cur_row)
                new = not all(sel[i] for i in idx)
                for i in idx:
                    sel[i] = new
                if cur_row[0] == "item":
                    cur_row = rows[min(last, cur + 1)]
            elif k == ord("a"):
                sel[:] = [True] * len(sel)
            elif k == ord("n"):
                sel[:] = [False] * len(sel)
            elif k == ord("i"):
                sel[:] = [not s for s in sel]
            elif k == ord("z"):
                toggle_all()
                if cur_row[0] == "item":
                    cat = entries[cur_row[1]]["category"]
                    cur_row = ("hdr", cat, count[cat])
            elif k in (10, 13, curses.KEY_ENTER):
                return [e for i, e in enumerate(entries) if sel[i]]
            elif k in (ord("q"), 27):
                return None
    return curses.wrapper(ui)


def scope_count(n: int, total: int | None) -> str:
    """'(5)' or, with total (restore): '(5 von 16 – 11 laufen bereits)'.

    Args:
        n: Entries offered.
        total: All entries of the snapshot, or None.

    Returns:
        The count text.
    """
    if total is None:
        return f"({n})"
    running = total - n
    return f"({n} von {total}" + (f" – {running} laufen bereits)" if running else ")")


def choose_scope(title: str, verb: str, items: list, info: list,
                 extra: str | None = None, total: list | None = None):
    """Menu: all / checklist / one category / [extra] / cancel.
    total (restore): all snapshot entries - the counts then show "n von m" and how many
    of them are already running.
    Returns a list of entries, "checklist", "extra" or None (cancel).

    Args:
        title: Title of the menu.
        verb: Verb of the first option ("starten", "beenden").
        items: Entries offered.
        info: Lines shown above the options.
        extra: Optional additional option.
        total: All snapshot entries (restore), or None.

    Returns:
        list | str | None: Entries, "checklist", "extra" or None.
    """
    by = {c: [e for e in items if e["category"] == c] for c in CAT_ORDER}
    tot = None if total is None else {c: sum(e["category"] == c for e in total)
                                      for c in CAT_ORDER}
    opts = [f"Alle {verb} {scope_count(len(items), None if total is None else len(total))}",
            "Auswahl treffen … (Checkliste)"]
    opts += [f"Nur {CAT_SHORT[c]} {scope_count(len(by[c]), None if tot is None else tot[c])}"
             for c in CAT_ORDER]
    if extra:
        opts.append(extra)
    opts.append("Abbrechen")
    idx = menu(title, opts, info)
    log.info("%s: menu %s -> %s", title, " | ".join(opts),
             "Abbruch" if idx is None else opts[idx])
    if idx is None or idx == len(opts) - 1:
        return None
    if idx == 0:
        return items
    if idx == 1:
        return "checklist"
    if extra and idx == len(opts) - 2:
        return "extra"
    return by[CAT_ORDER[idx - 2]]


def choose_programs(entries: list) -> None:
    """Interactive choice of the program for videos / images; saved to programs.ini.

    Args:
        entries: Entries of the snapshot (decide which MIME types matter).
    """
    apps = desktop_apps()
    all_apps = desktop_apps(include_hidden=True)
    while True:
        progs = load_programs()
        opts = [f"{label}: {program_label(progs.get(s), all_apps)}"
                for s, label in SLOT_LABEL.items()] + ["Zurueck"]
        idx = menu("Programme waehlen", opts,
                   info=["Gilt fuer das Oeffnen beim Wiederherstellen. Gespeichert in:",
                         str(PROGRAMS_FILE)])
        if idx is None or idx == len(opts) - 1:
            return
        slot = list(SLOT_LABEL)[idx]
        cat = next(c for c, s in PROGRAM_SLOTS.items() if s == slot)
        files = [f for e in entries if e["category"] == cat for f in e["files"]]
        mimes = [mime_of(f) for f in files] or SLOT_FALLBACK_MIMES[slot]
        handlers = handlers_for(mimes, apps)
        choices = ["Wie aufgezeichnet (Originalprogramm)"]
        choices += [f"{app['name']}  ({app['id']}, oeffnet {n}/{len(mimes)})"
                    for app, n in handlers]
        info = [f"{SLOT_LABEL[slot]}: {len(files)} Datei(en) im Snapshot, "
                f"{len(handlers)} passende Programme installiert"]
        pick = menu(f"Programm fuer {SLOT_LABEL[slot]}", choices, info)
        if pick is None:
            continue
        if pick == 0:
            progs.pop(slot, None)
        else:
            progs[slot] = handlers[pick - 1][0]["id"]
        save_programs(progs)


# =========================================================================== commands
def pick_snapshot(spec: str | None) -> Path | None:
    """Snapshot chosen by `spec` ('latest', name, number) or in a menu.

    Args:
        spec: "latest", file name, path, or None for the menu.

    Returns:
        Path of the snapshot, or None.
    """
    snaps = list_snapshots()
    if not snaps:
        print(f"Keine Snapshots vorhanden in {SNAP_DIR}")
        return None
    if spec:
        if spec == "latest":
            return snaps[0]
        p = Path(spec)
        name = spec if spec.endswith(".json") else spec + ".json"
        cand = p if p.is_file() else SNAP_DIR / name
        if cand.is_file():
            return cand
        print(f"Snapshot nicht gefunden: {spec}")
        return None
    if len(snaps) == 1:
        return snaps[0]
    idx = menu("Snapshot waehlen (neuester oben)", [snapshot_title(s) for s in snaps],
               info=[f"{len(snaps)} Snapshots in {SNAP_DIR}"])
    return None if idx is None else snaps[idx]


def _load_or_report(path: Path) -> dict | None:
    try:
        return load_snapshot(path)
    except (OSError, json.JSONDecodeError) as exc:
        print(f"Snapshot unlesbar: {path}: {exc}")
        return None


def _snap_info(path: Path, data: dict) -> str:
    label = f"   «{data['label']}»" if data.get("label") else ""
    return f"Snapshot: {path.name}{label}"


def cmd_save(cfg, label: str | None, interactive: bool, kill_after: bool = False,
             force: bool = False, relaunch: bool = True) -> int:
    """Command 'save': record the programs and write a snapshot.

    Args:
        cfg (dict): Configuration.
        label: Descriptive name; None asks in interactive mode.
        interactive: Show the checklist and ask.
        kill_after: Terminate the saved programs afterwards.
        force: With kill_after, SIGKILL survivors.
        relaunch: Start app windows of terminated browsers again.

    Returns:
        Exit code.
    """
    entries = build_entries(cfg)
    print(f"Erfasst: {len(entries)} Eintraege  [{_counts(entries)}]")
    if not entries:
        print("Nichts zu speichern.")
        return 0
    if CAPTURE_WARNING:
        print(CAPTURE_WARNING)
        if interactive and not _ask_yes("Trotzdem speichern?"):
            print("Nicht gespeichert - spaeter erneut versuchen.")
            return 1
    if interactive:
        title = "Aufnahme – abwaehlen, was NICHT gespeichert werden soll"
        chosen = checklist(title, entries, [True] * len(entries))
        if chosen is None:
            print("Abgebrochen, nichts gespeichert.")
            return 1
        if not chosen:
            print("Keine Auswahl, nichts gespeichert.")
            return 1
        entries = chosen
        if label is None:
            label = input("Optionale Bezeichnung (Enter = keine): ").strip()
    annotate_browser_sessions(entries)
    source = assign_order(entries)
    if source:
        n = sum(1 for e in entries if "order" in e)
        print(f"Alt-Tab-Reihenfolge: {n} Fenster - "
              + ("vom Mitschreiber (session-snap watch)" if source == "watch" else
                 "Mitschreiber laeuft nicht: Reihenfolge des Oeffnens, aktives Fenster vorne"))
    path = save_snapshot(entries, label or "", source)
    print(f"Gespeichert: {path}  ({len(entries)} Eintraege)")
    if not kill_after:
        return 0
    targets, protected = kill_targets(cfg, entries)
    _report_protected(protected)
    if not targets:
        print("Keine laufenden Programme zu beenden.")
        return 0
    if interactive and not _ask_yes(f"{len(targets)} gespeicherte(s) Programm(e) jetzt beenden?"):
        print("Nicht beendet.")
        return 0
    confirm = _ask_yes if interactive else None
    with Overlay("Beenden", cfg["overlay"]):
        return terminate(targets, cfg["kill_timeout"], force, confirm_force=confirm,
                         relaunch=relaunch)


def _order_label(data: dict) -> str:
    return {"watch": "vom Mitschreiber", "creation": "Reihenfolge des Oeffnens"}.get(
        data.get("order_source", ""), "unbekannt")


def choose_order(data: dict, mode: str | None, interactive: bool) -> str:
    """'saved' or 'keep'. Asks in the terminal unless given by --order.

    Args:
        data: Snapshot data.
        mode: "saved", "keep" or None to decide here.
        interactive: Ask in the terminal.

    Returns:
        "saved" or "keep".
    """
    if not any("order" in e for e in data.get("entries", [])):
        return "keep"
    if mode:
        return mode
    if not interactive:
        return "saved"
    idx = menu("Alt-Tab-Reihenfolge", [
        f"Wie beim Speichern ({_order_label(data)})",
        "Nicht aendern (neu gestartete Fenster vorne)"],
        ["In welcher Reihenfolge sollen die Fenster im Fensterwechsler (Alt-Tab) stehen?",
         "Fenster, die nicht im Snapshot stehen, bleiben dahinter."])
    return "keep" if idx == 1 else "saved"


def _apply_order(entries: list, timeout: float, dry_run: bool) -> None:
    n = sum(1 for e in entries if "order" in e)
    if dry_run:
        print(f"DRY-RUN  danach Alt-Tab-Reihenfolge wie beim Speichern: {n} Fenster")
        return
    busy(f"Alt-Tab-Reihenfolge: {n} Fenster ordnen")
    try:
        done = reorder_windows(entries, timeout)
    except (WaylandError, OSError) as exc:
        print(f"Hinweis: Reihenfolge nicht hergestellt - {exc}")
        log.info("reorder unavailable: %s", exc)
        return
    print(f"Alt-Tab-Reihenfolge wie beim Speichern: {len(done)} von {n} Fenstern geordnet")
    log.info("reordered %d of %d windows", len(done), n)


def _window_modes_info(cfg, enabled: bool = True) -> str:
    """'Vollbild: Bilder; maximiert: Videos' (fullscreen wins over maximize)."""
    full = cfg["fullscreen"] if enabled else set()
    mx = (cfg["maximize"] - full) if enabled else set()

    def names(cats):
        """Name the categories for the info line.

        Args:
            cats (set): Category keys.

        Returns:
            str: Comma-separated names, or "nichts".
        """
        return ", ".join(CAT_SHORT[c] for c in CAT_ORDER if c in cats) or "nichts"
    return f"Vollbild: {names(full)}; maximiert: {names(mx)}"


def running_signatures(cfg) -> set:
    """Signatures of the entries that run now. A browser counts only if it has an open
    window: Chrome keeps running in the background without any window ("continue running
    background apps"), restoring must then still open it. Without a window list (no COSMIC)
    every process counts.

    Args:
        cfg (dict): Configuration.

    Returns:
        Signatures of the running entries.
    """
    windows = list_windows()
    out = set()
    for e in build_entries(cfg, windows=windows):
        if (e["category"] == CAT_BROWSER and windows is not None
                and not any(app_matches(a, window_names(e)) for a, _t in windows)):
            continue
        out.add(signature(e))
    return out


def cmd_restore(cfg, spec: str | None, start_all: bool, dry_run: bool,
                overrides: dict | None = None, player_opts: bool = True,
                maximize: bool = True, order: str | None = None) -> int:
    """Command 'restore': start the programs of a snapshot that do not run.

    Args:
        cfg (dict): Configuration.
        spec: Snapshot ("latest", name, path) or None for the menu.
        start_all: Start everything without menus.
        dry_run: Only print the commands.
        overrides: Program choice for this run only.
        player_opts: Insert the configured player options.
        maximize: Maximize / fullscreen windows as configured.
        order: "saved", "keep" or None to ask.

    Returns:
        Exit code.
    """
    path = pick_snapshot(spec)
    data = _load_or_report(path) if path else None
    if path is None or data is None:
        return 1
    entries = data.get("entries", [])
    if not entries:
        print("Snapshot ist leer.")
        return 0
    running = running_signatures(cfg)
    pending = [e for e in entries if signature(e) not in running]
    chosen: Any
    if start_all:
        chosen = pending
    else:
        while True:
            progs = dict(load_programs(), **(overrides or {}))
            labels = ", ".join(f"{label} -> {program_label(progs.get(s))}"
                               for s, label in SLOT_LABEL.items())
            info = [_snap_info(path, data), f"Inhalt: {_counts(entries)}",
                    f"Bereits laufend (werden standardmaessig nicht erneut gestartet): "
                    f"{len(entries) - len(pending)}",
                    f"Programme: {labels}",
                    "Videos starten " + ("pausiert (--play: sofort abspielen)"
                                         if player_opts else "sofort (--play)")
                    + "; " + _window_modes_info(cfg, maximize)]
            chosen = choose_scope("Wiederherstellen", "starten", pending, info,
                                  extra="Programme fuer Videos/Bilder waehlen …",
                                  total=entries)
            if chosen != "extra":
                break
            choose_programs(entries)
        if chosen == "checklist":
            pre = [signature(e) not in running for e in entries]
            chosen = checklist(f"Starten – {path.name}", entries, pre, running,
                               foldable=True)
        if chosen is None:
            print("Abgebrochen.")
            return 1
    ask = sys.stdin.isatty() and not start_all and not order
    mode = choose_order(data, order or (None if ask else cfg["order"]), ask)
    if not chosen:
        print("Nichts zu starten.")
        if mode == "saved":
            _apply_order(entries, cfg["maximize_timeout"], dry_run)
        return 0
    progs = dict(load_programs(), **(overrides or {}))
    chosen, warnings = apply_programs(chosen, progs)
    if player_opts:
        chosen = apply_player_options(chosen, cfg["player_options"])
    if cfg["browser_session"]:
        chosen = prepare_browser_sessions(chosen, dry_run)
    if maximize and CAT_IMG in cfg["fullscreen"]:   # e.g. eog --fullscreen (own mode)
        chosen = apply_player_options(chosen, cfg["player_options"], {CAT_IMG})
    for w in warnings:
        print(f"Warnung: {w}")
    if mode == "saved":   # start in the recorded order, too (helps even without COSMIC)
        chosen = sorted(chosen, key=lambda e: e.get("order", -1))
    # a browser restoring its session must START first - an app window started before it
    # would start the browser process, which then ignores --restore-last-session
    chosen = ([e for e in chosen if "--restore-last-session" in e["cmd"]]
              + [e for e in chosen if "--restore-last-session" not in e["cmd"]])
    log.info("restore from %s: %d entries%s", path, len(chosen), " (dry-run)" if dry_run else "")
    with Overlay("Wiederherstellen", cfg["overlay"] and not dry_run):
        rc = run_launches(chosen, cfg["delay"], dry_run,
                          cfg["maximize"] if maximize else set(), cfg["maximize_timeout"],
                          cfg["fullscreen"] if maximize else set())
        if mode == "saved":
            sigs = {signature(c) for c in chosen}
            _apply_order(chosen + [e for e in entries if signature(e) not in sigs],
                         cfg["maximize_timeout"], dry_run)
    return rc


def _report_protected(protected: list) -> None:
    for t in protected:
        print(f"Geschuetzt, wird nicht beendet: {t['program']} (PID {t['pid']}) "
              f"- {t.get('protect') or 'geschuetzt'}")


def collateral(targets: list, current: list) -> list:
    """Describe what terminating the targets closes besides the chosen entries.

    A process ends as a whole: an image viewer with one process for all windows closes every
    image, a browser that shows an image closes all its windows.

    Args:
        targets: Chosen kill targets (with their ``entries``).
        current: All entries recorded right now.

    Returns:
        One line per target that would close further windows or files.
    """
    out = []
    for t in targets:
        mine = {signature(e) for e in t.get("entries", [])}
        others = [e for e in current if e["pid"] == t["pid"] and signature(e) not in mine]
        if others:
            names = ", ".join(_uniq(os.path.basename(e["files"][0]) if e["files"]
                                    else e["program"] for e in others)[:3])
            more = f" (+{len(others) - 3})" if len(others) > 3 else ""
            out.append(f"{t['program']} (PID {t['pid']}) schliesst auch {len(others)} "
                       f"weitere(s): {names}{more}")
    return out


def browser_targets(targets: list, chosen_cats: set) -> list:
    """Return the targets that are browsers although no browser category was chosen.

    Args:
        targets: Chosen kill targets.
        chosen_cats: Categories the user selected.

    Returns:
        The browser targets that need an explicit confirmation.
    """
    if CAT_BROWSER in chosen_cats:
        return []
    return [t for t in targets
            if any(e["category"] == CAT_BROWSER for e in t.get("entries", []))
            or browser_family(" ".join([t["program"]] + t["cmd"][:1])) in BROWSER_FAMILIES]


def _live_choice(extra: list, cats: set) -> int | None:
    """Ask what to do with open entries the snapshot does not know.

    Args:
        extra: Open entries of the chosen categories that are not in the snapshot.
        cats: The chosen categories.

    Returns:
        0 = save a new snapshot, then terminate all; 1 = only the snapshot's entries;
        None = cancel.
    """
    what = " und ".join(CAT_SHORT[c] for c in CAT_ORDER if c in cats)
    idx = menu("Beenden – mehr offen als im Snapshot",
               [f"Erst neuen Snapshot speichern, dann alle offenen {what} beenden",
                "Nur die Eintraege aus dem Snapshot beenden", "Abbrechen"],
               [f"Offen, aber nicht im Snapshot: {len(extra)}  [{_counts(extra)}]"])
    log.info("kill: %d open entries not in the snapshot -> %s", len(extra), idx)
    return None if idx in (None, 2) else idx


def cmd_kill(cfg: dict, spec: str | None, kill_all: bool, force: bool, timeout: float | None,
             dry_run: bool, relaunch: bool = True) -> int:
    """Command 'kill': terminate the recorded programs that still run.

    The selection is compared with what is open now: if more entries of the chosen
    categories are open than the snapshot holds, the user decides whether to save a new
    snapshot first and terminate all of them. Before anything is signalled, windows that
    would close as a side effect are shown, and browsers outside the chosen categories need
    an explicit confirmation.

    Args:
        cfg: Configuration.
        spec: Snapshot ('latest', name, number) or None for the menu.
        kill_all: Terminate everything without menus (``--all``).
        force: SIGKILL after the timeout without asking.
        timeout: Seconds to wait after SIGTERM; None uses the configuration.
        dry_run: Only show what would be signalled.
        relaunch: Start app windows of a terminated browser again.

    Returns:
        Exit code.
    """
    path = pick_snapshot(spec)
    data = _load_or_report(path) if path else None
    if path is None or data is None:
        return 1
    snap_entries = data.get("entries", [])
    current = build_entries(cfg)
    targets, protected = kill_targets(cfg, snap_entries, current)
    _report_protected(protected)
    if not targets:
        print("Keine laufenden Programme aus diesem Snapshot gefunden.")
        if CAPTURE_WARNING:
            print(CAPTURE_WARNING)
        return 0
    interactive = sys.stdin.isatty() and not kill_all
    if not kill_all:
        apps = _uniq([nm for t in targets for nm, _ in t.get("relaunch", [])])
        info = [_snap_info(path, data),
                f"Laufende Programme aus diesem Snapshot: {len(targets)}",
                "Beenden erfolgt mit SIGTERM (Programme duerfen sauber schliessen)."]
        if apps and relaunch:
            info.append(f"App-Fenster, die danach neu gestartet werden: {', '.join(apps)}")
        if CAPTURE_WARNING:
            info.append(CAPTURE_WARNING)
        chosen = choose_scope("Beenden", "beenden", targets, info)
        if chosen == "checklist":
            chosen = checklist(f"Beenden – {path.name}", targets, [True] * len(targets),
                               foldable=True)
        if chosen is None:
            print("Abgebrochen.")
            return 1
        targets = chosen
        cats = {t["category"] for t in targets}
        wanted = {signature(e) for e in snap_entries}
        prot = protected_pids()
        extra = [e for e in current if e["category"] in cats and signature(e) not in wanted
                 and e["pid"] not in prot and not e.get("protect")]
        if extra and targets:
            choice = _live_choice(extra, cats)
            if choice is None:
                print("Abgebrochen.")
                return 1
            if choice == 0:
                saved = save_snapshot(current, "vor dem Beenden", assign_order(current))
                print(f"Gespeichert: {saved}  ({len(current)} Eintraege)")
                targets, _prot = kill_targets(
                    cfg, [e for e in current if e["category"] in cats], current)
    if not targets:
        print("Nichts zu beenden.")
        return 0
    side = collateral(targets, current)
    browsers = browser_targets(targets, {t["category"] for t in targets})
    for line in side:
        print("  " + line)
    if side and interactive and not dry_run and not _ask_yes("Trotzdem beenden?"):
        print("Abgebrochen.")
        return 1
    if browsers and not dry_run:
        names = ", ".join(f"{t['program']} (PID {t['pid']})" for t in browsers)
        print(f"Dabei wird ein Browser beendet: {names}")
        if not interactive or not _ask_yes("Browser wirklich beenden?"):
            targets = [t for t in targets if t not in browsers]
            print("Browser bleiben offen.")
    log.info("kill from %s: %d processes%s", path, len(targets), " (dry-run)" if dry_run else "")
    confirm = _ask_yes if interactive else None
    wait = cfg["kill_timeout"] if timeout is None else timeout
    with Overlay("Beenden", cfg["overlay"] and not dry_run):
        return terminate(targets, wait, force, dry_run, confirm_force=confirm,
                         relaunch=relaunch)


def cmd_list() -> int:
    """Command 'list': all snapshots, newest first.

    Returns:
        Exit code.
    """
    snaps = list_snapshots()
    if not snaps:
        print(f"Keine Snapshots in {SNAP_DIR}")
        return 0
    print(f"Snapshots in {SNAP_DIR} (neuester zuerst):")
    for s in snaps:
        print(f"  {s.name:<40} {snapshot_title(s)}")
    return 0


def cmd_manage(ask=None, read=None) -> int:
    """Interactive: rename, archive, bring back and delete snapshots.

    Args:
        ask (Callable | None): Yes/no question (tests); None asks in the terminal.
        read (Callable | None): Text input (tests); None reads from the terminal.

    Returns:
        Exit code.
    """
    ask = ask or _ask_yes
    read = read or input
    archived = False
    while True:
        snaps = list_archived() if archived else list_snapshots()
        where = "Archiv" if archived else "Snapshots"
        opts = [snapshot_title(s) for s in snaps]
        switch = ("→ Zurueck zu den Snapshots" if archived
                  else f"→ Archiv anzeigen ({len(list_archived())})")
        idx = menu(f"Snapshots verwalten – {where}", opts + [switch, "Fertig"],
                   info=[f"{len(snaps)} in {archive_dir() if archived else SNAP_DIR}",
                         "Snapshot waehlen, dann: umbenennen, archivieren, loeschen"])
        if idx is None or idx == len(opts) + 1:
            return 0
        if idx == len(opts):
            archived = not archived
            continue
        path = snaps[idx]
        acts = (["Umbenennen", "Aus dem Archiv zurueckholen", "Loeschen", "Zurueck"] if archived
                else ["Umbenennen", "Archivieren", "Loeschen", "Zurueck"])
        act = menu(f"{path.name}", acts, info=[snapshot_title(path)])
        if act is None or act == 3:
            continue
        try:
            if act == 0:
                label = read("Neue Bezeichnung (Enter = keine): ").strip()
                rename_snapshot(path, label)
                print(f"Umbenannt: {path.name}  «{label}»")
            elif act == 1:
                dest = move_snapshot(path, not archived)
                print(f"{'Zurueckgeholt' if archived else 'Archiviert'}: {dest}")
            elif act == 2 and ask(f"{path.name} endgueltig loeschen?"):
                delete_snapshot(path)
                print(f"Geloescht: {path.name}")
        except (OSError, json.JSONDecodeError, EOFError) as exc:
            print(f"Fehler: {exc}")


def cmd_show(spec: str | None) -> int:
    """Command 'show': content of one snapshot.

    Args:
        spec: Snapshot ("latest", name, path) or None for the menu.

    Returns:
        Exit code.
    """
    path = pick_snapshot(spec)
    data = _load_or_report(path) if path else None
    if path is None or data is None:
        return 1
    print(f"# {path}\n# erstellt {data.get('created')}  "
          f"Sitzung {data.get('session_type')}/{data.get('desktop')}")
    for cat in CAT_ORDER:
        items = [e for e in data.get("entries", []) if e["category"] == cat]
        if not items:
            continue
        print(f"\n== {CAT_LABEL[cat]} ({len(items)}) ==")
        for e in items:
            print(f"  {e['program']} (PID {e.get('pid')})")
            for f in e["files"]:
                print(f"      Datei:  {f}{'' if os.path.exists(f) else '  [FEHLT]'}")
            print(f"      Befehl: {shlex.join(e['cmd'])}")
            if e.get("argv") and e["argv"] != e["cmd"]:
                print(f"      Original: {shlex.join(e['argv'])}")
            if e.get("found_via"):
                print(f"      Gefunden ueber: {e['found_via']}")
            if "env" in e:
                now = {k: v for k, v in os.environ.items() if is_look_var(k)}
                diff = sorted(k for k in set(now) | set(e["env"]) if now.get(k) != e["env"].get(k))
                print("      Umgebung: wie beim Speichern"
                      + (f" (weicht vom Terminal ab: {', '.join(diff)})" if diff else ""))
            if e.get("launched_from") or e.get("launched_via"):
                print(f"      Gestartet ueber: {e.get('launched_from') or '?'} "
                      f"({e.get('launched_via')})")
    return 0


def cmd_programs(video: str | None, image: str | None, reset: bool) -> int:
    """Show or set the programs used to open videos/images on restore.

    Args:
        video: Program for videos; None keeps the setting, "", "-" or "original" removes it.
        image: Program for images; same values as video.
        reset: Remove both choices first.

    Returns:
        Exit code.
    """
    progs = {} if reset else load_programs()
    for slot, val in (("video", video), ("image", image)):
        if val is not None:
            if val in ("", "-", "original"):
                progs.pop(slot, None)
            else:
                progs[slot] = val
    if reset or video is not None or image is not None:
        save_programs(progs)
    all_apps = desktop_apps(include_hidden=True)
    apps = desktop_apps()
    print(f"Einstellungen ({PROGRAMS_FILE}):")
    for slot, label in SLOT_LABEL.items():
        print(f"  {label:<7} {program_label(progs.get(slot), all_apps)}")
    for slot, mimes in SLOT_FALLBACK_MIMES.items():
        print(f"\nInstallierte Programme fuer {SLOT_LABEL[slot]} ({', '.join(mimes)}):")
        for app, n in handlers_for(mimes, apps):
            print(f"  {app['id']:<44} {app['name']}  (oeffnet {n}/{len(mimes)})")
    return 0


# environment variables that decide the look (light/dark theme, toolkit backend)
LOOK_VARS = ("GTK_THEME", "GTK_USE_PORTAL", "GDK_BACKEND", "QT_QPA_PLATFORMTHEME",
             "QT_STYLE_OVERRIDE", "QT_QPA_PLATFORM", "XDG_CURRENT_DESKTOP",
             "XDG_SESSION_DESKTOP", "XDG_DATA_DIRS", "DISPLAY", "WAYLAND_DISPLAY")


def cmd_diagnose(cfg) -> int:
    """Report, per relevant process, what session-snap sees and why it is (not) recorded.
    Relevant: known players/viewers/browsers and every process with a media/image file
    open or in its arguments. The output is meant to be copied into a bug report.

    Args:
        cfg (dict): Configuration.

    Returns:
        Exit code.
    """
    procs = scan_processes()
    entries = build_entries(cfg, procs)
    by_pid: dict = {}
    for e in entries:
        by_pid.setdefault(e["pid"], []).append(e)
    desk = desktop_exec_names()
    ids = {a["id"] for a in desktop_apps(include_hidden=True)}
    known = cfg["video_players"] | cfg["image_viewers"] | BROWSER_NAMES
    n = 0
    print(f"session-snap {VERSION} diagnose - Sitzung {os.environ.get('XDG_SESSION_TYPE', '?')}"
          f"/{os.environ.get('XDG_CURRENT_DESKTOP', '?')}")
    look = [f"{k}={os.environ[k]}" for k in LOOK_VARS if k in os.environ]
    print(f"Darstellung (session-snap selbst): {' '.join(look) if look else '-'}")
    ref = session_look_env()
    if ref:
        mine = {k: v for k, v in os.environ.items() if is_look_var(k)}
        diff = sorted(k for k in set(mine) | set(ref[2]) if mine.get(k) != ref[2].get(k))
        print(f"Desktop-Sitzung (Vorlage fuer Darstellung): {ref[0]} PID {ref[1]}")
        for k in diff:
            print(f"    {k}: Sitzung={ref[2].get(k, '(nicht gesetzt)')}  "
                  f"Terminal={mine.get(k, '(nicht gesetzt)')}")
        if not diff:
            print("    keine Abweichung zum Terminal")
    else:
        print("Desktop-Sitzung (Vorlage fuer Darstellung): nicht gefunden")
    for key in ("color-scheme", "gtk-theme"):
        try:
            r = subprocess.run(["gsettings", "get", "org.gnome.desktop.interface", key],
                               capture_output=True, text=True, timeout=5, check=False)
            val = r.stdout.strip() or r.stderr.strip()
        except (OSError, subprocess.TimeoutExpired) as exc:
            val = f"nicht abfragbar ({exc.__class__.__name__})"
        print(f"gsettings org.gnome.desktop.interface {key}: {val}")
    print(f"Mitschreiber (Alt-Tab-Reihenfolge): "
          f"{'laeuft, PID ' + str(watch_pid()) if watcher_alive() else 'laeuft NICHT'}; "
          f"Autostart: {'ja' if autostart_file().exists() else 'nein'}")
    wins = list_windows()
    if wins is None:
        print("Fensterliste (COSMIC): nicht verfuegbar")
    else:
        print(f"Fensterliste (COSMIC): {len(wins)} Fenster")
        for a, t in wins:
            print(f"    {a or '?'}: {t}")
    for pid in sorted(procs):
        q = procs[pid]
        media = fd_files(pid, MEDIA_EXT | IMAGE_EXT)
        argf = argv_files(q)
        if not (q.names & known or media or argf and any(
                _ext(f) in MEDIA_EXT | IMAGE_EXT for f in argf) or pid in by_pid):
            continue
        n += 1
        origin, via = launched_from(pid, q.ppid, ids)
        print(f"\n[{n}] PID {pid}  exe={q.exe or '?'}  flatpak={q.flatpak_id or '-'}")
        print(f"    argv:    {shlex.join(q.argv)}")
        print(f"    cwd:     {q.cwd or '?'}")
        print(f"    offen:   {', '.join(media) if media else '-'}")
        print(f"    Starter: {origin or '-'}  ({via or 'nicht ermittelbar'})")
        env = _read_environ(pid)
        look = [f"{k}={env[k]}" for k in LOOK_VARS if k in env]
        print(f"    Darstellung: {' '.join(look) if look else '-'}")
        if pid in by_pid:
            for e in by_pid[pid]:
                print(f"    ERFASST als {CAT_LABEL[e['category']]}: {shlex.join(e['cmd'])}")
                if e.get("found_via"):
                    print(f"        gefunden ueber {e['found_via']}")
            continue
        parent = procs.get(q.ppid)
        if parent and q.exe and parent.exe == q.exe:
            why = f"Kindprozess von PID {q.ppid} mit gleichem Programm"
        elif _match_any(q.names, cfg["exclude"]):
            why = "passt auf ein exclude-Muster"
        elif not q.names - GENERIC_NAMES:
            why = "Interpreter/Starter ohne eigenen Programmnamen"
        elif not q.names & desk:
            why = "kein sichtbarer .desktop-Eintrag (include/video_players/image_viewers)"
        else:
            why = "keine Regel trifft zu"
        print(f"    NICHT erfasst: {why}")
    if not n:
        print("\nKeine relevanten Prozesse gefunden.")
    return 0


def cmd_config() -> int:
    """Command 'config': write the configuration template (never overwrites).

    Returns:
        Exit code.
    """
    if CONFIG_FILE.exists():
        print(f"Konfiguration existiert bereits: {CONFIG_FILE}")
        return 0
    CONFIG_FILE.parent.mkdir(parents=True, exist_ok=True)
    CONFIG_FILE.write_text(CONFIG_TEMPLATE, encoding="utf-8")
    print(f"Vorlage geschrieben: {CONFIG_FILE}")
    return 0


def interactive_menu(cfg) -> int:
    """Main menu shown when session-snap is started without a command.

    Args:
        cfg (dict): Configuration.

    Returns:
        Exit code.
    """
    while True:
        snaps = list_snapshots()
        idx = menu("Hauptmenue",
                   ["Aktuellen Zustand aufnehmen (Snapshot speichern)",
                    f"Snapshot wiederherstellen ({len(snaps)} vorhanden)",
                    "Protokollierte Programme beenden (kill)",
                    "Snapshot-Inhalt anzeigen",
                    "Programme fuer Videos/Bilder waehlen",
                    "Snapshots verwalten (umbenennen, archivieren, loeschen)",
                    "Beenden"],
                   info=[f"Snapshots: {SNAP_DIR}"])
        if idx is None or idx == 6:
            return 0
        if idx == 5:
            cmd_manage()
            continue
        if idx == 0:
            cmd_save(cfg, None, True)
        elif idx == 1:
            cmd_restore(cfg, None, False, False)
        elif idx == 2:
            cmd_kill(cfg, None, False, False, None, False)
        elif idx == 3:
            cmd_show(None)
        elif idx == 4:
            latest = list_snapshots()
            data = _load_or_report(latest[0]) if latest else None
            choose_programs((data or {}).get("entries", []))
            continue
        try:
            input("\nWeiter mit Enter …")
        except EOFError:
            return 0


DASH_ALIASES = {"--update": "update", "--save": "save", "--restore": "restore",
                "--kill": "kill", "-k": "kill",
                "--list": "list", "--show": "show", "--config": "config",
                "--programs": "programs", "--diagnose": "diagnose"}


def build_parser() -> argparse.ArgumentParser:
    """Command line parser.

    Returns:
        The parser.
    """
    ap = argparse.ArgumentParser(
        prog="session-snap",
        description="Offene Programme (Videos, Bilder, Browser, Sonstige) protokollieren, "
                    "wiederherstellen und beenden.")
    ap.add_argument("--version", action="version", version=f"%(prog)s {VERSION}")
    sub = ap.add_subparsers(dest="cmd")
    s = sub.add_parser("save", help="aktuellen Zustand speichern")
    s.add_argument("-l", "--label", help="Bezeichnung des Snapshots (frei waehlbar)")
    s.add_argument("-y", "--yes", action="store_true",
                   help="ohne Checkliste/Rueckfrage alles speichern")
    s.add_argument("--kill", action="store_true",
                   help="nach dem Speichern die gespeicherten Programme beenden")
    s.add_argument("--force", action="store_true",
                   help="mit --kill: nicht reagierende Prozesse per SIGKILL beenden")
    s.add_argument("--no-relaunch", action="store_true",
                   help="mit --kill: App-Fenster (z. B. Claude) danach NICHT neu starten")
    r = sub.add_parser("restore", help="Snapshot wiederherstellen")
    r.add_argument("-s", "--snapshot", help="Dateiname, Pfad oder 'latest'")
    r.add_argument("--all", action="store_true",
                   help="alle (nicht laufenden) ohne Rueckfrage starten")
    r.add_argument("--dry-run", action="store_true", help="nur anzeigen, nichts starten")
    r.add_argument("--video", metavar="PROG",
                   help="Videos diesmal mit PROG oeffnen (Desktop-ID wie mpv.desktop oder Befehl)")
    r.add_argument("--image", metavar="PROG",
                   help="Bilder diesmal mit PROG oeffnen (Desktop-ID oder Befehl)")
    r.add_argument("--play", action="store_true",
                   help="Videos sofort abspielen (keine Startoptionen wie --pause)")
    r.add_argument("--no-maximize", action="store_true",
                   help="Fenster nach dem Start weder maximieren noch auf Vollbild schalten")
    r.add_argument("--order", choices=("saved", "keep"),
                   help="Alt-Tab-Reihenfolge: saved = wie beim Speichern, keep = nicht aendern")
    k = sub.add_parser("kill", help="laufende Programme eines Snapshots beenden")
    k.add_argument("-s", "--snapshot", help="Dateiname, Pfad oder 'latest'")
    k.add_argument("--all", action="store_true", help="alle ohne Rueckfrage beenden")
    k.add_argument("--force", action="store_true",
                   help="nicht reagierende Prozesse nach der Wartezeit per SIGKILL beenden")
    k.add_argument("--timeout", type=float, help="Wartezeit nach SIGTERM in Sekunden")
    k.add_argument("--dry-run", action="store_true", help="nur anzeigen, nichts beenden")
    k.add_argument("--no-relaunch", action="store_true",
                   help="App-Fenster (z. B. Claude) danach NICHT neu starten")
    sub.add_parser("list", help="Snapshots auflisten")
    sub.add_parser("manage", help="Snapshots umbenennen, archivieren, loeschen")
    sh = sub.add_parser("show", help="Snapshot-Inhalt ausgeben")
    sh.add_argument("snapshot", nargs="?", help="Dateiname, Pfad oder 'latest'")
    sub.add_parser("config", help="Konfigurationsvorlage anlegen")
    pg = sub.add_parser("programs", help="Programme fuer Videos/Bilder anzeigen oder festlegen")
    pg.add_argument("--video", metavar="PROG", help="Standard fuer Videos ('original' = keiner)")
    pg.add_argument("--image", metavar="PROG", help="Standard fuer Bilder ('original' = keiner)")
    pg.add_argument("--reset", action="store_true", help="alle Festlegungen loeschen")
    sub.add_parser("diagnose", help="zeigen, was erkannt wird und warum (fuer Fehlerberichte)")
    w = sub.add_parser("watch", help="Alt-Tab-Reihenfolge mitschreiben (Hintergrundprozess)")
    w.add_argument("--stop", action="store_true", help="laufenden Mitschreiber beenden")
    w.add_argument("--status", action="store_true", help="anzeigen, ob er laeuft")
    w.add_argument("--start", action="store_true",
                   help="Autostart-Eintrag anlegen und im Hintergrund starten")
    u = sub.add_parser("update", help="neuere Version installieren, dann gelieferte "
                                      "Patch-Archive einspielen (Entwickler)")
    u.add_argument("options", nargs="*", metavar="OPTION",
                   help="an apply-release.sh weitergereicht, z. B. --list oder -y")
    sub.add_parser("reload", help="laufenden Mitschreiber (watch) auf die installierte "
                                  "Version umschalten")
    return ap


def main(argv: list | None = None) -> int:  # pylint: disable=too-many-return-statements
    """Entry point: parse the command line and run the command; returns the exit code.

    Args:
        argv: Arguments without the program name; None uses sys.argv.

    Returns:
        Exit code.
    """
    argv = list(sys.argv[1:] if argv is None else argv)
    if argv and argv[0] in DASH_ALIASES:
        argv[0] = DASH_ALIASES[argv[0]]
    try:
        locale.setlocale(locale.LC_ALL, "")
    except locale.Error:
        pass
    os.environ.setdefault("ESCDELAY", "25")  # snappy Esc key in curses
    if argv and argv[0] == "--install":
        print("session-snap wird seit 2.0.0 mit install.sh aus einem Release-Tag installiert:\n"
              "  cd ~/Projects/session-snap && ./install.sh\n"
              "Aktualisieren: session-snap update")
        return 2
    check_not_root()
    init_paths()
    # "update" hands all further arguments unchanged to apply-release.sh
    extra = argv[1:] if argv and argv[0] == "update" else []
    a = build_parser().parse_args(argv[:1] if extra else argv)
    done = [] if os.environ.get("SESSION_SNAP_DIR") else migrate_layout()
    setup_logging()
    if done:
        print("Umzug in die XDG-Ablage (einmalig):")
        for x in done:
            print(f"  {x}")
            log.info("migration: %s", x)
        note = old_path_block_note()
        if note:
            print(note)
        if autostart_file().exists():
            print("  " + "\n  ".join(install_watcher(start=True)))
    if a.cmd == "update":
        return ReleaseUpdater(extra).run()
    if a.cmd == "reload":
        return cmd_reload()
    cfg = load_config()
    try:
        if a.cmd == "save":
            tty = sys.stdin.isatty()
            return cmd_save(cfg, a.label, not a.yes and tty, a.kill, a.force,
                            not a.no_relaunch)
        if a.cmd == "restore":
            over = {k: v for k, v in (("video", a.video), ("image", a.image)) if v}
            return cmd_restore(cfg, a.snapshot, a.all, a.dry_run, over, not a.play,
                               not a.no_maximize, a.order)
        if a.cmd == "watch":
            return cmd_watch_cli(a.stop, a.status, a.start)
        if a.cmd == "kill":
            return cmd_kill(cfg, a.snapshot, a.all, a.force, a.timeout, a.dry_run,
                            not a.no_relaunch)
        if a.cmd == "manage":
            return cmd_manage()
        if a.cmd == "programs":
            return cmd_programs(a.video, a.image, a.reset)
        if a.cmd == "diagnose":
            return cmd_diagnose(cfg)
        if a.cmd == "list":
            return cmd_list()
        if a.cmd == "show":
            return cmd_show(a.snapshot)
        if a.cmd == "config":
            return cmd_config()
        return interactive_menu(cfg)
    except KeyboardInterrupt:
        print("\nAbgebrochen.")
        return 130


def run() -> None:
    """Console entry point: run :func:`main` and end quietly on a closed pipe (``| head``)."""
    try:
        sys.exit(main())
    except BrokenPipeError:
        os.dup2(os.open(os.devnull, os.O_WRONLY), sys.stdout.fileno())
        sys.exit(0)
