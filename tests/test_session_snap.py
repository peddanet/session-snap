"""Unit and integration tests for session-snap (pytest; Python standard library only).

Run as the normal desktop user from the clone (tools/check.sh runs them with coverage):
    python3 -m pytest -q

Safety: the tests only ever signal dummy processes they started themselves. The dummies are
copies of the Python interpreter named vlc / eog / fakeapp / hiddenapp inside a temporary
directory; every process list is filtered to binaries inside that directory.
"""
# Test classes are numbered T<n>_<Topic> as referenced in the documentation; tests
# deliberately exercise internal helpers of the program.
# pylint: disable=invalid-name,protected-access
from __future__ import annotations

import contextlib
import curses
import fcntl
import importlib
import io
import json
import os
import pty
import re
import select
import shutil
import signal
import socket
import struct
import subprocess
import sys
import tempfile
import termios
import threading
import time
import unittest
from datetime import datetime
from pathlib import Path
from unittest import mock
from urllib.parse import quote

HERE = Path(__file__).resolve().parent
SRC = HERE.parent / "src"
IS_ROOT = os.geteuid() == 0
os.environ.setdefault("SESSION_SNAP_DIR", tempfile.mkdtemp(prefix="ssnap-import-"))
ss = importlib.import_module("session_snap.cli")
SCAN_PROCESSES = ss.scan_processes   # the real scan, before any test replaces it
# command line of the program for tests that start it as a process
PROG = [sys.executable, "-m", "session_snap"]
os.environ["PYTHONPATH"] = str(SRC) + (os.pathsep + os.environ["PYTHONPATH"]
                                       if os.environ.get("PYTHONPATH") else "")
os.environ["SESSION_SNAP_OVERLAY"] = "0"   # no desktop notifications while testing (T59: own)

SLEEP = "import time\ntime.sleep(120)\n"
# dummies hold the file named by their first argument (as a restored player does) or by $F
HOLD_F = ("import os, sys, time, urllib.parse as u\n"
          "a = [x for x in sys.argv[1:] if x != '--']\n"
          "f = open(u.unquote(u.urlparse(a[-1]).path) if a else os.environ['F'])\n"
          "time.sleep(120)\n")
IGNORE_TERM = ("import os, signal, sys, time\nsignal.signal(signal.SIGTERM, signal.SIG_IGN)\n"
               "f = open(sys.argv[1] if len(sys.argv) > 1 else os.environ['F'])\n"
               "time.sleep(120)\n")
ONE = "import time;time.sleep(120)"  # one-line variant for desktop-entry Exec lines
WITH_CHILD = ("import subprocess, sys, time\n"
              "subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(120)'])\n"
              "time.sleep(120)\n")


@unittest.skipIf(IS_ROOT, "must run as normal user (the tool refuses root)")
class Base(unittest.TestCase):
    """Isolated environment: storage dir, desktop entries, dummy binaries, media files."""

    tmp: Path
    bin: Path
    store: Path
    media: Path
    film: Path
    png: Path
    gif: Path
    cfg: dict

    @classmethod
    def setUpClass(cls):
        cls.tmp = Path(tempfile.mkdtemp(prefix="ssnap-test-"))
        cls.bin = cls.tmp / "bin"
        cls.media = cls.tmp / "media dir"
        cls.store = cls.tmp / "store"
        for d in (cls.bin, cls.media, cls.store, cls.tmp / "sys" / "applications"):
            d.mkdir(parents=True)
        py = os.path.realpath(sys.executable)
        for name in ("vlc", "eog", "fakeapp", "hiddenapp", "childapp", "chrome", "firefox",
                     "fakeplayer", "fakeviewer"):
            shutil.copy2(py, cls.bin / name)
        apps = cls.tmp / "data" / "applications"
        apps.mkdir(parents=True)
        (apps / "fake.desktop").write_text(
            "[Desktop Entry]\nType=Application\nName=Fake\nExec=env FOO=1 fakeapp %U\n")
        (apps / "child.desktop").write_text(
            "[Desktop Entry]\nType=Application\nName=Child\nExec=childapp\n")
        (apps / "hidden.desktop").write_text(
            "[Desktop Entry]\nType=Application\nName=Hidden\nNoDisplay=true\nExec=hiddenapp\n")
        b = cls.bin
        # Chrome app launcher (like the entry Chrome creates for an installed web app)
        (apps / "chrome-abc123-Default.desktop").write_text(
            "[Desktop Entry]\nType=Application\nName=Claude\n"
            f'Exec={b}/chrome -c "{ONE}" --profile-directory=Default --app-id=abc123\n')
        (apps / "fakeplayer.desktop").write_text(
            "[Desktop Entry]\nType=Application\nName=Fake Player\n"
            f'Exec={b}/fakeplayer -c "{ONE}" %f\nMimeType=video/x-matroska;video/mp4;\n')
        (apps / "fakeviewer.desktop").write_text(
            "[Desktop Entry]\nType=Application\nName=Fake Viewer\n"
            f'Exec={b}/fakeviewer -c "{ONE}" %U\nMimeType=image/png;image/gif;\n')
        cls.film = cls.media / ("Film mit Leerzeichen und einem sehr langen Namen, "
                                "der umbrochen werden muss.mkv")
        cls.png = cls.media / "a.png"
        cls.gif = cls.media / "b.gif"
        for f in (cls.film, cls.png, cls.gif):
            f.write_text(SLEEP)  # restored "viewers" execute the file -> stay alive
        (cls.store / "config.ini").write_text(
            "[session-snap]\ndelay = 0\nkill_timeout = 3\nvideo_players = fakeplayer\n"
            "image_viewers = fakeviewer\n"
            # dummies are Python interpreters: no player options for "vlc"/"eog"; "-B" is a
            # harmless Python flag used to observe the insertion for "fakeplayer"
            "[player-options]\nvlc =\neog =\nfakeplayer = -B\n")
        cls._env_backup = dict(os.environ)
        os.environ.update({"XDG_DATA_HOME": str(cls.tmp / "data"),
                           "XDG_DATA_DIRS": str(cls.tmp / "sys"),
                           "SESSION_SNAP_DIR": str(cls.store),
                           # only the dummies of this run are scanned, here and in every
                           # child: an image viewer open on the desktop took over the
                           # windows of the dummy viewer (T44) and added an image the kill
                           # dialog asked about (T56)
                           "SESSION_SNAP_ONLY_PATH": str(cls.bin) + "/"})
        # isolated from a running desktop session: no Wayland socket (window list unknown,
        # as over SSH) and no real session process as reference for the appearance
        (cls.tmp / "run").mkdir(mode=0o700)
        os.environ.update({"WAYLAND_DISPLAY": "wayland-test",
                           "XDG_RUNTIME_DIR": str(cls.tmp / "run")})
        cls._session_refs = ss.SESSION_REFS
        ss.SESSION_REFS = ("ssnap-panel",)
        ss.init_paths()
        ss.setup_logging()
        cls.cfg = ss.load_config()

    @classmethod
    def tearDownClass(cls):
        cls._kill_all_dummies()
        for h in list(ss.log.handlers):  # close the log file of the temporary store
            ss.log.removeHandler(h)
            h.close()
        os.environ.clear()
        os.environ.update(cls._env_backup)
        ss.SESSION_REFS = cls._session_refs
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def setUp(self):
        self.children = []
        for f in self.store.glob("snapshots/session-snap_*.json"):
            f.unlink()
        (self.store / "programs.ini").unlink(missing_ok=True)

    def tearDown(self):
        self._kill_all_dummies()
        for p in self.children:  # reap our own Popen objects (no ResourceWarning)
            with contextlib.suppress(subprocess.TimeoutExpired):
                p.wait(timeout=3)

    # ------------------------------------------------------------------ helpers
    @classmethod
    def _dummy_pids(cls) -> list:
        out = []
        for d in os.listdir("/proc"):
            if d.isdigit():
                exe = ss._readlink(f"/proc/{d}/exe")
                if exe.startswith(str(cls.bin) + "/"):
                    out.append(int(d))
        return out

    @classmethod
    def _kill_all_dummies(cls):
        for pid in cls._dummy_pids():
            with contextlib.suppress(OSError):
                os.kill(pid, signal.SIGKILL)
        deadline = time.monotonic() + 3
        while cls._dummy_pids() and time.monotonic() < deadline:
            time.sleep(0.05)

    def spawn(self, name, code, *args, cwd=None, hold=None):
        """Start dummy program `name` running `code`; it is ended in tearDown.

        Args:
            name (str): Program name of the dummy (a copy of the interpreter).
            code (str): Python code it runs.
            *args: Further command line arguments.
            cwd (Path | None): Working directory; None uses the test folder.
            hold (Path | None): File the dummy keeps open (variable F).

        Returns:
            subprocess.Popen: The started process.
        """
        env = dict(os.environ)
        if hold:
            env["F"] = str(hold)
        # ended in tearDown via self.children
        p = subprocess.Popen([str(self.bin / name),  # pylint: disable=consider-using-with
                             "-c", code, *args], cwd=cwd or self.tmp,
                             env=env, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                             stderr=subprocess.DEVNULL, start_new_session=True)
        self.children.append(p)
        return p

    def mine(self, entries):
        """Only the entries that belong to dummy processes of this test.

        Args:
            entries (list): Entries of a capture.

        Returns:
            list: The entries of this test's dummies.
        """
        return [e for e in entries if e["cmd"][0].startswith(str(self.bin) + "/")]

    def only_dummies(self):
        """Running processes, restricted to the dummies of this test (stand-in for
        ss.scan_processes, so that real programs of the desktop are not seen).

        Returns:
            dict: {pid: Proc} of the dummies.
        """
        mine = set(self._dummy_pids())
        return {pid: p for pid, p in SCAN_PROCESSES().items() if pid in mine}

    def entries(self):
        """Current capture, filtered to the dummies of this test.

        Returns:
            list: The filtered entries.
        """
        return self.mine(ss.build_entries(self.cfg))

    def wait_child(self, pid, fd, timeout=30.0):
        """Wait for a child started with pty.fork, reading its terminal meanwhile.

        Without a limit a hanging child would block the whole test run. On timeout the
        child is killed and the test fails with what its terminal showed last and the
        kernel function it waited in (/proc/PID/wchan).

        Args:
            pid (int): Process id of the child.
            fd (int): Master side of its pseudo-terminal.
            timeout (float): Seconds until the test fails.

        Returns:
            str: Terminal output read while waiting (escape sequences removed).
        """
        data, deadline = b"", time.monotonic() + timeout
        while time.monotonic() < deadline:
            if os.waitpid(pid, os.WNOHANG)[0] == pid:
                break
            if select.select([fd], [], [], 0.1)[0]:
                try:
                    data += os.read(fd, 65536)
                except OSError:
                    pass
        else:
            try:
                wchan = Path(f"/proc/{pid}/wchan").read_text(encoding="utf-8")
            except OSError:
                wchan = "?"
            os.kill(pid, signal.SIGKILL)
            os.waitpid(pid, 0)
            text = data.decode("utf-8", "replace")
            self.fail(f"child {pid} still running after {timeout:.0f} s (waits in "
                      f"{wchan!r}); last terminal output:\n{text[-2000:]}")
        return re.sub(r"\x1b\[[0-9;?]*[A-Za-z]|\x1b[()][A-Z0-9]|\x1b[=>]", "",
                      data.decode("utf-8", "replace"))

    def wait_for(self, pred, timeout=8.0, msg="condition"):
        """Poll `pred` until it is true or fail after `timeout` seconds.

        Args:
            pred (Callable): Condition to poll.
            timeout (float): Seconds until the test fails.
            msg (str): Description for the failure message.
        """
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if pred():
                return
            time.sleep(0.05)
        self.fail(f"timeout waiting for {msg}")

    def start_standard_set(self):
        """vlc holding the film via fd only, eog with relative + file:// args, fakeapp.

        Returns:
            list: The entries of the started dummies.
        """
        self.spawn("vlc", HOLD_F, hold=self.film)
        uri = "file://" + quote(str(self.gif))
        self.spawn("eog", SLEEP, "a.png", uri, cwd=self.media)
        self.spawn("fakeapp", SLEEP)
        want = [[str(self.film)], [str(self.png)], [str(self.gif)], []]
        # wait for the exact state: vlc must already hold the film open (fd), otherwise
        # it would be captured as "vlc without file" (different signature)
        self.wait_for(lambda: [e["files"] for e in self.entries()] == want,
                      msg="4 dummy entries incl. open film")
        return self.entries()

    @contextlib.contextmanager
    def isolated(self):
        """Restrict build_entries to the dummies - real desktop programs stay untouched.

        Yields:
            None: While build_entries sees only the dummies.
        """
        orig = ss.build_entries
        with mock.patch.object(ss, "build_entries", lambda cfg, procs=None, windows=False:
                               self.mine(orig(cfg, procs, windows))):
            yield

    def run_main(self, *argv) -> tuple[int, str]:
        """Run main() with `argv`; returns (exit code, captured stdout).

        Args:
            *argv: Command line arguments.

        Returns:
            (exit code, captured standard output).
        """
        buf = io.StringIO()
        with self.isolated(), contextlib.redirect_stdout(buf):
            rc = ss.main(list(argv))
        return rc, buf.getvalue()


# =========================================================================== unit tests
class T1_Unit(Base):
    """Pure functions without processes."""

    def test_01_argv_files(self):
        """argv parsing: relative, absolute, file:// URI, options/URLs/missing skipped"""
        p = ss.Proc(1, 0, ["x", "-v", "a.png", str(self.gif), "file://" + quote(str(self.film)),
                           "https://example.org/a.png", "missing.png", "a.png"],
                    "/x", str(self.media), 0)
        self.assertEqual(ss.argv_files(p), [str(self.png), str(self.gif), str(self.film)])

    def test_02_desktop_names(self):
        """.desktop parsing: env prefix resolved, NoDisplay excluded, interpreters ignored"""
        names = ss.desktop_exec_names()
        self.assertIn("fakeapp", names)
        self.assertIn("childapp", names)
        self.assertNotIn("hiddenapp", names)
        self.assertNotIn("env", names)

    def test_03_signature_pid_independent(self):
        """signature(): identical for the same program/file under a different PID"""
        a = {"category": "vlc", "program": "vlc", "cmd": ["/v", "/f"], "files": ["/f"],
             "pid": 1}
        b = dict(a, pid=999, start=5)
        self.assertEqual(ss.signature(a), ss.signature(b))

    def test_04_naming_rule_and_order(self):
        """file naming rule session-snap_DATE_TIME.json, same-second suffix, newest first"""
        fixed = datetime(2026, 10, 14, 20, 30, 5)
        with mock.patch.object(ss, "datetime") as dt:
            dt.now.return_value = fixed
            p1 = ss.save_snapshot([], "a")
            p2 = ss.save_snapshot([], "b")
        self.assertEqual(p1.name, "session-snap_2026-10-14_20-30-05.json")
        self.assertEqual(p2.name, "session-snap_2026-10-14_20-30-05_1.json")
        self.assertEqual(p1.parent, self.store / "snapshots")
        self.assertEqual(ss.list_snapshots()[0], p2)
        pattern = r"^session-snap_\d{4}-\d\d-\d\d_\d\d-\d\d-\d\d(_\d+)?\.json$"
        for p in ss.list_snapshots():
            self.assertRegex(p.name, pattern)

    def test_05_roundtrip_umlaut_and_non_utf8(self):
        """snapshot round trip keeps umlauts and non-UTF-8 file names byte-exact"""
        raw = os.fsencode(str(self.media)) + b"/caf\xe9 \xc3\xa4.png"
        with open(raw, "w", encoding="utf-8") as fh:
            fh.write("x")
        name = os.fsdecode(raw)
        e = {"category": "image", "program": "eog", "cmd": ["/eog", name], "files": [name],
             "pid": 1, "start": 1, "cwd": "", "flatpak": ""}
        data = ss.load_snapshot(ss.save_snapshot([e], "Größe"))
        self.assertEqual(data["entries"][0]["files"][0], name)
        self.assertEqual(data["label"], "Größe")
        self.assertTrue(os.path.exists(data["entries"][0]["files"][0]))
        os.remove(raw)

    def test_06_config(self):
        """config.ini: overrides applied, invalid number ignored, broken file -> defaults"""
        cfgf = self.tmp / "cfg.ini"
        cfgf.write_text("[session-snap]\nexclude = foo*, bar\ndelay = 0.1\nkill_timeout = x\n")
        with mock.patch.object(ss, "CONFIG_FILE", cfgf):
            c = ss.load_config()
        self.assertIn("foo*", c["exclude"])
        self.assertEqual(c["delay"], 0.1)
        self.assertEqual(c["kill_timeout"], 5.0)
        cfgf.write_text("no section header\n")
        with mock.patch.object(ss, "CONFIG_FILE", cfgf), \
                contextlib.redirect_stderr(io.StringIO()):
            self.assertEqual(ss.load_config()["delay"], 0.4)

    def test_57_restore_menu_counts(self):
        """restore menu: 'n von m' per category plus how many are already running;
        kill menu (no total): plain count"""
        self.assertEqual(ss.scope_count(0, 16), "(0 von 16 – 16 laufen bereits)")
        self.assertEqual(ss.scope_count(3, 3), "(3 von 3)")
        self.assertEqual(ss.scope_count(0, 0), "(0 von 0)")
        self.assertEqual(ss.scope_count(4, None), "(4)")
        total = [{"category": "image"}] * 9 + [{"category": "browser"}] * 3
        pending = [{"category": "image"}] * 2
        seen = {}
        with mock.patch.object(ss, "menu", lambda t, opts, info: seen.setdefault("o", opts)
                               and None):
            ss.choose_scope("W", "starten", pending, [], total=total)
        self.assertEqual(seen["o"][0], "Alle starten (2 von 12 – 10 laufen bereits)")
        self.assertIn("Nur Bilder (2 von 9 – 7 laufen bereits)", seen["o"])
        self.assertIn("Nur Videos (0 von 0)", seen["o"])
        with mock.patch.object(ss, "menu", lambda t, opts, info: seen.update(k=opts)):
            ss.choose_scope("B", "beenden", pending, [])
        self.assertEqual(seen["k"][0], "Alle beenden (2)")

    def test_58_eog_fullscreen_option(self):
        """images in 'fullscreen': eog is started with its own --fullscreen; videos are not
        touched by the image call, images not by the video call"""
        self.assertEqual(ss.DEFAULT_PLAYER_OPTIONS["eog"], ["--fullscreen"])
        img = {"category": "image", "program": "eog", "files": ["/b/a.png"],
               "cmd": ["/usr/bin/eog", "/b/a.png"]}
        vid = {"category": "vlc", "program": "vlc", "files": ["/v.mkv"],
               "cmd": ["/usr/bin/vlc", "/v.mkv"]}
        out = ss.apply_player_options([img, vid], ss.DEFAULT_PLAYER_OPTIONS, {"image"})
        self.assertEqual(out[0]["cmd"], ["/usr/bin/eog", "--fullscreen", "/b/a.png"])
        self.assertEqual(out[1]["cmd"], vid["cmd"])
        out = ss.apply_player_options([img], ss.DEFAULT_PLAYER_OPTIONS)
        self.assertEqual(out[0]["cmd"], img["cmd"])
        again = ss.apply_player_options(
            [dict(img, cmd=["/usr/bin/eog", "--fullscreen", "/b/a.png"])],
            ss.DEFAULT_PLAYER_OPTIONS, {"image"})
        self.assertEqual(again[0]["cmd"].count("--fullscreen"), 1)
        self.assertIn("eog = --fullscreen", ss.CONFIG_TEMPLATE)
        nom = dict(img, program="nomacs", cmd=["/usr/bin/nomacs", "/b/a.png"])
        out = ss.apply_player_options([nom], ss.DEFAULT_PLAYER_OPTIONS, {"image"})
        self.assertEqual(out[0]["cmd"], ["/usr/bin/nomacs", "--fullscreen", "/b/a.png"])
        cv = dict(img, program="cosmic-viewer", cmd=["/usr/bin/cosmic-viewer", "/b/a.png"])
        out = ss.apply_player_options([cv], ss.DEFAULT_PLAYER_OPTIONS, {"image"})
        self.assertEqual(out[0]["cmd"], cv["cmd"])                 # no option: path only

    def test_59_overlay_notification_and_cancel(self):
        """busy overlay: Notify with button 'Abbrechen' (critical, never expires), text
        updates replace the same notification, close removes it; the button interrupts
        session-snap like Ctrl+C. Without gdbus / session bus: silently nothing."""
        fake = self.tmp / "fakebin"
        fake.mkdir(exist_ok=True)
        calls = self.tmp / "gdbus-calls.txt"
        (fake / "gdbus").write_text(
            f"#!{sys.executable}\nimport os, sys, time\n"
            f"open({str(calls)!r}, 'a').write(' '.join(sys.argv[1:]) + '\\n')\n"
            "if sys.argv[1] == 'monitor':\n"
            "    if os.environ.get('FAKE_CANCEL'):\n"
            "        time.sleep(0.5)\n"
            "        print('/org/freedesktop/Notifications: org.freedesktop.Notifications."
            "ActionInvoked (uint32 7, \\'cancel\\')', flush=True)\n"
            "    time.sleep(30)\n"
            "elif 'Notify' in ' '.join(sys.argv):\n"
            "    print('(uint32 7,)')\n")
        os.chmod(fake / "gdbus", 0o755)  # nosec B103 - test stub must be executable
        env = {"PATH": f"{fake}:{os.environ['PATH']}", "SESSION_SNAP_OVERLAY": "1",
               "DBUS_SESSION_BUS_ADDRESS": "unix:path=/nonexistent"}
        with mock.patch.dict(os.environ, env), contextlib.redirect_stdout(io.StringIO()):
            with ss.Overlay("Test", "notify") as ov:
                ss.busy("Schritt 1")
                ss.busy("Schritt 2", 3, 7)
            self.assertIsNone(ss.Overlay.current)
            log = calls.read_text()
            self.assertEqual(log.count("Notifications.Notify"), 2)
            self.assertIn("session-snap 0 ", log)            # first: new notification
            self.assertIn("session-snap 7 ", log)            # update replaces id 7
            self.assertIn('["cancel", "Abbrechen"]', log)
            self.assertIn("<byte 2>", log)
            self.assertIn("CloseNotification 7", log)
            del ov
            with mock.patch.dict(os.environ, {"FAKE_CANCEL": "1"}):
                with self.assertRaises(KeyboardInterrupt):
                    with ss.Overlay("Test", "notify") as ov:
                        ss.busy("lange Arbeit")
                        time.sleep(5)
                self.assertTrue(ov.cancelled)
        with mock.patch.dict(os.environ, {"SESSION_SNAP_OVERLAY": "1",
                                          "DBUS_SESSION_BUS_ADDRESS": ""}):
            self.assertFalse(ss.Overlay("x", "notify").enabled)

    def test_59b_overlay_gtk_helper(self):
        """overlay styles fullscreen/corner: the GTK helper gets one JSON line per step
        (title, text, done, total) and keyboard on/off; "cancel" from the helper interrupts
        like Ctrl+C; a helper that cannot show anything falls back to the notification;
        config / environment select the style"""
        rec = self.tmp / "helper-in.txt"
        helper = self.tmp / "fake_overlay.py"
        helper.write_text(
            "import os, sys, time\n"
            f"rec = open({str(rec)!r}, 'a')\n"
            "rec.write('style ' + sys.argv[1] + '\\n'); rec.flush()\n"
            "if os.environ.get('FAKE_UNAVAILABLE'):\n"
            "    print('unavailable: no layer shell', flush=True)\n"
            "    sys.exit(3)\n"
            "print('ready', flush=True)\n"
            "for line in sys.stdin:\n"
            "    rec.write(line); rec.flush()\n"
            "    if os.environ.get('FAKE_CANCEL') and 'lange' in line:\n"
            "        print('cancel', flush=True)\n")
        env = {"SESSION_SNAP_OVERLAY": "1", "SESSION_SNAP_OVERLAY_PYTHON": sys.executable,
               "WAYLAND_DISPLAY": "wayland-test", "DBUS_SESSION_BUS_ADDRESS": ""}
        with mock.patch.dict(os.environ, env), mock.patch.object(ss, "OVERLAY_HELPER", helper), \
                contextlib.redirect_stdout(io.StringIO()) as out:
            with ss.Overlay("Test", "corner") as ov:
                ss.busy("Starte 3 Programm(e) …", 0, 3)
                ss.busy("Starte 3 Programm(e) …", 1, 3)
                ss.overlay_keyboard(False)
                ss.overlay_keyboard(True)
            self.assertIsNone(ov.helper)
            lines = rec.read_text().splitlines()
            self.assertEqual(lines[0], "style corner")
            msgs = [json.loads(x) for x in lines[1:]]
            self.assertEqual(msgs[0], {"title": "Test", "text": "Starte 3 Programm(e) …",
                                       "done": 0, "total": 3})
            self.assertEqual(msgs[1]["done"], 1)
            self.assertEqual(msgs[2:], [{"keyboard": False}, {"keyboard": True}])
            self.assertEqual(out.getvalue().count("Starte 3"), 1)   # terminal: once
            rec.unlink()
            with mock.patch.dict(os.environ, {"FAKE_CANCEL": "1"}):
                with self.assertRaises(KeyboardInterrupt):
                    with ss.Overlay("Test", True) as ov:
                        ss.busy("lange Arbeit")
                        time.sleep(5)
                self.assertTrue(ov.cancelled)
            self.assertIn("style fullscreen", rec.read_text())
            with mock.patch.dict(os.environ, {"FAKE_UNAVAILABLE": "1"}):
                with ss.Overlay("Test", "fullscreen") as ov:
                    ss.busy("Schritt")              # no gdbus/bus: notification off too
                self.assertEqual(ov.style, "notify")
                self.assertFalse(ov.enabled)
            with mock.patch.dict(os.environ, {"SESSION_SNAP_OVERLAY_PYTHON": "/nonexistent"}):
                ov = ss.Overlay("Test", "fullscreen")
                ov.show("Schritt")
                self.assertEqual(ov.style, "notify")
                ov.close()
        with mock.patch.dict(os.environ, {"WAYLAND_DISPLAY": "", "SESSION_SNAP_OVERLAY": "1"}):
            self.assertEqual(ss.Overlay("x", "fullscreen").style, "notify")
        with mock.patch.dict(os.environ, {"SESSION_SNAP_OVERLAY": "corner",
                                          "WAYLAND_DISPLAY": "w"}):
            self.assertEqual(ss.Overlay("x", "off").style, "corner")
        with mock.patch.dict(os.environ, {"SESSION_SNAP_OVERLAY": "0"}):
            self.assertFalse(ss.Overlay("x", "fullscreen").enabled)
        self.assertFalse(ss.Overlay("x", False).enabled)
        for value, style in (("yes", "fullscreen"), ("ja", "fullscreen"), ("no", "off"),
                             ("off", "off"), ("Corner", "corner"), ("notify", "notify"),
                             ("0", "off")):
            self.assertEqual(ss.parse_overlay(value), style, value)
        self.assertIn("overlay = fullscreen", ss.CONFIG_TEMPLATE)

    def test_59d_repository_samples(self):
        """the repository carries every file a developer needs as a sample: config.ini equals
        the built-in template, programs.ini is readable, the privacy lists hold terms and only
        invented ones, the icon is a file of the package"""
        root = Path(__file__).resolve().parents[1]
        ex = root / "examples"
        self.assertEqual((ex / "config.ini.sample").read_text(encoding="utf-8"),
                         ss.CONFIG_TEMPLATE)
        with mock.patch.object(ss, "PROGRAMS_FILE", ex / "programs.ini.sample"):
            self.assertEqual(set(ss.load_programs()), {"video", "image"})
        for name in ("denylist.sample.txt", "allowlist.sample.txt"):
            lines = [x.strip() for x in (ex / "privacy" / name).read_text().splitlines()
                     if x.strip() and not x.lstrip().startswith("#")]
            self.assertTrue(lines, name)
        self.assertTrue(ss.ICON_FILE.is_file())
        self.assertTrue((root / "docs" / "images" / "social-preview.png").is_file())

    def test_59c_desktop_entry(self):
        """desktop-entry writes the menu entry and the icon below XDG_DATA_HOME, opens the
        menu in cosmic-term (else x-terminal-emulator, else Terminal=true), leaves equal
        files alone and removes both with --remove"""
        data = self.tmp / "xdg-data"
        fake = self.tmp / "termbin"
        fake.mkdir(exist_ok=True)
        env = {"XDG_DATA_HOME": str(data), "PATH": str(fake), "SESSION_SNAP_BIN": "/x/bin"}
        with mock.patch.dict(os.environ, env):
            ss.init_paths()
            self.assertIn("Terminal=true\nCategories", ss.desktop_entry_text())
            self.assertIn("Exec=/x/bin/session-snap\n", ss.desktop_entry_text())
            (fake / "x-terminal-emulator").write_text("#!/bin/sh\n")
            os.chmod(fake / "x-terminal-emulator", 0o755)  # nosec B103 - test stub
            self.assertIn("Exec=x-terminal-emulator -e /x/bin/session-snap\n",
                          ss.desktop_entry_text())
            (fake / "cosmic-term").write_text("#!/bin/sh\n")
            os.chmod(fake / "cosmic-term", 0o755)  # nosec B103 - test stub
            with contextlib.redirect_stdout(io.StringIO()) as out:
                self.assertEqual(ss.main(["desktop-entry"]), 0)
            entry, icon = ss.desktop_entry_paths()
            self.assertEqual(entry, data / "applications" / "session-snap.desktop")
            text = entry.read_text()
            self.assertIn("Exec=cosmic-term -e /x/bin/session-snap\n", text)
            self.assertIn("Terminal=false", text)
            self.assertIn("Icon=session-snap", text)
            self.assertEqual(icon.read_text(), ss.ICON_FILE.read_text())
            self.assertTrue(icon.read_text().startswith("<svg"))
            self.assertEqual(out.getvalue().count("angelegt"), 2)
            with contextlib.redirect_stdout(io.StringIO()) as out:
                ss.cmd_desktop_entry()
            self.assertEqual(out.getvalue().count("unveraendert"), 2)
            with contextlib.redirect_stdout(io.StringIO()) as out:
                ss.main(["desktop-entry", "--remove"])
                ss.cmd_desktop_entry(remove=True)
            self.assertFalse(entry.exists() or icon.exists())
            self.assertEqual(out.getvalue().count("entfernt"), 2)
        ss.init_paths()

    def test_63_manage_snapshots(self):
        """rename keeps the file name and sets the label; archive hides a snapshot from the
        lists (restore/kill), bringing it back shows it again; delete only after 'ja';
        the interactive menu drives the same functions"""
        a = ss.save_snapshot([], "alt")
        time.sleep(1.1)
        b = ss.save_snapshot([], "neu")
        ss.rename_snapshot(a, "Feierabend Freitag")
        self.assertEqual(ss.load_snapshot(a)["label"], "Feierabend Freitag")
        self.assertIn("«Feierabend Freitag»", ss.snapshot_title(a))
        arch = ss.move_snapshot(a, True)
        self.assertEqual(ss.list_snapshots(), [b])
        self.assertEqual(ss.list_archived(), [arch])
        back = ss.move_snapshot(arch, False)
        self.assertEqual(back, a)
        self.assertEqual(ss.list_archived(), [])
        # interactive: choose b (index 0), "Loeschen" (2): first refused, then confirmed
        picks = iter([0, 2, 0, 2, None])
        answers = iter([False, True])
        with mock.patch.object(ss, "menu", lambda *x, **k: next(picks)), \
                contextlib.redirect_stdout(io.StringIO()) as out:
            ss.cmd_manage(ask=lambda q: next(answers))
        self.assertIn("Geloescht", out.getvalue())
        self.assertEqual(ss.list_snapshots(), [a])
        ss.delete_snapshot(a)

    def test_07_key_decoding(self):
        """_getkey(): ESC[B and ESC O B -> down, ESC[6~ -> PgDn, lone Esc -> 27, junk -> -1"""

        class Scr:
            """Minimal curses screen stub that replays queued key codes."""
            def __init__(self, seq):
                self.q = [ord(c) for c in seq]

            def getch(self):
                """Next queued key code, -1 when empty.

                Returns:
                    int: Key code.
                """
                return self.q.pop(0) if self.q else -1

            def nodelay(self, flag):
                """Ignored (curses API stub).

                Args:
                    flag (bool): Ignored.
                """

        self.assertEqual(ss._getkey(Scr("\x1b[B")), curses.KEY_DOWN)
        self.assertEqual(ss._getkey(Scr("\x1bOB")), curses.KEY_DOWN)
        self.assertEqual(ss._getkey(Scr("\x1bOA")), curses.KEY_UP)
        self.assertEqual(ss._getkey(Scr("\x1b[6~")), curses.KEY_NPAGE)
        self.assertEqual(ss._getkey(Scr("\x1b")), 27)
        self.assertEqual(ss._getkey(Scr("\x1b[Z")), -1)
        self.assertEqual(ss._getkey(Scr(" ")), ord(" "))

    def test_08_cli_aliases(self):
        """CLI: '--kill' / '-k' map to the kill subcommand with all options"""
        with mock.patch.object(ss, "cmd_kill", return_value=0) as ck:
            self.run_main("--kill", "-s", "latest", "--all", "--force", "--timeout", "2")
            self.run_main("-k", "--dry-run")
        args = ck.call_args_list[0].args
        self.assertEqual(args[1:], ("latest", True, True, 2.0, False, True))
        self.assertTrue(ck.call_args_list[1].args[5])
        with mock.patch.object(ss, "cmd_kill", return_value=0) as ck:
            self.run_main("kill", "--no-relaunch")
        self.assertFalse(ck.call_args.args[6])

    def test_09_protected_chain(self):
        """protected_pids(): contains this process and its parent"""
        prot = ss.protected_pids()
        self.assertIn(os.getpid(), prot)
        self.assertIn(os.getppid(), prot)

    def test_10_root_guard(self):
        """check_not_root(): exits when the effective UID is 0"""
        with mock.patch.object(ss.os, "geteuid", return_value=0):
            with self.assertRaises(SystemExit):
                ss.check_not_root()


# =========================================================================== integration tests
class T2_Capture(Base):
    """Capturing dummy processes."""

    def test_11_classification(self):
        """capture: VLC via open fd, images via args/URI, rest via .desktop, hidden excluded"""
        self.spawn("hiddenapp", SLEEP)
        es = self.start_standard_set()
        cats = [(e["category"], e["program"], e["files"]) for e in es]
        self.assertEqual(cats, [
            ("vlc", "vlc", [str(self.film)]),
            ("image", "eog", [str(self.png)]),
            ("image", "eog", [str(self.gif)]),
            ("rest", "fakeapp", []),
        ])
        # restart = ORIGINAL program and options, file argument appended (fd-only file)
        self.assertEqual(es[0]["cmd"], [str(self.bin / "vlc"), "-c", HOLD_F, str(self.film)])
        # images: file arguments (relative / file:// URI) replaced by the one absolute path
        self.assertEqual(es[1]["cmd"], [str(self.bin / "eog"), "-c", SLEEP, str(self.png)])
        self.assertEqual(es[2]["cmd"], [str(self.bin / "eog"), "-c", SLEEP, str(self.gif)])
        self.assertTrue(all(e["start"] > 0 for e in es))

    def test_12_child_processes_collapsed(self):
        """capture: a child process with the same binary is not recorded separately"""
        self.spawn("childapp", WITH_CHILD)
        self.wait_for(lambda: len(self._dummy_pids()) == 2, msg="parent + child")
        time.sleep(0.2)
        es = [e for e in self.entries() if e["program"] == "childapp"]
        self.assertEqual(len(es), 1)

    def test_13_save_cli(self):
        """save -y -l LABEL: snapshot file with label and all four entries"""
        self.start_standard_set()
        rc, out = self.run_main("save", "-y", "-l", "Abend")
        self.assertEqual(rc, 0)
        snap = ss.load_snapshot(ss.list_snapshots()[0])
        self.assertEqual(snap["label"], "Abend")
        self.assertEqual(len(snap["entries"]), 4)
        self.assertIn("Gespeichert:", out)


class T3_Restore(Base):
    """Restoring snapshots."""

    def _snapshot_and_stop(self):
        es = self.start_standard_set()
        ss.save_snapshot(es, "t")
        self._kill_all_dummies()
        return es

    def test_14_restore_all(self):
        """restore --all: every entry restarted, identical signatures afterwards"""
        es = self._snapshot_and_stop()
        rc, out = self.run_main("restore", "-s", "latest", "--all")
        self.assertEqual(rc, 0, out)
        self.assertIn("4 gestartet, 0 fehlgeschlagen", out)
        want = {ss.signature(e) for e in es}
        self.wait_for(lambda: want <= {ss.signature(e) for e in self.entries()},
                      msg="all restored")

    def test_15_restore_skips_running(self):
        """restore --all twice: second run starts nothing (already running)"""
        self._snapshot_and_stop()
        self.run_main("restore", "-s", "latest", "--all")
        self.wait_for(lambda: len(self.entries()) == 4, msg="restored")
        rc, out = self.run_main("restore", "-s", "latest", "--all")
        self.assertEqual(rc, 0)
        self.assertIn("Nichts zu starten.", out)

    def test_16_restore_missing_file(self):
        """restore with a deleted file: reported, exit code 1, the others still start"""
        self._snapshot_and_stop()
        moved = self.gif.with_suffix(".bak")
        self.gif.rename(moved)
        try:
            rc, out = self.run_main("restore", "-s", "latest", "--all")
        finally:
            moved.rename(self.gif)
        self.assertEqual(rc, 1)
        self.assertIn("Datei fehlt", out)
        self.assertIn("3 gestartet, 1 fehlgeschlagen", out)

    def test_27_detached_launch(self):
        """launch: program runs in its own session, is no child of the caller; exec errors
        are reported"""
        good, msg = ss.launch({"category": "rest", "program": "fakeapp", "files": [],
                               "cmd": [str(self.bin / "fakeapp"), "-c", SLEEP], "cwd": ""})
        self.assertTrue(good, msg)
        self.wait_for(lambda: len(self._dummy_pids()) == 1, msg="detached dummy")
        pid = self._dummy_pids()[0]
        with open(f"/proc/{pid}/stat", encoding="ascii") as fh:
            fields = fh.read().rsplit(")", 1)[1].split()
        self.assertNotEqual(int(fields[1]), os.getpid())       # not our child
        self.assertNotEqual(int(fields[3]), os.getsid(0))      # other session
        with self.assertRaises(OSError) as cm:
            ss.spawn_detached([str(self.tmp / "no-such-binary")], str(self.tmp))
        self.assertIn("FileNotFoundError", str(cm.exception))
        good, msg = ss.launch({"category": "rest", "program": "x", "files": [],
                               "cmd": ["no-such-program-xyz"], "cwd": ""})
        self.assertFalse(good)
        self.assertIn("Programm nicht gefunden", msg)

    def test_17_restore_dry_run(self):
        """restore --dry-run: commands listed, nothing started"""
        self._snapshot_and_stop()
        rc, out = self.run_main("restore", "-s", "latest", "--all", "--dry-run")
        self.assertEqual(rc, 0)
        self.assertEqual(len(re.findall(r"^\[ *\d+/4\].*DRY-RUN", out, re.M)), 4)
        time.sleep(0.3)
        self.assertEqual(self._dummy_pids(), [])

    def test_18_checklist_via_pty(self):
        """interactive restore, 'Auswahl treffen' (same tree as kill): n = all off, Down to
        Videos (ESC[B), Right unfolds (long path shown completely), Down x2 (ESC O B, ESC[B)
        to Bilder, Right, Down to a.png, Space, Enter -> only a.png is started"""
        self._snapshot_and_stop()
        # curses takes LINES/COLUMNS from the environment before the terminal size;
        # pytest-xdist workers may carry them (COLUMNS=80): the pty size must count
        env = {k: v for k, v in os.environ.items() if k not in ("LINES", "COLUMNS")}
        env["TERM"] = "xterm-256color"
        pid, fd = pty.fork()
        if pid == 0:  # child
            os.execvpe(sys.executable, PROG + ["restore", "-s", "latest"],
                       env)
        fcntl.ioctl(fd, termios.TIOCSWINSZ, struct.pack("HHHH", 30, 100, 0, 0))

        def read(t):
            """Read what the terminal shows until a text appears.

            Args:
                t (float): Seconds to read.

            Returns:
                str: Output read so far.
            """
            data, end = b"", time.monotonic() + t
            while time.monotonic() < end:
                r, _, _ = select.select([fd], [], [], 0.05)
                if r:
                    try:
                        data += os.read(fd, 65536)
                    except OSError:
                        break
            return re.sub(r"\x1b\[[0-9;?]*[A-Za-z]|\x1b[()][A-Z0-9]|\x1b[=>]", "",
                          data.decode("utf-8", "replace"))

        self.assertIn("Auswahl treffen", read(3))
        screen = ""
        for key in (b"2", b"n", b"\x1b[B", b"\x1b[C", b"\x1bOB", b"\x1b[B", b"\x1b[C",
                    b"\x1b[B", b" ", b"\r"):
            os.write(fd, key)
            screen += read(0.6)
        screen += read(2)
        screen += self.wait_child(pid, fd)
        self.assertIn("werden muss.mkv", screen)  # long path shown completely
        for word in str(self.film).split():      # no word cut at the right edge
            self.assertIn(word, screen)
        self.assertIn("Ergebnis: 1 gestartet", screen)
        self.assertIn(str(self.png), screen)
        self.wait_for(lambda: [e["files"] for e in self.entries()] == [[str(self.png)]],
                      msg="only a.png restored")


class T4_Kill(Base):
    """Terminating recorded programs."""

    def _snapshot(self, es):
        ss.save_snapshot(es, "k")

    def test_19_kill_all(self):
        """kill --all: every recorded process ends with SIGTERM, exit code 0"""
        self._snapshot(self.start_standard_set())
        rc, out = self.run_main("kill", "-s", "latest", "--all")
        self.assertEqual(rc, 0, out)
        self.assertIn("3 beendet, 0 laufen weiter", out)  # 4 entries = 3 processes
        self.wait_for(lambda: not self._dummy_pids(), msg="all ended")

    def test_20_kill_dry_run(self):
        """kill --dry-run: nothing is signalled"""
        self._snapshot(self.start_standard_set())
        rc, out = self.run_main("kill", "-s", "latest", "--all", "--dry-run")
        self.assertEqual(rc, 0)
        self.assertEqual(out.count("DRY-RUN"), 3)
        self.assertEqual(len(self._dummy_pids()), 3)

    def test_21_kill_unresponsive(self):
        """SIGTERM ignored by a normal program: without --force exit 1 and alive; with
        --force SIGKILL ends it. A player ignoring SIGTERM is ended with SIGKILL without
        --force and without a question (no unsaved data)."""
        self.spawn("fakeapp", IGNORE_TERM, hold=self.film)
        self.wait_for(lambda: [e["program"] for e in self.entries()] == ["fakeapp"],
                      msg="stubborn fakeapp")
        time.sleep(0.3)  # let the dummy install its SIGTERM handler
        self._snapshot(self.entries())
        rc, out = self.run_main("kill", "-s", "latest", "--all", "--timeout", "0.5")
        self.assertEqual(rc, 1)
        self.assertIn("0 beendet, 1 laufen weiter", out)
        self.assertEqual(len(self._dummy_pids()), 1)
        rc, out = self.run_main("kill", "-s", "latest", "--all", "--timeout", "0.5", "--force")
        self.assertEqual(rc, 0, out)
        self.assertIn("SIGKILL", out)
        self.wait_for(lambda: not self._dummy_pids(), msg="killed")
        self.spawn("vlc", IGNORE_TERM, hold=self.film)
        self.wait_for(lambda: [e["files"] for e in self.entries()] == [[str(self.film)]],
                      msg="stubborn vlc holding the film")
        time.sleep(0.3)
        self._snapshot(self.entries())
        asked = []
        with mock.patch.object(ss, "_ask_yes", lambda q: asked.append(q) or False):
            rc, out = self.run_main("kill", "-s", "latest", "--all", "--timeout", "0.5")
        self.assertEqual(rc, 0, out)
        self.assertIn("keine ungespeicherten Daten", out)
        self.assertEqual(asked, [])
        self.wait_for(lambda: not self._dummy_pids(), msg="vlc killed")

    def test_22_kill_protects_own_chain(self):
        """a process in the caller's own process chain is reported, never signalled"""
        es = self.start_standard_set()
        self._snapshot(es)
        vlc_pid = es[0]["pid"]
        with mock.patch.object(ss, "protected_pids", return_value={vlc_pid}):
            _rc, out = self.run_main("kill", "-s", "latest", "--all")
        self.assertIn("Geschuetzt", out)
        self.assertIn(vlc_pid, self._dummy_pids())

    def test_23_pid_reuse_guard(self):
        """PID identity: a wrong start time means 'already ended' - no signal is sent"""
        es = self.start_standard_set()
        t = dict(es[0], start=es[0]["start"] + 12345)
        self.assertEqual(ss._signal(t, signal.SIGTERM), "bereits beendet")
        time.sleep(0.2)
        self.assertIn(es[0]["pid"], self._dummy_pids())

    def test_24_save_then_kill(self):
        """save --kill -y: snapshot written, then the saved programs are terminated"""
        self.start_standard_set()
        rc, out = self.run_main("save", "-y", "--kill", "-l", "Feierabend")
        self.assertEqual(rc, 0, out)
        self.assertEqual(len(ss.load_snapshot(ss.list_snapshots()[0])["entries"]), 4)
        self.wait_for(lambda: not self._dummy_pids(), msg="all ended")

    def test_25_kill_then_restore_cycle(self):
        """full cycle: save -> kill -> restore yields the same signatures again"""
        es = self.start_standard_set()
        self.run_main("save", "-y")
        self.run_main("kill", "-s", "latest", "--all")
        self.wait_for(lambda: not self._dummy_pids(), msg="ended")
        self.run_main("restore", "-s", "latest", "--all")
        want = {ss.signature(e) for e in es}
        self.wait_for(lambda: want == {ss.signature(e) for e in self.entries()},
                      msg="cycle restored")

    def test_56_kill_checklist_folded_categories(self):
        """interactive kill, 'Auswahl treffen': tree with root 'Alle Programme', categories
        folded (overview with program names), legend at the bottom; Space on the root
        deselects everything, Space on a category selects the whole category, Right
        unfolds it; Enter -> only the selected category is terminated"""
        es = self.start_standard_set()
        self._snapshot(es)
        keep = sorted(e["pid"] for e in es if e["category"] != "image")
        # curses takes LINES/COLUMNS from the environment before the terminal size;
        # pytest-xdist workers may carry them (COLUMNS=80): the pty size must count
        env = {k: v for k, v in os.environ.items() if k not in ("LINES", "COLUMNS")}
        env["TERM"] = "xterm-256color"
        pid, fd = pty.fork()
        if pid == 0:  # child
            os.execvpe(sys.executable, PROG + ["kill", "-s", "latest"],
                       env)
        fcntl.ioctl(fd, termios.TIOCSWINSZ, struct.pack("HHHH", 30, 160, 0, 0))

        def read(t, until=None):
            """Read what the terminal shows, for t seconds or until a text appears.

            Waiting for the text instead of a fixed time keeps the test stable
            when the tests run in parallel (check.sh with pytest-xdist).

            Args:
                t (float): Seconds to read at most.
                until (str | None): Stop as soon as this text was read.

            Returns:
                str: Output read so far, without terminal control sequences.
            """
            data, end = b"", time.monotonic() + t
            while time.monotonic() < end:
                r, _, _ = select.select([fd], [], [], 0.05)
                if r:
                    try:
                        data += os.read(fd, 65536)
                    except OSError:
                        break
                    if until and until in strip(data):
                        break
            return strip(data)

        def strip(data):
            """Decode terminal output and remove its control sequences.

            Args:
                data (bytes): Raw output of the terminal.

            Returns:
                str: Plain text.
            """
            return re.sub(r"\x1b\[[0-9;?]*[A-Za-z]|\x1b[()][A-Z0-9]|\x1b[=>]", "",
                          data.decode("utf-8", "replace"))

        self.assertIn("Auswahl treffen", read(30, "Auswahl treffen"))
        os.write(fd, b"2")
        folded = read(30, "Kurztasten:")                     # the legend is drawn last
        self.assertIn("Alle Programme (4/4 gewaehlt)", folded)   # images count one by one
        self.assertIn("Videos (1/1 gewaehlt)", folded)
        self.assertIn("Bilder (2/2 gewaehlt)", folded)          # 2 images, 1 eog process
        self.assertIn("Kurztasten:", folded)                # legend line 2
        self.assertNotIn("a.png", folded)                   # folded: no file lines yet
        # (curses redraws only changed characters afterwards: outcome is checked instead)
        for key in (b" ",            # root: all off
                    b"\x1b[B", b"\x1b[B",                   # down to header Bilder
                    b" ",            # whole category Bilder on
                    b"\x1b[C"):      # unfold Bilder
            os.write(fd, key)
            screen = read(30, "a.png") if key == b"\x1b[C" else read(0.5)
        self.assertIn("a.png", screen)
        os.write(fd, b"\r")
        screen = read(6)
        screen += self.wait_child(pid, fd)
        self.assertIn("1 beendet", screen)
        self.wait_for(lambda: sorted(self._dummy_pids()) == keep, msg="only eog ended")


class T6_Browser_Programs(Base):
    """Browsers, app windows and program choice."""

    def test_28_browser_capture(self):
        """browsers: own category, restart command keeps only the profile; app window
        (Claude) recorded as separate entry with the launcher's Exec line"""
        self.spawn("chrome", ONE, "--profile-directory=Default", "--app-id=abc123")
        self.spawn("firefox", ONE, "-P", "work", "https://example.org/")
        self.wait_for(lambda: len(self.entries()) == 3, msg="chrome, app, firefox")
        es = {e["program"]: e for e in self.entries()}
        b = str(self.bin)
        self.assertEqual(es["chrome"]["category"], "browser")
        self.assertEqual(es["chrome"]["cmd"], [f"{b}/chrome", "--profile-directory=Default"])
        self.assertTrue(es["Claude"]["app"])
        self.assertEqual(es["Claude"]["cmd"], [f"{b}/chrome", "-c", ONE,
                                               "--profile-directory=Default",
                                               "--app-id=abc123"])
        self.assertEqual(es["firefox"]["cmd"], [f"{b}/firefox", "-P", "work"])

    def test_29_exec_tokens(self):
        """desktop Exec expansion: %f/%U replaced, other codes dropped, path appended if
        no file code, quoting respected"""
        self.assertEqual(ss.exec_tokens('env A=1 prog --x %U', "/a b.mkv"),
                         ["env", "A=1", "prog", "--x", "/a b.mkv"])
        self.assertEqual(ss.exec_tokens('prog %i %c -n', "/f.png"), ["prog", "-n", "/f.png"])
        self.assertEqual(ss.exec_tokens('prog --open=%f 100%%', "/f"), ["prog", "--open=/f",
                                                                        "100%"])
        self.assertEqual(ss.exec_tokens('chrome --app-id=x %U'), ["chrome", "--app-id=x"])

    def test_30_kill_relaunches_app(self):
        """kill: Chrome ends although the app window (Claude) was not selected; the app is
        started again afterwards; --no-relaunch suppresses that"""
        self.spawn("chrome", ONE, "--profile-directory=Default", "--app-id=abc123")
        self.wait_for(lambda: len(self.entries()) == 2, msg="chrome + app")
        old = self._dummy_pids()
        ss.save_snapshot([e for e in self.entries() if not e.get("app")], "nur chrome")
        rc, out = self.run_main("kill", "-s", "latest", "--all")
        self.assertEqual(rc, 0, out)
        self.assertIn("Claude: neu gestartet", out)
        self.wait_for(lambda: self._dummy_pids() and not set(self._dummy_pids()) & set(old),
                      msg="new chrome process")
        rc, out = self.run_main("kill", "-s", "latest", "--all", "--no-relaunch")
        self.assertNotIn("neu gestartet", out)
        self.wait_for(lambda: not self._dummy_pids(), msg="all ended")

    def test_60_app_window_found_through_window_list(self):
        """Claude opened in an ALREADY running Chrome: the process has no --app-id, the app
        is found through its window 'chrome-<id>-Default'; kill starts it again"""
        self.spawn("chrome", ONE, "--profile-directory=Default")
        self.wait_for(lambda: len(self.entries()) == 1, msg="chrome")
        wins = [("chrome-abc123-Default", "Claude"), ("google-chrome", "Neuer Tab")]
        # only the dummies: a real Chrome of the desktop would otherwise host the app window
        with mock.patch.object(ss, "list_windows", return_value=wins), \
                mock.patch.object(ss, "scan_processes", self.only_dummies):
            es = self.entries()
            self.assertEqual(sorted(e["program"] for e in es), ["Claude", "chrome"])
            app = next(e for e in es if e.get("app"))
            self.assertIn("--app-id=abc123", app["cmd"])
            ss.save_snapshot([e for e in es if not e.get("app")], "nur chrome")
            rc, out = self.run_main("kill", "-s", "latest", "--all")
        self.assertEqual(rc, 0, out)
        self.assertIn("Claude: neu gestartet", out)
        self.wait_for(lambda: len(self._dummy_pids()) == 1, msg="relaunched app")

    def test_61_window_names_for_order(self):
        """Alt-Tab order: windows are found under their desktop ID (fake.desktop for
        fakeapp), without '-bin' (firefox-bin -> firefox), as google-chrome for chrome, and
        web apps only under their app id"""
        es = [{"category": "rest", "program": "fakeapp", "files": [],
               "cmd": [str(self.bin / "fakeapp")]},
              {"category": "browser", "program": "firefox-bin", "files": [],
               "cmd": ["/usr/lib/firefox/firefox-bin"]},
              {"category": "browser", "program": "chrome", "files": [],
               "cmd": ["/opt/google/chrome/chrome"]},
              {"category": "browser", "program": "WhatsApp", "files": [], "app": True,
               "cmd": ["/opt/google/chrome/chrome", "--app-id=hnpf"]}]
        wins = [("chrome-hnpf-Default", "WhatsApp"), ("google-chrome", "Tab"),
                ("firefox", "Mozilla Firefox"), ("fake", "Fake")]
        self.assertEqual(ss._match_windows(es, wins), {0: 3, 1: 2, 2: 1, 3: 0})

    def test_62_background_browser_not_running(self):
        """a browser process WITHOUT window (Chrome in the background) does not count as
        running for restore; with its window it does"""
        self.spawn("chrome", ONE, "--profile-directory=Default")
        self.wait_for(lambda: len(self.entries()) == 1, msg="chrome")
        sig = ss.signature(self.entries()[0])
        with self.isolated():
            with mock.patch.object(ss, "list_windows", return_value=[("firefox", "x")]):
                self.assertNotIn(sig, ss.running_signatures(self.cfg))
            with mock.patch.object(ss, "list_windows", return_value=[("google-chrome", "T")]):
                self.assertIn(sig, ss.running_signatures(self.cfg))
            with mock.patch.object(ss, "list_windows", return_value=None):
                self.assertIn(sig, ss.running_signatures(self.cfg))

    def test_64_chrome_identity_ignores_profile_flag(self):
        """snapshot taken while Chrome ran as plain 'chrome'; now Chrome was started through
        an app (--profile-directory=Default --app-id=...): kill must still end it (with the
        app windows inside). A different --user-data-dir is a different Chrome."""
        plain = {"category": "browser", "program": "chrome", "files": [],
                 "cmd": [str(self.bin / "chrome")]}
        viaapp = dict(plain, cmd=[str(self.bin / "chrome"), "--profile-directory=Default"])
        other = dict(plain, cmd=[str(self.bin / "chrome"), "--user-data-dir=/x/claude"])
        self.assertEqual(ss.signature(plain), ss.signature(viaapp))
        self.assertNotEqual(ss.signature(plain), ss.signature(other))
        self.spawn("chrome", ONE, "--profile-directory=Default", "--app-id=abc123")
        self.wait_for(lambda: len(self.entries()) == 2, msg="chrome + app")
        ss.save_snapshot([plain], "chrome ohne Optionen")
        rc, out = self.run_main("kill", "-s", "latest", "--all", "--no-relaunch")
        self.assertEqual(rc, 0, out)
        self.assertIn("1 beendet", out)
        self.wait_for(lambda: not self._dummy_pids(), msg="chrome ended")

    def test_65_chrome_session_restore(self):
        """save remembers the open Chrome profiles (Local State); restore starts Chrome
        without --profile-directory and with --restore-last-session, before app windows;
        an app window the session already opened is not started a second time"""
        data = self.tmp / "chrome-data"
        data.mkdir(exist_ok=True)
        (data / "Local State").write_text(json.dumps({"profile": {
            "last_active_profiles": ["Default", "Profile 1"],
            "info_cache": {"Default": {"name": "Lorem"}, "Profile 1": {"name": "Ipsum"}}}}))
        self.assertEqual(ss.chrome_profiles(data), [("Default", "Lorem"),
                                                    ("Profile 1", "Ipsum")])
        br = {"category": "browser", "program": "chrome", "files": [], "pid": 0,
              "cmd": [str(self.bin / "chrome"), f"--user-data-dir={data}",
                      "--profile-directory=Default"]}
        ss.annotate_browser_sessions([br])
        self.assertEqual([p["name"] for p in br["browser_profiles"]], ["Lorem", "Ipsum"])
        with mock.patch.object(ss, "end_background_browser", return_value=False), \
                contextlib.redirect_stdout(io.StringIO()) as out:
            res = ss.prepare_browser_sessions([br])
        self.assertEqual(res[0]["cmd"], [str(self.bin / "chrome"), f"--user-data-dir={data}",
                                         "--restore-last-session"])
        self.assertIn("Lorem, Ipsum", out.getvalue())
        # app window already opened by the session: not launched again
        app = {"category": "browser", "program": "Claude", "files": [], "app": True,
               "cmd": [str(self.bin / "chrome"), "--app-id=abc123"], "cwd": ""}
        started = []
        with mock.patch.object(ss, "launch", lambda e, ref=False: started.append(e["program"])
                               or (True, "gestartet")), \
                mock.patch.object(ss, "list_windows",
                                  return_value=[("chrome-abc123-Default", "Claude")]), \
                mock.patch.object(ss, "session_look_env", return_value=None), \
                contextlib.redirect_stdout(io.StringIO()) as out:
            ss.run_launches([res[0], app], 0)
        self.assertEqual(started, ["chrome"])
        self.assertIn("von der Browser-Sitzung bereits geoeffnet", out.getvalue())

    def test_66_firefox_session_restore(self):
        """the profile of a running Firefox is found through its open .parentlock; while it
        runs prefs.js is not touched; afterwards resume_session_once is set once (old value
        replaced, other prefs kept, backup of prefs.js)"""
        prof = self.tmp / "ff-profile.default"
        prof.mkdir(exist_ok=True)
        (prof / ".parentlock").write_text("")
        (prof / "prefs.js").write_text('user_pref("a.b", 1);\n'
                                       f'user_pref("{ss.RESUME_PREF}", false);\n')
        p = self.spawn("firefox", HOLD_F, hold=prof / ".parentlock")
        self.wait_for(lambda: ss.firefox_profile_dir(p.pid) == str(prof), msg="lock open")
        self.assertIn("laeuft", ss.firefox_set_resume(str(prof)))
        self._kill_all_dummies()
        self.assertEqual(ss.firefox_set_resume(str(prof)), "")
        text = (prof / "prefs.js").read_text()
        self.assertIn('user_pref("a.b", 1);', text)
        self.assertEqual(text.count(ss.RESUME_PREF), 1)
        self.assertIn(f'user_pref("{ss.RESUME_PREF}", true);', text)
        self.assertTrue(any(b.name.startswith("firefox-prefs ff-profile.default")
                            for b in ss.BACKUP_DIR.iterdir()))
        e = {"category": "browser", "program": "firefox", "files": [], "cmd": ["firefox"],
             "browser_profiles": [{"dir": str(prof), "name": "default"}]}
        with contextlib.redirect_stdout(io.StringIO()) as out:
            ss.prepare_browser_sessions([e])
        self.assertIn("Firefox-Sitzung default: wird wiederhergestellt", out.getvalue())

    def test_31_programs_cli(self):
        """programs: lists installed handlers per type, saves and resets the choice"""
        rc, out = self.run_main("programs", "--video", "fakeplayer.desktop")
        self.assertEqual(rc, 0)
        self.assertEqual(ss.load_programs(), {"video": "fakeplayer.desktop"})
        self.assertIn("Fake Player (fakeplayer.desktop)", out)
        self.assertIn("fakeviewer.desktop", out)
        names = [a["id"] for a, n in ss.handlers_for(["video/x-matroska"])]
        self.assertIn("fakeplayer.desktop", names)
        self.assertNotIn("fakeviewer.desktop", names)
        self.run_main("programs", "--reset")
        self.assertEqual(ss.load_programs(), {})

    def test_32_restore_with_other_program(self):
        """restore --video/--image: files open in the chosen programs; a second restore
        sees them as running (signature independent of the program)"""
        es = self.start_standard_set()
        ss.save_snapshot(es, "t")
        self._kill_all_dummies()
        rc, out = self.run_main("restore", "-s", "latest", "--all", "--video",
                                "fakeplayer.desktop", "--image", "fakeviewer.desktop")
        self.assertEqual(rc, 0, out)
        self.assertIn("[mit fakeplayer.desktop]", out)
        self.assertIn("[mit fakeviewer.desktop]", out)
        self.wait_for(lambda: {e["program"] for e in self.entries()}
                      == {"fakeplayer", "fakeviewer", "fakeapp"}, msg="other programs")
        rc, out = self.run_main("restore", "-s", "latest", "--all")
        self.assertIn("Nichts zu starten.", out)

    def test_33_saved_program_and_unknown_program(self):
        """saved choice in programs.ini is used; an unknown desktop ID falls back to the
        recorded program with a warning"""
        ss.save_programs({"image": "does-not-exist.desktop", "video": "fakeplayer.desktop"})
        es = [{"category": "vlc", "program": "vlc", "cmd": ["/v", str(self.film)],
               "files": [str(self.film)]},
              {"category": "image", "program": "eog", "cmd": ["/e", str(self.png)],
               "files": [str(self.png)]}]
        out, warn = ss.apply_programs(es, ss.load_programs())
        self.assertEqual(out[0]["cmd"][0], str(self.bin / "fakeplayer"))
        self.assertEqual(out[0]["cmd"][-1], str(self.film))
        self.assertEqual(out[1]["cmd"], ["/e", str(self.png)])
        self.assertTrue(warn and "does-not-exist.desktop" in warn[0])


class T7_Exact_Program(Base):
    """Exact restart command and player options."""

    def test_34_file_cmd(self):
        """file_cmd: original options and `--` kept, file/URL arguments replaced by the one
        file at the position of the first file argument; appended if none"""
        argv = ["mpv", "--player-operation-mode=pseudo-gui", "--", "file:///a%20b.mkv",
                "/c.mkv", "https://x.org/v"]
        self.assertEqual(ss.file_cmd(["/usr/bin/mpv"], argv, {3, 4}, "/c.mkv"),
                         ["/usr/bin/mpv", "--player-operation-mode=pseudo-gui", "--", "/c.mkv"])
        self.assertEqual(ss.file_cmd(["/usr/bin/vlc"], ["vlc", "--started-from-file"], set(),
                                     "/v.mkv"), ["/usr/bin/vlc", "--started-from-file", "/v.mkv"])

    def test_35_original_options_survive_cycle(self):
        """capture -> kill -> restore: the restored process has exactly the original program
        and options (here: a URI argument after `--`), not only the bare file"""
        uri = "file://" + quote(str(self.film))
        self.spawn("fakeplayer", HOLD_F, "--", uri)
        self.wait_for(lambda: [e["files"] for e in self.entries()] == [[str(self.film)]],
                      msg="fakeplayer with film")
        e = self.entries()[0]
        self.assertEqual(e["argv"][-2:], ["--", uri])
        self.assertEqual(e["cmd"][-2:], ["--", str(self.film)])
        ss.save_snapshot([e], "t")
        self.run_main("kill", "-s", "latest", "--all")
        self.wait_for(lambda: not self._dummy_pids(), msg="ended")
        rc, out = self.run_main("restore", "-s", "latest", "--all")
        self.assertEqual(rc, 0, out)
        self.wait_for(lambda: len(self._dummy_pids()) == 1, msg="restored")
        with open(f"/proc/{self._dummy_pids()[0]}/cmdline", "rb") as fh:
            argv = [a.decode() for a in fh.read().rstrip(b"\0").split(b"\0")]
        self.assertEqual(argv, [str(self.bin / "fakeplayer"), "-B", "-c", HOLD_F, "--",
                                str(self.film)])

    def test_36_player_options(self):
        """start options: inserted after the binary (or after `flatpak run APP`), not
        duplicated, only for videos; --play suppresses them"""
        popts = {"mpv": ["--pause", "--window-maximized=yes"], "vlc": ["--start-paused"]}
        es = [{"category": "vlc", "program": "mpv", "files": ["/v.mkv"],
               "cmd": ["/usr/bin/mpv", "--pause", "--", "/v.mkv"]},
              {"category": "vlc", "program": "org.videolan.VLC", "files": ["/v.mkv"],
               "cmd": ["flatpak", "run", "org.videolan.VLC", "/v.mkv"]},
              {"category": "image", "program": "mpv", "files": ["/a.png"],
               "cmd": ["/usr/bin/mpv", "/a.png"]}]
        out = ss.apply_player_options(es, popts)
        self.assertEqual(out[0]["cmd"], ["/usr/bin/mpv", "--window-maximized=yes", "--pause",
                                         "--", "/v.mkv"])
        self.assertEqual(out[1]["cmd"], ["flatpak", "run", "org.videolan.VLC",
                                         "--start-paused", "/v.mkv"])
        self.assertEqual(out[2]["cmd"], ["/usr/bin/mpv", "/a.png"])
        self.spawn("fakeplayer", HOLD_F, str(self.film))
        self.wait_for(lambda: len(self.entries()) == 1, msg="fakeplayer")
        ss.save_snapshot(self.entries(), "t")
        self._kill_all_dummies()
        _rc, out = self.run_main("restore", "-s", "latest", "--all", "--dry-run")
        self.assertIn("-B -c", out)
        _rc, out = self.run_main("restore", "-s", "latest", "--all", "--dry-run", "--play")
        self.assertNotIn("-B", out)

    def test_37_launched_from(self):
        """origin of a process: GIO_LAUNCHED_DESKTOP_FILE (GLib launchers) and systemd unit
        names app[-<launcher>]-<ID>-<RANDOM>.scope are mapped to the desktop file ID"""
        os.environ["GIO_LAUNCHED_DESKTOP_FILE"] = str(self.tmp / "data/applications/x.desktop")
        try:
            p = self.spawn("fakeplayer", HOLD_F, str(self.film))
        finally:
            del os.environ["GIO_LAUNCHED_DESKTOP_FILE"]
        self.wait_for(lambda: len(self.entries()) == 1, msg="fakeplayer")
        self.assertEqual(ss.launched_from(p.pid, 0, set()),
                         ("x.desktop", "GIO_LAUNCHED_DESKTOP_FILE"))
        ids = {"fakeplayer.desktop", "org.gnome.Loupe.desktop"}
        for unit, want in (("app-cosmic-fakeplayer-1a2b.scope", "fakeplayer.desktop"),
                           ("app-gnome-org.gnome.Loupe-4711.scope", "org.gnome.Loupe.desktop"),
                           ("session-2.scope", "")):
            with mock.patch.object(ss, "_systemd_unit", return_value=unit), \
                    mock.patch.object(ss, "_read_environ", return_value={}):
                self.assertEqual(ss.launched_from(1, 0, ids)[0], want, unit)

    def test_38_unreadable_xdg_dir_and_diagnose(self):
        """an unreadable XDG_DATA_DIRS entry is skipped (no crash); diagnose reports
        recorded processes and the reason why others are not recorded"""
        locked = self.tmp / "locked"
        (locked / "applications").mkdir(parents=True)
        locked.chmod(0)
        old = os.environ["XDG_DATA_DIRS"]
        os.environ["XDG_DATA_DIRS"] = f"{locked}:{old}"
        try:
            self.spawn("vlc", HOLD_F, hold=self.film)
            self.spawn("hiddenapp", HOLD_F, hold=self.png)
            self.wait_for(lambda: len(self.entries()) == 1, msg="vlc")
            time.sleep(0.3)
            rc, out = self.run_main("diagnose")
        finally:
            os.environ["XDG_DATA_DIRS"] = old
            locked.chmod(0o755)
        self.assertEqual(rc, 0)
        self.assertIn("ERFASST als Videos", out)
        self.assertIn("NICHT erfasst: kein sichtbarer .desktop-Eintrag", out)


class FakeCosmic:
    """Tiny Wayland server speaking the wire protocol of wl_display/wl_registry and of
    zcosmic_toplevel_info_v1 (v1) / zcosmic_toplevel_manager_v1 as defined in the
    cosmic-protocols XML files. Windows are added with add_window(); close removes a
    window (closed event) unless it carries keep_open; set_maximized and
    set_fullscreen(toplevel, null) requests are recorded and answered with a state event
    containing 0 (maximized) or 3 (fullscreen). Like cosmic-comp, fullscreen is executed
    without being announced as a capability."""

    def __init__(self, path, caps=(1, 2, 3, 4)):
        self.path, self.caps = path, caps
        self.srv = socket.socket(socket.AF_UNIX)
        self.srv.bind(path)
        self.srv.listen(4)
        self.conns, self.windows, self.maximized, self.activated = [], [], [], []
        self.fullscreen, self.closed = [], []
        self.lock = threading.Lock()
        self.stop = False
        self.t = threading.Thread(target=self._accept, daemon=True)
        self.t.start()

    @staticmethod
    def _str(v):
        b = v.encode() + b"\0"
        return struct.pack("=I", len(b)) + b + b"\0" * (-len(b) % 4)

    @staticmethod
    def _msg(obj, op, payload=b""):
        return struct.pack("=II", obj, (8 + len(payload)) << 16 | op) + payload

    def _accept(self):
        self.srv.settimeout(0.2)
        while not self.stop:
            try:
                c, _ = self.srv.accept()
            except OSError:
                continue
            st = {"sock": c, "types": {1: "display"}, "info": None, "next": 0xFF000000,
                  "handles": {}}
            with self.lock:
                self.conns.append(st)
            threading.Thread(target=self._serve, args=(st,), daemon=True).start()

    def _send(self, st, data):
        try:
            st["sock"].sendall(data)
        except OSError:
            pass

    def _states(self, win):
        vals = [2] if win.get("active") else []
        return struct.pack("=I", 4 * len(vals)) + struct.pack(f"={len(vals)}I", *vals)

    def _announce(self, st, win):
        hid, st["next"] = st["next"], st["next"] + 1
        st["handles"][hid] = win
        self._send(st, self._msg(st["info"], 0, struct.pack("=I", hid))
                   + self._msg(hid, 3, self._str(win["app_id"]))
                   + self._msg(hid, 2, self._str(win["title"]))
                   + self._msg(hid, 8, self._states(win)) + self._msg(hid, 1))

    def add_window(self, app_id, title, active=False):
        """Announce a new toplevel window to the connected clients.

        Args:
            app_id (str): Application id of the window.
            title (str): Window title.
            active (bool): Mark it as the active window.

        Returns:
            int: Handle of the new window.
        """
        win = {"app_id": app_id, "title": title}
        with self.lock:
            self.windows.append(win)
            for st in self.conns:
                if st["info"]:
                    self._announce(st, win)
        if active:
            self.activate(win)
        return win

    def activate(self, win):
        """the user (or a client) activates a window: state events to every client

        Args:
            win (int): Handle of the window.
        """
        with self.lock:
            for w in self.windows:
                w["active"] = w is win
            for st in self.conns:
                for hid, w in st["handles"].items():
                    self._send(st, self._msg(hid, 8, self._states(w)) + self._msg(hid, 1))

    def remove(self, win):
        """The window closes: a closed event to every client that knows it.

        Args:
            win (dict): The window.
        """
        with self.lock:
            if win in self.windows:
                self.windows.remove(win)
            for st in self.conns:
                for hid, w in list(st["handles"].items()):
                    if w is win:
                        self._send(st, self._msg(hid, 0))
                        del st["handles"][hid]

    def _serve(self, st):
        buf, c = b"", st["sock"]
        names = {1: "zcosmic_toplevel_info_v1", 2: "zcosmic_toplevel_manager_v1", 3: "wl_seat"}
        while not self.stop:
            try:
                d = c.recv(4096)
            except OSError:
                return
            if not d:
                return
            buf += d
            while len(buf) >= 8 and len(buf) >= struct.unpack_from("=I", buf, 4)[0] >> 16:
                obj, word = struct.unpack_from("=II", buf)
                size, op = word >> 16, word & 0xFFFF
                p, buf = buf[8:size], buf[size:]
                typ = st["types"].get(obj)
                if typ == "display" and op == 1:              # get_registry
                    reg = struct.unpack_from("=I", p)[0]
                    st["types"][reg] = "registry"
                    for n, iface in names.items():
                        self._send(st, self._msg(reg, 0, struct.pack("=I", n)
                                                 + self._str(iface) + struct.pack("=I", 1)))
                elif typ == "display" and op == 0:            # sync
                    self._send(st, self._msg(struct.unpack_from("=I", p)[0], 0,
                                             struct.pack("=I", 0)))
                elif typ == "registry" and op == 0:           # bind
                    n = struct.unpack_from("=I", p)[0]
                    ln = struct.unpack_from("=I", p, 4)[0]
                    off = 8 + ln + (-ln % 4)
                    _ver, oid = struct.unpack_from("=II", p, off)
                    st["types"][oid] = names[n]
                    if n == 1:
                        st["info"] = oid
                        with self.lock:
                            for win in self.windows:
                                self._announce(st, win)
                    elif n == 3:
                        pass
                    else:
                        caps = struct.pack(f"={len(self.caps)}I", *self.caps)
                        self._send(st, self._msg(oid, 0, struct.pack("=I", len(caps)) + caps))
                elif typ == "zcosmic_toplevel_manager_v1" and op == 1:   # close
                    win = st["handles"].get(struct.unpack_from("=I", p)[0])
                    if win is not None and not win.get("keep_open"):
                        self.closed.append((win["app_id"], win["title"]))
                        threading.Thread(target=self.remove, args=(win,)).start()
                elif typ == "zcosmic_toplevel_manager_v1" and op == 2:   # activate
                    win = st["handles"].get(struct.unpack_from("=I", p)[0])
                    if win is not None:
                        self.activated.append((win["app_id"], win["title"]))
                        threading.Thread(target=self.activate, args=(win,)).start()
                elif typ == "zcosmic_toplevel_manager_v1" and op == 3:   # set_maximized
                    hid = struct.unpack_from("=I", p)[0]
                    win = st["handles"].get(hid)
                    if win is not None:
                        self.maximized.append(win["app_id"])
                        self._send(st, self._msg(hid, 8, struct.pack("=II", 4, 0))
                                   + self._msg(hid, 1))
                elif typ == "zcosmic_toplevel_manager_v1" and op == 7:   # set_fullscreen
                    hid, output = struct.unpack_from("=II", p)
                    win = st["handles"].get(hid)
                    if win is not None and output == 0:
                        self.fullscreen.append(win["app_id"])
                        self._send(st, self._msg(hid, 8, struct.pack("=II", 4, 3))
                                   + self._msg(hid, 1))

    def close(self):
        """Stop the fake compositor and remove its socket."""
        self.stop = True
        for st in self.conns:
            st["sock"].close()
        self.srv.close()


@unittest.skipIf(IS_ROOT, "must run as normal user (the tool refuses root)")
class T8_Maximize(unittest.TestCase):
    """Window modes against a simulated COSMIC compositor."""

    def setUp(self):
        self.dir = Path(tempfile.mkdtemp(prefix="ssnap-wl-"))
        self.env = mock.patch.dict(os.environ, {"XDG_RUNTIME_DIR": str(self.dir),
                                                "WAYLAND_DISPLAY": "wayland-fake"})
        self.env.start()

    def tearDown(self):
        self.env.stop()
        shutil.rmtree(self.dir, ignore_errors=True)

    def test_89_close_single_image_windows(self):
        """COSMIC close request: the chosen image windows close, the others stay; a window
        that refuses or is missing is reported; without the capability or a compositor no
        single window can be closed"""
        srv = FakeCosmic(str(self.dir / "wayland-fake"))
        try:
            self.assertTrue(ss.window_close_available())
            for i in range(3):
                srv.add_window("org.gnome.eog", f"b{i}.png")
            stuck = srv.add_window("org.gnome.eog", "b3.png")
            stuck["keep_open"] = True
            imgs = [_e(ss.CAT_IMG, "eog", 100, [f"/p/b{i}.png"]) for i in range(5)]
            target = dict(imgs[0], entries=imgs[:2])
            out = io.StringIO()
            with contextlib.redirect_stdout(out):
                rc = ss.close_image_windows([target], 3.0)
            self.assertEqual(rc, 0, out.getvalue())
            self.assertEqual(sorted(srv.closed), [("org.gnome.eog", "b0.png"),
                                                  ("org.gnome.eog", "b1.png")])
            self.assertEqual([w["title"] for w in srv.windows], ["b2.png", "b3.png"])
            with contextlib.redirect_stdout(out):
                rc = ss.close_image_windows([dict(imgs[0], entries=imgs[3:5])], 0.5)
            self.assertEqual(rc, 1)
            self.assertIn("window still open   eog (PID 100): b3.png", out.getvalue())
            self.assertIn("no window found     eog (PID 100): b4.png", out.getvalue())
            with contextlib.redirect_stdout(out):
                ss.close_image_windows([target], 1.0, dry_run=True)
            self.assertIn("DRY-RUN  close window  eog (PID 100): b1.png", out.getvalue())
        finally:
            srv.close()
        os.environ["WAYLAND_DISPLAY"] = "wayland-nocap"
        srv = FakeCosmic(str(self.dir / "wayland-nocap"), caps=(2, 3))
        try:
            self.assertFalse(ss.window_close_available())
        finally:
            srv.close()
        os.environ["WAYLAND_DISPLAY"] = "wayland-none"
        self.assertFalse(ss.window_close_available())
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            self.assertEqual(ss.close_image_windows([target], 0.5), 1)
        self.assertIn("Closing image windows failed", out.getvalue())

    def test_39_parse_and_match(self):
        """config 'maximize' parsing and app_id matching (mpv, vlc, org.gnome.eog,
        org.nomacs.ImageLounge, desktop IDs)"""
        self.assertEqual(ss.parse_maximize("videos, images"), {"vlc", "image"})
        self.assertEqual(ss.parse_maximize("all"), {"vlc", "image", "browser", "rest"})
        self.assertEqual(ss.parse_maximize("none"), set())
        self.assertTrue(ss.app_matches("mpv", {"mpv"}))
        self.assertTrue(ss.app_matches("org.gnome.eog", {"eog"}))
        self.assertTrue(ss.app_matches("nomacs", {"org.nomacs.ImageLounge.desktop", "x"}))
        self.assertFalse(ss.app_matches("firefox", {"mpv", "vlc"}))

    def test_40_maximize_only_new_matching_windows(self):
        """WindowMaximizer: existing windows and foreign programs are left alone; new
        windows of the started programs are maximized and confirmed by the compositor"""
        srv = FakeCosmic(str(self.dir / "wayland-fake"))
        try:
            srv.add_window("mpv", "alt.mkv - mpv")                # existed before
            m = ss.WindowMaximizer()
            srv.add_window("mpv", "neu.mkv - mpv")
            srv.add_window("firefox", "Startseite")
            srv.add_window("vlc", "neu2.mkv - VLC media player")
            res = m.run({"mpv": 1, "vlc|org.videolan.VLC": 1}, timeout=5)
        finally:
            srv.close()
        self.assertEqual(sorted(srv.maximized), ["mpv", "vlc"])
        self.assertEqual(sorted((a, ok) for a, _t, ok in res), [("mpv", True), ("vlc", True)])

    def test_51_watch_records_alt_tab_order(self):
        """session-snap watch follows activations (most recent last) and writes order.json;
        while it runs, watcher_alive() is true; afterwards false"""
        srv = FakeCosmic(str(self.dir / "wayland-fake"))
        store = self.dir / "store"
        with mock.patch.dict(os.environ, {"SESSION_SNAP_DIR": str(store)}):
            ss.init_paths()
            try:
                a = srv.add_window("mpv", "A.mkv - mpv")
                b = srv.add_window("eog", "B.png")
                c = srv.add_window("vlc", "C.mkv - VLC media player", active=True)
                t = threading.Thread(target=ss.cmd_watch, kwargs={"run_for": 2.5})
                t.start()
                time.sleep(0.6)
                self.assertTrue(ss.watcher_alive())
                srv.activate(a)
                time.sleep(0.3)
                srv.activate(b)
                time.sleep(0.3)
                srv.add_window("firefox", "Startseite")       # new, never activated
                t.join()
                self.assertFalse(ss.watcher_alive())
                data = json.loads((store / "order.json").read_text())
                titles = [w["title"] for w in data["windows"]]
                self.assertEqual(titles, ["C.mkv - VLC media player", "A.mkv - mpv", "B.png",
                                          "Startseite"])
                del c
            finally:
                srv.close()
                ss.init_paths()

    def test_52_save_order_and_restore_reorder(self):
        """save assigns the Alt-Tab position to the entries (watcher: real order, else
        creation order with the active window last); restore activates oldest first;
        windows outside the snapshot are not touched"""
        entries = [
            {"category": "vlc", "program": "mpv", "files": ["/v/A.mkv"],
             "cmd": ["mpv", "/v/A.mkv"]},
            {"category": "image", "program": "eog", "files": ["/b/B.png"],
             "cmd": ["eog", "/b/B.png"]},
            {"category": "vlc", "program": "vlc", "files": ["/v/C.mkv"],
             "cmd": ["vlc", "/v/C.mkv"]},
            {"category": "image", "program": "eog", "files": ["/b/D.png"],
             "cmd": ["eog", "/b/D.png"]}]
        wins = [("mpv", "A.mkv - mpv", False), ("eog", "B.png", True),
                ("vlc", "C.mkv - VLC media player", False), ("eog", "D.png", False),
                ("firefox", "Startseite", False)]
        with mock.patch.object(ss, "watcher_alive", return_value=False):
            src = ss.assign_order(entries, wins)
        self.assertEqual(src, "creation")
        self.assertEqual([e["order"] for e in entries], [0, 4, 1, 2])   # B was active: front
        # watcher order wins over creation order
        store = self.dir / "store"
        store.mkdir()
        (store / "order.json").write_text(json.dumps({"windows": [
            {"app_id": "eog", "title": "D.png"}, {"app_id": "mpv", "title": "A.mkv - mpv"},
            {"app_id": "vlc", "title": "C.mkv - VLC media player"},
            {"app_id": "eog", "title": "B.png"}]}))
        with mock.patch.dict(os.environ, {"SESSION_SNAP_DIR": str(store)}):
            ss.init_paths()
            try:
                with mock.patch.object(ss, "watcher_alive", return_value=True):
                    self.assertEqual(ss.assign_order(entries, wins), "watch")
            finally:
                ss.init_paths()
        self.assertEqual([e["order"] for e in entries], [2, 4, 3, 1])  # firefox: oldest (0)
        srv = FakeCosmic(str(self.dir / "wayland-fake"))
        try:
            for app, title, _act in wins:
                srv.add_window(app, title)
            done = ss.reorder_windows(entries, timeout=3, settle=1, pause=0)
        finally:
            srv.close()
        want = [("eog", "D.png"), ("mpv", "A.mkv - mpv"), ("vlc", "C.mkv - VLC media player"),
                ("eog", "B.png")]
        self.assertEqual(done, want)
        self.assertEqual(srv.activated, want)                         # firefox untouched

    def test_41_no_cosmic(self):
        """without the COSMIC globals or the maximize capability: clear error, no crash;
        without WAYLAND_DISPLAY: clear error"""
        srv = FakeCosmic(str(self.dir / "wayland-fake"), caps=(1, 2))
        try:
            with self.assertRaises(ss.WaylandError) as cm:
                ss.WindowMaximizer()
        finally:
            srv.close()
        self.assertIn("Maximieren", str(cm.exception))
        with mock.patch.dict(os.environ, {"WAYLAND_DISPLAY": ""}):
            with self.assertRaises(ss.WaylandError):
                ss.WindowMaximizer()

    def test_53_fullscreen_images_maximize_videos(self):
        """defaults: images fullscreen, videos maximized; 'fullscreen' wins over 'maximize';
        set_fullscreen (request 7, output null) works without an announced capability"""
        cfgf = self.dir / "cfg.ini"
        cfgf.write_text("[session-snap]\nmaximize = videos, images\n")
        with mock.patch.object(ss, "CONFIG_FILE", cfgf):
            c = ss.load_config()
        self.assertEqual(c["fullscreen"], {"image"})
        self.assertEqual(c["maximize"], {"vlc", "image"})
        img = {"category": "image"}
        vid = {"category": "vlc"}
        self.assertEqual(ss.window_mode(img, c["maximize"], c["fullscreen"]), ss.MODE_FULL)
        self.assertEqual(ss.window_mode(vid, c["maximize"], c["fullscreen"]), ss.MODE_MAX)
        self.assertIsNone(ss.window_mode({"category": "rest"}, c["maximize"], c["fullscreen"]))
        self.assertEqual(ss._window_modes_info(c), "Vollbild: Bilder; maximiert: Videos")
        srv = FakeCosmic(str(self.dir / "wayland-fake"), caps=(1, 2))   # no maximize cap
        try:
            m = ss.WindowMaximizer(need_maximize=False)
            srv.add_window("eog", "Foto 1.png - Bildbetrachter")
            res = m.run({"eog": [ss.MODE_FULL]}, timeout=5)
        finally:
            srv.close()
        self.assertEqual(srv.fullscreen, ["eog"])
        self.assertEqual(srv.maximized, [])
        self.assertEqual([(a, mode, ok) for a, _t, mode, ok in res],
                         [("eog", ss.MODE_FULL, True)])
        srv = FakeCosmic(str(self.dir / "wayland-fake2"))
        with mock.patch.dict(os.environ, {"WAYLAND_DISPLAY": "wayland-fake2"}):
            try:
                m = ss.WindowMaximizer()
                srv.add_window("vlc", "A.mkv - VLC media player")
                srv.add_window("eog", "B.png - Bildbetrachter")
                res = m.run({"vlc": [ss.MODE_MAX], "eog": [ss.MODE_FULL]}, timeout=5)
            finally:
                srv.close()
        self.assertEqual((srv.maximized, srv.fullscreen), (["vlc"], ["eog"]))
        self.assertTrue(all(ok for *_x, ok in res))


XBEL = """<?xml version="1.0" encoding="UTF-8"?>
<xbel version="1.0" xmlns:bookmark="http://www.freedesktop.org/standards/desktop-bookmarks"
      xmlns:mime="http://www.freedesktop.org/standards/shared-mime-info">
{items}
</xbel>
"""
XBEL_ITEM = """  <bookmark href="{href}" added="{t}" modified="{t}" visited="{t}">
    <info><metadata owner="http://freedesktop.org"><mime:mime-type type="image/png"/>
      <bookmark:applications>
        <bookmark:application name="{app}" exec="&apos;{prog} %u&apos;" modified="{t}" count="1"/>
      </bookmark:applications></metadata></info>
  </bookmark>"""


def write_xbel(data_home, items):
    """items: [(path, program, ISO time)]

    Args:
        data_home (Path): XDG data folder of the test.
        items (list): (path, program, ISO time) per file.
    """
    body = "\n".join(XBEL_ITEM.format(href="file://" + quote(str(f)), app="Image Viewer",
                                      prog=prog, t=t) for f, prog, t in items)
    (Path(data_home) / "recently-used.xbel").write_text(XBEL.format(items=body))


class T9_Single_Instance(Base):
    """Single-instance image viewers (eog): several windows, one process, one file in argv."""

    def _photos(self, n, sub="Urlaub 2026") -> list[Path]:
        d = self.tmp / sub
        d.mkdir(exist_ok=True)
        out: list[Path] = []
        for i in range(1, n + 1):
            f = d / f"Foto {i}.png"
            f.write_text(SLEEP)
            out.append(f)
        return out

    def test_42_titles_and_recent(self):
        """window title parsing; recently-used.xbel parsing (newest first, program names)"""
        self.assertEqual(ss.title_candidates("Foto 2.png")[0], "Foto 2.png")
        self.assertIn("Foto 2.png", ss.title_candidates("Foto 2.png - nomacs - Image Lounge"))
        self.assertIn("Foto 2.png", ss.title_candidates("*Foto 2.png (2/5)"))
        self.assertIn("b.gif", ss.title_candidates("b.gif — Loupe"))
        photos = self._photos(2, "xbel")
        f1, f2 = photos[0], photos[1]
        write_xbel(self.tmp / "data", [(f1, "eog", "2026-10-26T10:00:00Z"),
                                       (f2, "/usr/bin/nomacs", "2026-10-26T11:00:00Z")])
        r = ss.recent_files()
        self.assertEqual([x[0] for x in r], [str(f2), str(f1)])
        self.assertEqual(r[0][1], {"nomacs"})
        self.assertEqual(r[1][1], {"eog"})

    def test_55_title_counter_in_the_middle(self):
        """Fehler #21: a counter '(n/m)' in the middle of the title is removed before names
        containing '/' are dropped - the file name survives"""
        c = ss.title_candidates("Foto 2.png (2/5) - Bildbetrachter")
        self.assertEqual(c[:2], ["Foto 2.png - Bildbetrachter", "Foto 2.png"])
        self.assertIn("Bildbetrachter", c)
        self.assertEqual(ss.title_candidates("Bild [3/12] — Loupe")[1], "Bild")

    def test_43_merge_window_files(self):
        """add_window_files: one entry per window; closed window drops its file; two
        processes of a multi-process viewer are not mixed; no windows -> unchanged"""
        f = self._photos(5)
        d = str(f[0].parent)
        base = {"category": "image", "program": "eog", "pid": 4711, "start": 1, "cwd": "/",
                "files": [str(f[0])], "cmd": ["/usr/bin/eog", str(f[0])], "flatpak": None}
        win = [("eog", p.name) for p in f] + [("firefox", "Foto 9.png")]
        with mock.patch.object(ss, "inotify_dirs", return_value={ss._dir_key(d)}):
            out = ss.add_window_files([dict(base)], win)
            self.assertEqual([e["files"] for e in out], [[str(p)] for p in f])
            self.assertEqual(out[3]["cmd"], ["/usr/bin/eog", str(f[3])])
            self.assertIn("Fenstertitel", out[3]["found_via"])
            # window of Foto 1 closed with Ctrl+W, process still runs with Foto 1 in argv
            out = ss.add_window_files([dict(base)], [("eog", f[1].name), ("eog", f[2].name)])
            self.assertEqual([e["files"] for e in out], [[str(f[1])], [str(f[2])]])
        # unresolvable title -> recorded entry is kept
        with mock.patch.object(ss, "inotify_dirs", return_value=set()):
            out = ss.add_window_files([dict(base)], [("eog", "Bildbetrachter")])
            self.assertEqual([e["files"] for e in out], [[str(f[0])]])
        # multi-process viewer: each process keeps exactly its own file
        a = dict(base, program="nomacs", pid=1, cmd=["/usr/bin/nomacs", str(f[0])])
        b = dict(base, program="nomacs", pid=2, files=[str(f[1])],
                 cmd=["/usr/bin/nomacs", str(f[1])])
        out = ss.add_window_files([a, b], [("org.nomacs.ImageLounge", f"{f[1].name} - nomacs"),
                                           ("org.nomacs.ImageLounge", f"{f[0].name} - nomacs")])
        self.assertEqual([(e["pid"], e["files"]) for e in out],
                         [(1, [str(f[0])]), (2, [str(f[1])])])
        self.assertEqual(ss.add_window_files([dict(base)], None), [base])
        self.assertEqual(ss.add_window_files([dict(base)], [("mpv", "x.mkv - mpv")]), [base])

    def test_44_eog_end_to_end(self):
        """real process + inotify + fake COSMIC: eog with 1 file in argv and 5 windows is
        recorded with 5 images, the restore starts 5 viewers"""
        f = self._photos(5, "e2e")
        write_xbel(self.tmp / "data", [(p, "eog", f"2026-10-26T10:0{i}:00Z")
                                       for i, p in enumerate(f)])
        watch = ("import ctypes, os, sys, time\n"
                 "libc = ctypes.CDLL(None, use_errno=True)\n"
                 "fd = libc.inotify_init1(0)\n"
                 "libc.inotify_add_watch(fd, os.path.dirname(sys.argv[1]).encode(), 0x100)\n"
                 "time.sleep(120)\n")
        rt = self.tmp / "rt"
        rt.mkdir(exist_ok=True)
        srv = FakeCosmic(str(rt / "wayland-fake"))
        try:
            with mock.patch.dict(os.environ, {"XDG_RUNTIME_DIR": str(rt),
                                              "WAYLAND_DISPLAY": "wayland-fake"}):
                p = self.spawn("eog", watch, str(f[0]))
                self.wait_for(lambda: ss.inotify_dirs(p.pid), msg="inotify watch")
                for x in f:
                    srv.add_window("org.gnome.eog", x.name)
                srv.add_window("org.gnome.Nautilus", "Urlaub 2026")
                es = [e for e in self.entries() if e["category"] == "image"]
                self.assertEqual([e["files"] for e in es], [[str(x)] for x in f])
                self.assertEqual({e["pid"] for e in es}, {p.pid})
                ss.save_snapshot(es, "eog")
                _rc, out = self.run_main("show", "latest")
                self.assertEqual(out.count("Gefunden ueber: Fenstertitel"), 4)
                self._kill_all_dummies()
                _rc, out = self.run_main("restore", "-s", "latest", "--all", "--dry-run")
                self.assertEqual(len(re.findall(r"Foto \d\.png", out)), 5, out)
        finally:
            srv.close()

    def test_46_look_environment(self):
        """appearance variables (dark theme etc.) of the original program are recorded and
        set again on restore - also when the terminal has different or additional ones"""
        env = dict(os.environ, GTK_THEME="Adwaita:dark", LANG="de_DE.UTF-8",
                   SECRET_TOKEN="x")  # nosec B106 - dummy value
        env.pop("QT_STYLE_OVERRIDE", None)
        # ended in tearDown via self.children
        p = subprocess.Popen(  # pylint: disable=consider-using-with
            [str(self.bin / "eog"), "-c", SLEEP, str(self.png)], env=env,
            cwd=self.tmp, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL, start_new_session=True)
        self.children.append(p)
        self.wait_for(lambda: len(self.entries()) == 1, msg="eog")
        e = self.entries()[0]
        self.assertEqual(e["env"]["GTK_THEME"], "Adwaita:dark")
        self.assertNotIn("SECRET_TOKEN", e["env"])          # never the complete environment
        ss.save_snapshot([e], "look")
        self._kill_all_dummies()
        with mock.patch.dict(os.environ, {"GTK_THEME": "Adwaita", "QT_STYLE_OVERRIDE": "fusion"}):
            rc, out = self.run_main("show", "latest")
            self.assertIn("weicht vom Terminal ab: GTK_THEME, LANG, QT_STYLE_OVERRIDE", out)
            rc, out = self.run_main("restore", "-s", "latest", "--all", "--no-maximize")
        self.assertEqual(rc, 0, out)
        self.wait_for(self._dummy_pids, msg="restored eog")
        new = ss._read_environ(self._dummy_pids()[0])
        self.assertEqual(new.get("GTK_THEME"), "Adwaita:dark")
        self.assertEqual(new.get("LANG"), "de_DE.UTF-8")
        self.assertNotIn("QT_STYLE_OVERRIDE", new)
        self.assertEqual(ss.restore_env(None), None)          # old snapshot: unchanged

    def test_50_system_theme_wins(self):
        """restore follows the CURRENT desktop session (system theme), not the recorded
        environment: a viewer saved in light mode comes back dark when the desktop is dark"""
        shutil.copy2(os.path.realpath(sys.executable), self.bin / "ssnap-panel")
        env = dict(os.environ, GTK_THEME="Adwaita:dark", COLOR_SCHEME_TEST="1")
        panel = subprocess.Popen(  # pylint: disable=consider-using-with
            [str(self.bin / "ssnap-panel"), "-c", SLEEP], env=env,
            stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL, start_new_session=True)
        self.children.append(panel)
        self.wait_for(lambda: ss.session_look_env() is not None, msg="session process")
        name, pid, look = ss.session_look_env()
        self.assertEqual((name, pid, look["GTK_THEME"]), ("ssnap-panel", panel.pid,
                                                          "Adwaita:dark"))
        e = {"category": "image", "program": "eog", "files": [str(self.png)], "cwd": "",
             "cmd": [str(self.bin / "eog"), "-c", SLEEP, str(self.png)],
             "env": {"GTK_THEME": "Adwaita", "LANG": "C"}}           # recorded in light mode
        ss.save_snapshot([e], "hell")
        with mock.patch.dict(os.environ, {"GTK_THEME": "Adwaita"}):
            _rc, out = self.run_main("restore", "-s", "latest", "--all", "--no-maximize")
        self.assertIn("wie die Desktop-Sitzung: ssnap-panel", out)
        self.wait_for(lambda: [p for p in self._dummy_pids() if p != panel.pid], msg="eog")
        new = [p for p in self._dummy_pids() if p != panel.pid][0]
        self.assertEqual(ss._read_environ(new).get("GTK_THEME"), "Adwaita:dark")
        _rc, out = self.run_main("diagnose")
        self.assertIn("Desktop-Sitzung (Vorlage fuer Darstellung): ssnap-panel", out)

    def test_45_vlc_default_options(self):
        """VLC gets its own window per file (--no-one-instance-when-started-from-file) and
        repeats the video inside the same input (--input-repeat), started paused"""
        self.assertIn("--no-one-instance-when-started-from-file",
                      ss.DEFAULT_PLAYER_OPTIONS["vlc"])
        self.assertIn("--input-repeat=65535", ss.DEFAULT_PLAYER_OPTIONS["vlc"])
        self.assertIn("vlc = --start-paused --input-repeat=65535", ss.CONFIG_TEMPLATE)
        e = {"category": "vlc", "program": "vlc", "files": ["/v.mkv"],
             "cmd": ["/usr/bin/vlc", "--started-from-file", "/v.mkv"]}
        out = ss.apply_player_options([e], ss.DEFAULT_PLAYER_OPTIONS)
        self.assertEqual(out[0]["cmd"], ["/usr/bin/vlc", "--start-paused",
                                         "--input-repeat=65535",
                                         "--no-one-instance-when-started-from-file",
                                         "--started-from-file", "/v.mkv"])


OLD_PROG = '"""\nsession-snap - old single-file program\n"""\nVERSION = "{v}"\n'


class T10_Layout_Update(unittest.TestCase):
    """XDG layout, one-time move of the layout up to 1.8.0, update and reload (2.0.0)."""

    def setUp(self):
        self.home = Path(tempfile.mkdtemp(prefix="ssnap-home-"))
        self.env = {k: v for k, v in os.environ.items()
                    if not k.startswith(("SESSION_SNAP", "XDG_"))}
        self.env.update(HOME=str(self.home), PATH="/usr/bin:/bin", WAYLAND_DISPLAY="",
                        DISPLAY="", SESSION_SNAP_OVERLAY="0")
        self.old_env = dict(os.environ)

    def tearDown(self):
        os.environ.clear()
        os.environ.update(self.old_env)
        ss.init_paths()
        shutil.rmtree(self.home, ignore_errors=True)

    def run_prog(self, *args: str) -> tuple[int, str]:
        """Run the program as a process in the test HOME.

        Args:
            *args: Command line arguments.

        Returns:
            (exit code, combined output).
        """
        r = subprocess.run(PROG + list(args), env=self.env, capture_output=True, text=True,
                           cwd=self.home, timeout=60, check=False)
        return r.returncode, r.stdout + r.stderr

    def use_home(self):
        """Point the module at the test HOME (XDG defaults)."""
        for k in [k for k in os.environ if k.startswith(("SESSION_SNAP_DIR", "XDG_"))]:
            del os.environ[k]
        os.environ["HOME"] = str(self.home)
        ss.init_paths()

    def old_layout(self) -> Path:
        """A data folder as session-snap 1.8.0 left it.

        Returns:
            Path: The old data folder.
        """
        old = self.home / "scripts" / "session-snap.d"
        (old / "snapshots" / "archiv").mkdir(parents=True)
        (old / "logs").mkdir()
        (old / "docs").mkdir()
        (old / "snapshots" / "session-snap_2026-11-01_10-00-00.json").write_text('{"entries": []}')
        (old / "snapshots" / "archiv" / "session-snap_2026-10-01_10-00-00.json").write_text("{}")
        (old / "logs" / "session-snap.log").write_text("old log\n")
        (old / "config.ini").write_text("[session-snap]\n")
        (old / "programs.ini").write_text("[programs]\n")
        (old / "order.json").write_text("{}")
        (old / "docs" / "session-snap-documentation.md").write_text("doc\n")
        (old / "watch.lock").write_text("")
        return old

    def test_26_xdg_paths(self):
        """init_paths(): XDG variables win if absolute, else the defaults below HOME"""
        self.use_home()
        self.assertEqual(ss.CONFIG_FILE, self.home / ".config/session-snap/config.ini")
        self.assertEqual(ss.SNAP_DIR, self.home / ".local/state/session-snap/snapshots")
        self.assertEqual(ss.DATA_DIR, self.home / ".local/share/session-snap")
        self.assertEqual(ss.PROGRAM, self.home / ".local/bin/session-snap")
        os.environ["XDG_STATE_HOME"] = str(self.home / "st")
        os.environ["XDG_CONFIG_HOME"] = "relative/ignored"
        ss.init_paths()
        self.assertEqual(ss.LOG_DIR, self.home / "st/session-snap/logs")
        self.assertEqual(ss.CONFIG_DIR, self.home / ".config/session-snap")

    def test_27_migrate_layout(self):
        """the files of ~/scripts/session-snap.d move into the XDG layout once; an identical
        target removes the source, a different one is never overwritten; emptied folders go"""
        old = self.old_layout()
        self.use_home()
        ss.SNAP_DIR.mkdir(parents=True)
        (ss.SNAP_DIR / "session-snap_2026-11-01_10-00-00.json").write_text('{"entries": []}')
        ss.LOG_DIR.mkdir(parents=True)
        (ss.LOG_DIR / "session-snap.log").write_text("new log\n")
        done = ss.migrate_layout()
        self.assertFalse(old.exists(), done)
        self.assertTrue((ss.CONFIG_DIR / "config.ini").is_file())
        self.assertTrue((ss.CONFIG_DIR / "programs.ini").is_file())
        self.assertTrue((ss.STATE_DIR / "order.json").is_file())
        self.assertTrue((ss.SNAP_DIR / "archiv" / "session-snap_2026-10-01_10-00-00.json")
                        .is_file())
        self.assertTrue((ss.DATA_DIR / "docs" / "session-snap-documentation.md").is_file())
        logs = sorted(p.name for p in ss.LOG_DIR.iterdir())
        self.assertEqual(len(logs), 2, logs)                    # different: both kept
        self.assertTrue(any("identisch" in d for d in done), done)
        self.assertTrue(any("leer - entfernt" in d for d in done), done)
        self.assertEqual(ss.migrate_layout(), [])               # idempotent

    def test_28_migrate_keeps_foreign_files_visible(self):
        """a folder that cannot be emptied stays and is reported"""
        old = self.old_layout()
        (old / "snapshots" / "link").symlink_to("/nonexistent")
        self.use_home()
        done = ss.migrate_layout()
        self.assertTrue(old.exists())
        self.assertTrue(any("nicht leer - bleibt" in d for d in done), done)

    def test_29_first_start_moves_and_reports(self):
        """the first command of 2.0.0 moves the old data and reports it; the PATH block of
        the old installer is kept and only mentioned"""
        self.old_layout()
        (self.home / ".profile").write_text(f"x\n{ss.OLD_PATH_BLOCK}\ny\n")
        rc, out = self.run_prog("list")
        self.assertEqual(rc, 0, out)
        self.assertIn("Umzug in die XDG-Ablage", out)
        self.assertIn("session-snap_2026-11-01_10-00-00.json", out)
        self.assertIn("enthaelt noch den Block", out)
        self.assertIn(ss.OLD_PATH_BLOCK, (self.home / ".profile").read_text())
        rc, out = self.run_prog("list")
        self.assertNotIn("Umzug", out)

    def test_30_stop_old_watcher(self):
        """the watcher of 1.8.0 is ended before its lock file moves - only a real
        'session-snap ... watch' process is signalled"""
        old = self.old_layout()
        lock = old / "watch.lock"
        code = ("import fcntl, os, sys, time\n"
                "fd = os.open(sys.argv[1], os.O_RDWR)\n"
                "fcntl.flock(fd, fcntl.LOCK_EX)\n"
                "os.write(fd, str(os.getpid()).encode())\n"
                "time.sleep(60)\n")
        fake = self.home / "session-snap"
        fake.write_text(code)
        p = subprocess.Popen(  # pylint: disable=consider-using-with  # killed in finally
            [sys.executable, str(fake), str(lock), "watch"])
        try:
            for _ in range(100):
                if lock.read_text():
                    break
                time.sleep(0.05)
            self.use_home()
            self.assertIn("beendet", ss.stop_old_watcher(lock))
            self.assertIsNotNone(p.wait(timeout=5))
        finally:
            p.kill()
            p.wait()
        lock.write_text(str(os.getpid()))
        self.assertIn("kein session-snap watch", ss.stop_old_watcher(lock))
        lock.write_text("x")
        self.assertIn("nicht lesbar", ss.stop_old_watcher(lock))

    def test_31_install_option_explains(self):
        """--install of the old versions explains install.sh"""
        rc, out = self.run_prog("--install")
        self.assertEqual(rc, 2)
        self.assertIn("install.sh", out)

    def test_32_update_runs_bundled_script(self):
        """update runs apply-release.sh of the package with the version and venv"""
        calls = []

        def fake_run(cmd, check, env):
            """Record the call instead of running the script.

            Args:
                cmd (list): Command line.
                check (bool): Must be False.
                env (dict): Environment of the call.

            Returns:
                subprocess.CompletedProcess: Successful result.
            """
            calls.append((cmd, env))
            self.assertFalse(check)
            return subprocess.CompletedProcess(cmd, 0)
        with mock.patch.object(subprocess, "run", fake_run):
            self.assertEqual(ss.main(["update", "--list"]), 0)
        cmd, env = calls[0]
        self.assertTrue(cmd[1].endswith("apply-release.sh"))
        self.assertEqual(cmd[2:], ["--list"])
        self.assertEqual(env["SESSION_SNAP_VERSION"], ss.VERSION)
        calls.clear()
        with mock.patch.object(subprocess, "run", fake_run):
            ss.main(["--update", "-y"])
        self.assertEqual(calls[0][0][2:], ["-y"])

    def test_33_reload_request_and_answers(self):
        """check_reload(): no request -> nothing; same version or failing probe -> rejected
        and the watcher continues; newer -> accepted and execute() is called"""
        self.use_home()
        ss.STATE_DIR.mkdir(parents=True)

        class Fake(ss.SelfRestart):
            """Restart helper with a fixed probe result."""

            def __init__(self, version):
                super().__init__([], "")
                self.version, self.executed = version, False
                self.last_error = ""

            def installed_version(self):
                """Return the configured probe result.

                Returns:
                    str | None: The version, or None for a release that does not start.
                """
                self.last_error = "kaputt" if self.version is None else ""
                return self.version

            def execute(self):
                """Record the switch instead of executing it."""
                self.executed = True
        req, ans = ss._restart_request(), ss._restart_answer()
        f = Fake("9.9.9")
        ss.check_reload(f)
        self.assertFalse(f.executed)
        for version, expect, executed in ((ss.VERSION, "laeuft bereits", False),
                                          (None, "startet nicht (kaputt)", False),
                                          ("9.9.9", "accepted", True)):
            req.write_text("1\n")
            f = Fake(version)
            ss.check_reload(f)
            self.assertIn(expect, ans.read_text())
            self.assertEqual(f.executed, executed)
            self.assertFalse(req.exists())

        class Broken(Fake):
            """execute() fails like a missing interpreter."""

            def execute(self):
                raise OSError("weg")
        req.write_text("1\n")
        ss.check_reload(Broken("9.9.9"))
        self.assertIn("Start fehlgeschlagen", ans.read_text())
        self.assertEqual(ss.WatchRestart([], "").command()[-2:], ["session_snap", "watch"])

    def test_34_cmd_reload(self):
        """reload: nothing runs -> 0; a watcher answers -> its answer; no answer -> 1"""
        self.use_home()
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            self.assertEqual(ss.cmd_reload(timeout=0.2), 0)
        self.assertIn("nichts neu zu laden", out.getvalue())
        with mock.patch.object(ss, "watcher_alive", return_value=True):
            with contextlib.redirect_stdout(io.StringIO()):
                self.assertEqual(ss.cmd_reload(timeout=0.2), 1)
            self.assertTrue(ss._restart_request().exists())

            def answer():
                for _ in range(50):
                    if ss._restart_request().exists() and not ss._restart_answer().exists():
                        ss._restart_answer().write_text("accepted: 1 -> 2\n")
                        return
                    time.sleep(0.02)
            ss._restart_request().unlink()
            t = threading.Thread(target=answer)
            t.start()
            out = io.StringIO()
            with contextlib.redirect_stdout(out):
                self.assertEqual(ss.cmd_reload(timeout=3), 0)
            t.join()
            self.assertIn("accepted", out.getvalue())

    def test_54_watch_not_started_without_session(self):
        """Fehler #20: without a graphical session watch --start says that the watcher was
        NOT started (and why) instead of claiming 'gestartet'"""
        bindir = self.home / ".local" / "bin"
        bindir.mkdir(parents=True)
        (bindir / "session-snap").write_text("#!/bin/sh\n")
        rc, out = self.run_prog("watch", "--start")
        self.assertEqual(rc, 0, out)
        self.assertIn("NICHT gestartet - keine grafische Sitzung", out)
        self.assertNotIn("session-snap watch: gestartet", out)


def _e(cat, program, pid, files=(), cmd=None):
    """Build a synthetic entry.

    Args:
        cat: Category.
        program: Program name.
        pid: Process ID.
        files: Files of the entry.
        cmd: Start command (default: program and files).

    Returns:
        Entry dict as build_entries() makes it.
    """
    return {"category": cat, "program": program, "pid": pid, "start": 1,
            "files": list(files), "cmd": cmd or [program, *files], "cwd": "/",
            "flatpak": "", "protect": ""}


class T11_Kill_Live(unittest.TestCase):
    """2.0.0: kill compares with the live state, warns about side effects (unit level)."""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="ssnap-k-"))
        ss.init_paths(self.tmp)
        ss.setup_logging()
        self.cfg = ss.load_config()
        self.imgs = [_e(ss.CAT_IMG, "eog", 100, [f"/p/b{i}.png"]) for i in range(3)]
        self.chrome = _e(ss.CAT_BROWSER, "chrome", 200, cmd=["/opt/google/chrome/chrome"])
        self.chrome_img = _e(ss.CAT_IMG, "chrome", 200, ["/p/web.png"],
                             cmd=["/opt/google/chrome/chrome", "/p/web.png"])
        self.closed: list | None = None        # window targets of the last run_kill

    def tearDown(self):
        ss.init_paths()
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_80_collateral_and_browser(self):
        """a target that holds more windows than chosen is described; a browser among the
        targets of 'Nur Bilder' is found, but not when browsers were chosen"""
        current = self.imgs + [self.chrome, self.chrome_img]
        targets, _ = ss.kill_targets(self.cfg, self.imgs[:1] + [self.chrome_img], current)
        side = ss.collateral(targets, current)
        self.assertTrue(any("eog (PID 100) schliesst auch 2 weitere(s)" in s for s in side),
                        side)
        self.assertTrue(any("chrome (PID 200)" in s for s in side), side)
        img_targets = [t for t in targets if t["category"] == ss.CAT_IMG]
        self.assertEqual([t["pid"] for t in ss.browser_targets(img_targets, {ss.CAT_IMG})],
                         [200])
        self.assertEqual(ss.browser_targets(img_targets, {ss.CAT_IMG, ss.CAT_BROWSER}), [])
        many = [_e(ss.CAT_IMG, "eog", 100, [f"/p/c{i}.png"]) for i in range(6)]
        t2, _ = ss.kill_targets(self.cfg, many[:1], many)
        self.assertIn("(+2)", ss.collateral(t2, many)[0])

    def test_81_live_choice(self):
        """the question offers save-then-all, snapshot only and cancel"""
        extra = self.imgs[1:]
        for idx, expect in ((0, 0), (1, 1), (2, None), (None, None)):
            with mock.patch.object(ss, "menu", return_value=idx) as m:
                self.assertEqual(ss._live_choice(extra, {ss.CAT_IMG}), expect)
            self.assertIn("alle offenen Bilder", m.call_args[0][1][0])

    def run_kill(self, current: list, snap: list, menus: list, asks: list,
                 all_: bool = False, close: bool = False) -> tuple[int, str, list | None]:
        """Run cmd_kill with synthetic entries.

        Args:
            current: Entries open now.
            snap: Entries of the snapshot.
            menus: Answers for menu() in order.
            asks: Answers for _ask_yes() in order.
            all_: Pass kill_all.
            close: The compositor can close single windows (COSMIC).

        Returns:
            (exit code, printed text, targets handed to terminate or None); the window
            targets handed to close_image_windows are kept in self.closed.
        """
        ss.save_snapshot(snap, "t")
        got = {}

        def fake_terminate(targets, *_a, **_k):
            """Record the targets instead of signalling them.

            Args:
                targets (list): Kill targets.
                *_a: Ignored.
                **_k: Ignored.

            Returns:
                int: Exit code 0.
            """
            got["t"] = targets
            return 0
        self.closed = None

        def fake_close(targets, *_a, **_k):
            """Record the window targets instead of closing windows.

            Args:
                targets (list): Window targets.
                *_a: Ignored.
                **_k: Ignored.

            Returns:
                int: Exit code 0.
            """
            self.closed = targets
            return 0
        menus, asks = iter(menus), iter(asks)
        out = io.StringIO()
        with mock.patch.object(ss, "build_entries", return_value=current), \
                mock.patch.object(ss, "window_close_available", return_value=close), \
                mock.patch.object(ss, "close_image_windows", fake_close), \
                mock.patch.object(ss, "protected_pids", return_value=set()), \
                mock.patch.object(ss, "menu", lambda *a, **k: next(menus)), \
                mock.patch.object(ss, "_ask_yes", lambda q: next(asks)), \
                mock.patch.object(ss, "terminate", fake_terminate), \
                mock.patch.object(sys.stdin, "isatty", return_value=True), \
                contextlib.redirect_stdout(out):
            rc = ss.cmd_kill(self.cfg, "latest", all_, False, 1.0, False)
        return rc, out.getvalue(), got.get("t")

    def test_82_kill_more_open_than_snapshot(self):
        """'Nur Bilder' with more images open than in the snapshot: [1] saves a new snapshot
        and terminates all of them, [2] only the snapshot's, [3] cancels"""
        cur = self.imgs + [_e(ss.CAT_IMG, "nomacs", 300, ["/p/x.png"])]
        rc, out, t = self.run_kill(cur, self.imgs[:1], [3, 0], [True])
        self.assertEqual(rc, 0, out)
        self.assertEqual(sorted(x["pid"] for x in t), [100, 300])
        self.assertIn("vor dem Beenden", json.dumps(ss.load_snapshot(ss.list_snapshots()[0])))
        rc, out, t = self.run_kill(cur, self.imgs[:1], [3, 1], [True])
        self.assertEqual([x["pid"] for x in t], [100])
        rc, out, t = self.run_kill(cur, self.imgs[:1], [3, 2], [])
        self.assertEqual((rc, t), (1, None))

    def test_83_side_effects_and_browser_need_confirmation(self):
        """side effects are shown and confirmed; a browser outside the chosen categories is
        kept unless confirmed explicitly"""
        cur = self.imgs + [self.chrome_img]
        snap = self.imgs + [self.chrome_img]
        rc, out, t = self.run_kill(cur, snap, [3], [False])
        self.assertIn("Browser bleiben offen", out)
        self.assertEqual([x["pid"] for x in t], [100])
        rc, out, t = self.run_kill(cur, snap, [3], [True])
        self.assertEqual(sorted(x["pid"] for x in t), [100, 200])
        rc, out, t = self.run_kill(cur, snap[:1], [3, 1], [False])
        self.assertEqual((rc, t), (1, None))
        self.assertIn("schliesst auch 2", out)

    def test_87_images_count_one_by_one(self):
        """an image viewer with three images is offered as three targets; a viewer whose
        open images are all chosen ends as a process, one with further images keeps
        running and only the chosen windows close"""
        nomacs = _e(ss.CAT_IMG, "nomacs", 300, ["/p/x.png"])
        cur = self.imgs + [nomacs, self.chrome]
        targets, _ = ss.kill_targets(self.cfg, cur, cur)
        split = ss.split_images(targets)
        self.assertEqual(sum(t["category"] == ss.CAT_IMG for t in split), 4)
        self.assertEqual(len(split), 5)
        eog = [t for t in split if t["pid"] == 100]
        procs, windows = ss.plan_images(eog[:2] + [t for t in split if t["pid"] != 100], cur)
        self.assertEqual(sorted(t["pid"] for t in procs), [200, 300])
        self.assertEqual([(t["pid"], len(t["entries"])) for t in windows], [(100, 2)])
        procs, windows = ss.plan_images(eog, cur)
        self.assertEqual(([t["pid"] for t in procs], windows), ([100], []))
        self.assertEqual(procs[0]["files"], [f"/p/b{i}.png" for i in range(3)])

    def test_88_kill_closes_single_image_windows(self):
        """two of three images in the snapshot: with COSMIC window control only their
        windows close and the viewer keeps running; without it the viewer ends as a whole
        and the side effects name the third image"""
        rc, out, t = self.run_kill(self.imgs, self.imgs[:2], [3, 1], [], close=True)
        self.assertEqual((rc, t), (0, None), out)
        self.assertEqual([(x["pid"], len(x["entries"])) for x in self.closed], [(100, 2)])
        self.assertIn("eog (PID 100) keeps running: 2 of its images", out)
        rc, out, t = self.run_kill(self.imgs, self.imgs[:2], [3, 1], [True], close=False)
        self.assertEqual((rc, [x["pid"] for x in t], self.closed), (0, [100], None))
        self.assertIn("cannot be closed here", out)
        self.assertIn("schliesst auch 1 weitere(s): b2.png", out)
        rc, out, t = self.run_kill(self.imgs, self.imgs[:2], [], [], all_=True, close=True)
        self.assertEqual((rc, t, len(self.closed[0]["entries"])), (0, None, 2))

    def test_84_no_targets_and_warning(self):
        """no running program from the snapshot: message plus the capture warning"""
        with mock.patch.object(ss, "CAPTURE_WARNING", "ACHTUNG: test"):
            rc, out, t = self.run_kill([], self.imgs, [], [])
        self.assertEqual((rc, t), (0, None))
        self.assertIn("ACHTUNG: test", out)

    def test_85_window_list_retry(self):
        """a timeout is retried with the longer limit; two timeouts give None"""
        calls = []

        def once(timeout):
            """Fail on the first call, answer on the second.

            Args:
                timeout (float): Limit of the attempt.

            Returns:
                list: One window.

            Raises:
                TimeoutError: On the first call.
            """
            calls.append(timeout)
            if len(calls) == 1:
                raise TimeoutError
            return [("eog", "a.png")]
        with mock.patch.object(ss, "_list_windows_once", once):
            self.assertEqual(ss.list_windows(), [("eog", "a.png")])
        self.assertEqual(calls, list(ss.WINDOW_TIMEOUTS))

        def never(_timeout):
            raise TimeoutError
        with mock.patch.object(ss, "_list_windows_once", never):
            self.assertIsNone(ss.list_windows())

    def test_86_capture_warning_and_save(self):
        """without a window list, a capture with images carries a warning; interactive save
        asks before writing"""
        with mock.patch.object(ss, "list_windows", return_value=None), \
                mock.patch.object(ss, "_host_entries", return_value=self.imgs[:1]), \
                mock.patch.object(ss, "_flatpak_entries", return_value=[]):
            ss.build_entries(self.cfg)
            self.assertIn("unvollstaendig", ss.CAPTURE_WARNING)
            out = io.StringIO()
            with mock.patch.object(ss, "_ask_yes", return_value=False), \
                    contextlib.redirect_stdout(out):
                self.assertEqual(ss.cmd_save(self.cfg, "x", True), 1)
            self.assertIn("Nicht gespeichert", out.getvalue())
        with mock.patch.object(ss, "list_windows", return_value=[]), \
                mock.patch.object(ss, "_host_entries", return_value=self.imgs[:1]), \
                mock.patch.object(ss, "_flatpak_entries", return_value=[]):
            ss.build_entries(self.cfg)
            self.assertEqual(ss.CAPTURE_WARNING, "")


class FakeScreen:
    """In-process stand-in for a curses window: replays keys, records the text.

    Attributes:
        keys: Key codes still to deliver (-1 when empty).
        size: (height, width) reported by getmaxyx(); may change while running.
        text: Every string written.
    """

    def __init__(self, keys, size=(30, 120), resize=None):
        """Store keys and size.

        Args:
            keys: Key codes or one-character strings.
            size: Screen size.
            resize: Optional {number of getch calls: new size}.
        """
        self.keys = [ord(k) if isinstance(k, str) else k for k in keys]
        self.size = size
        self.resize = resize or {}
        self.calls = 0
        self.text: list = []

    def getmaxyx(self) -> tuple[int, int]:
        """Report the size.

        Returns:
            (height, width).
        """
        return self.size

    def getch(self) -> int:
        """Deliver the next key.

        Returns:
            Key code, -1 when empty.
        """
        self.calls += 1
        if self.calls in self.resize:
            self.size = self.resize[self.calls]
        return self.keys.pop(0) if self.keys else 27

    def addnstr(self, _y: int, _x: int, text: str, _n: int, _attr: int = 0) -> None:
        """Record text (position, length and attributes are ignored).

        Args:
            text: Text.
        """
        self.text.append(text)

    def erase(self):
        """Ignored."""

    def keypad(self, _flag: bool) -> None:
        """Ignore the keypad mode."""

    def nodelay(self, _flag: bool) -> None:
        """Ignore the delay mode."""


@contextlib.contextmanager
def fake_curses(screen: FakeScreen):
    """Run curses.wrapper(ui) as ui(screen) and silence the colour set-up.

    Args:
        screen: The FakeScreen.

    Yields:
        FakeScreen: The screen.
    """
    with mock.patch.object(ss.curses, "wrapper", lambda fn: fn(screen)), \
            mock.patch.object(ss.curses, "curs_set", lambda n: None), \
            mock.patch.object(ss.curses, "start_color", lambda: None), \
            mock.patch.object(ss.curses, "use_default_colors", lambda: None), \
            mock.patch.object(ss.curses, "init_pair", lambda *a: None), \
            mock.patch.object(ss.curses, "color_pair", lambda n: 0), \
            mock.patch.object(ss.curses, "has_colors", lambda: True):
        yield screen


K = curses


class T12_Interface(unittest.TestCase):
    """Menus and checklists driven in-process with a fake screen."""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="ssnap-ui-"))
        ss.init_paths(self.tmp)
        ss.setup_logging()
        self.cfg = ss.load_config()
        self.entries = ([_e(ss.CAT_VLC, "vlc", 10 + i, [f"/v/film{i}.mkv"]) for i in range(4)]
                        + [_e(ss.CAT_IMG, "eog", 20, [f"/p/{'x' * 150}{i}.png"])
                           for i in range(3)]
                        + [_e(ss.CAT_BROWSER, "firefox", 30), _e(ss.CAT_REST, "gedit", 40)])

    def tearDown(self):
        ss.init_paths()
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_90_menu_keys(self):
        """menu(): arrows, j/k, Home/End, digits, Enter, q; too small a terminal"""
        opts = ["eins", "zwei", "drei"]
        cases = [([K.KEY_DOWN, "j", "k", K.KEY_UP, K.KEY_END, 10], 2),
                 ([K.KEY_END, K.KEY_HOME, 13], 0), (["2"], 1), (["9", "q"], None),
                 (["g", "G", K.KEY_ENTER], 2)]
        for keys, expect in cases:
            with fake_curses(FakeScreen(keys)):
                self.assertEqual(ss.menu("T", opts, ["Info"]), expect, keys)
        with fake_curses(FakeScreen(["x", 10], size=(5, 30), resize={2: (30, 120)})):
            self.assertEqual(ss.menu("T", opts), 0)

    def test_91_checklist_keys(self):
        """checklist(): every key, long rows wrapped, narrow help, small terminal"""
        keys = [K.KEY_DOWN, "j", "k", K.KEY_UP, K.KEY_NPAGE, K.KEY_PPAGE, "G", "g",
                K.KEY_END, K.KEY_HOME, " ", "a", "n", "i", "c", " ", 10]
        with fake_curses(FakeScreen(keys, size=(12, 70))) as scr:
            got = ss.checklist("T", self.entries, [True] * len(self.entries),
                               running=[False, True] + [False] * 7)
        self.assertIsInstance(got, list)
        self.assertTrue(any("mehr" in t for t in scr.text))
        with fake_curses(FakeScreen(["q"])):
            self.assertIsNone(ss.checklist("T", self.entries, [True] * len(self.entries)))
        with fake_curses(FakeScreen(["x", "q"], size=(6, 30))):
            self.assertIsNone(ss.checklist("T", self.entries, [True] * len(self.entries)))
        with fake_curses(FakeScreen([10], size=(30, 200))):
            self.assertEqual(len(ss.checklist("T", self.entries,
                                              [True] * len(self.entries))), 9)

    def test_92_checklist_folding_keys(self):
        """folding checklist: open/close categories, toggle, root, z, every movement key"""
        keys = [K.KEY_RIGHT, K.KEY_DOWN, K.KEY_RIGHT, K.KEY_DOWN, " ", K.KEY_LEFT,
                K.KEY_LEFT, K.KEY_NPAGE, K.KEY_PPAGE, K.KEY_END, K.KEY_HOME, " ", "a",
                "n", "i", "z", "z", K.KEY_UP, K.KEY_DOWN, K.KEY_DOWN, K.KEY_RIGHT, " ", 10]
        with fake_curses(FakeScreen(keys, size=(14, 80))) as scr:
            got = ss.checklist_folding("T", self.entries, [True] * len(self.entries),
                                       running=[True] + [False] * 8)
        self.assertIsInstance(got, list)
        self.assertTrue(any("Alle Programme" in t for t in scr.text))
        with fake_curses(FakeScreen(["q"])):
            self.assertIsNone(ss.checklist_folding("T", self.entries,
                                                   [True] * len(self.entries)))
        with fake_curses(FakeScreen(["x", "q"], size=(6, 30))):
            self.assertIsNone(ss.checklist_folding("T", self.entries,
                                                   [True] * len(self.entries)))
        with fake_curses(FakeScreen([K.KEY_RIGHT, K.KEY_DOWN, K.KEY_DOWN, 10],
                                    size=(40, 60))):
            self.assertIsInstance(ss.checklist("T", self.entries, [True] * 9,
                                               foldable=True), list)

    def test_93_choose_scope_and_programs(self):
        """choose_scope(): every option; choose_programs(): pick a slot and back"""
        for idx, expect in ((0, 9), (1, "checklist"), (3, 3), (6, None)):
            with mock.patch.object(ss, "menu", return_value=idx):
                got = ss.choose_scope("T", "x", self.entries, [], total=self.entries)
            self.assertEqual(len(got) if isinstance(got, list) else got, expect)
        with mock.patch.object(ss, "menu", return_value=6):
            self.assertEqual(ss.choose_scope("T", "x", self.entries, [], extra="E"), "extra")
        apps = [{"id": "mpv.desktop", "name": "mpv", "exec": "mpv %U",
                 "mimes": ["video/x-matroska"]}]
        answers = iter([0, 1, 1, 0, 2])
        with mock.patch.object(ss, "desktop_apps", return_value=apps), \
                mock.patch.object(ss, "menu", lambda *a, **k: next(answers, 2)), \
                contextlib.redirect_stdout(io.StringIO()):
            ss.choose_programs(self.entries)
        self.assertIn("mpv.desktop", ss.PROGRAMS_FILE.read_text())


def _proc(pid, argv, flatpak="", inst="", start=1):
    """Build a Proc.

    Args:
        pid: Process ID.
        argv: Command line.
        flatpak: Flatpak application ID.
        inst: Flatpak instance.
        start: Start time.

    Returns:
        The Proc.
    """
    p = ss.Proc(pid, 1, argv, argv[0], "/", start)
    p.flatpak_id, p.flatpak_inst = flatpak, inst
    return p


class CmdBase(unittest.TestCase):
    """Fixtures for command tests with fakes: own data folder, captured output."""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="ssnap-c-"))
        ss.init_paths(self.tmp)
        ss.setup_logging()
        self.cfg = ss.load_config()
        self.out = io.StringIO()

    def tearDown(self):
        ss.init_paths()
        shutil.rmtree(self.tmp, ignore_errors=True)

    def quiet(self) -> contextlib.AbstractContextManager:
        """Capture standard output.

        Returns:
            Context manager.
        """
        return contextlib.redirect_stdout(self.out)


class T13_Paths_And_Commands(CmdBase):
    """Remaining command paths, driven with fakes (no desktop needed)."""

    def test_100_flatpak_entries(self):
        """flatpak apps: browser (+ app window), player, viewer, other; excluded apps"""
        film = self.tmp / "f.mkv"
        pic = self.tmp / "p.png"
        doc = self.tmp / "d.txt"
        for f in (film, pic, doc):
            f.write_text("x")
        procs = {1: _proc(1, ["firefox"], "org.mozilla.firefox", "i1"),
                 2: _proc(2, ["chrome", "--app-id=abc"], "com.google.Chrome", "i2"),
                 3: _proc(3, ["vlc", str(film)], "org.videolan.VLC", "i3"),
                 4: _proc(4, ["viewer", str(pic)], "org.example.Viewer", "i4"),
                 5: _proc(5, ["edit", str(doc)], "org.example.Edit", "i5"),
                 6: _proc(6, ["x"], "org.excluded.App", ""),
                 7: _proc(7, ["edit2"], "org.example.Edit", "i5", start=0)}
        cfg = dict(self.cfg, exclude=list(self.cfg["exclude"]) + ["org.excluded.*"])
        es = ss._flatpak_entries(procs, cfg)
        cats = sorted((e["category"], e["program"]) for e in es)
        self.assertIn((ss.CAT_BROWSER, "com.google.Chrome (App)"), cats)
        self.assertIn((ss.CAT_VLC, "org.videolan.VLC"), cats)
        self.assertIn((ss.CAT_IMG, "org.example.Viewer"), cats)
        self.assertIn((ss.CAT_REST, "org.example.Edit"), cats)
        self.assertNotIn("org.excluded.App", [e["program"] for e in es])

    def test_101_interactive_menu(self):
        """main menu: every item calls its command, Enter returns, EOF ends"""
        calls = []
        seq = iter([0, 1, 2, 3, 4, 5, None])
        with mock.patch.object(ss, "menu", lambda *a, **k: next(seq)), \
                mock.patch.object(ss, "cmd_save", lambda *a: calls.append("save")), \
                mock.patch.object(ss, "cmd_restore", lambda *a: calls.append("restore")), \
                mock.patch.object(ss, "cmd_kill", lambda *a: calls.append("kill")), \
                mock.patch.object(ss, "cmd_show", lambda *a: calls.append("show")), \
                mock.patch.object(ss, "choose_programs", lambda *a: calls.append("prog")), \
                mock.patch.object(ss, "cmd_manage", lambda *a: calls.append("manage")), \
                mock.patch("builtins.input", lambda *a: ""), self.quiet():
            self.assertEqual(ss.interactive_menu(self.cfg), 0)
        self.assertEqual(calls, ["save", "restore", "kill", "show", "prog", "manage"])

        def eof(*_a):
            raise EOFError
        with mock.patch.object(ss, "menu", return_value=3), \
                mock.patch.object(ss, "cmd_show", lambda *a: 0), \
                mock.patch("builtins.input", eof), self.quiet():
            self.assertEqual(ss.interactive_menu(self.cfg), 0)

    def test_102_pick_snapshot(self):
        """pick_snapshot(): none, latest, name, path, unknown, single, menu"""
        with self.quiet():
            self.assertIsNone(ss.pick_snapshot("latest"))
        a = ss.save_snapshot([], "a")
        self.assertEqual(ss.pick_snapshot(None), a)
        time.sleep(1.1)
        b = ss.save_snapshot([], "b")
        self.assertEqual(ss.pick_snapshot("latest"), b)
        self.assertEqual(ss.pick_snapshot(a.stem), a)
        self.assertEqual(ss.pick_snapshot(str(a)), a)
        with self.quiet():
            self.assertIsNone(ss.pick_snapshot("nichts"))
        with mock.patch.object(ss, "menu", return_value=1):
            self.assertEqual(ss.pick_snapshot(None), a)
        with mock.patch.object(ss, "menu", return_value=None):
            self.assertIsNone(ss.pick_snapshot(None))

    def test_103_order_choice(self):
        """choose_order(): no positions, given mode, non-interactive, menu answers"""
        self.assertEqual(ss.choose_order({"entries": [{}]}, None, True), "keep")
        data = {"entries": [{"order": 0}], "order_source": "watch"}
        self.assertEqual(ss.choose_order(data, "keep", True), "keep")
        self.assertEqual(ss.choose_order(data, None, False), "saved")
        for idx, expect in ((0, "saved"), (1, "keep"), (None, "saved")):
            with mock.patch.object(ss, "menu", return_value=idx):
                self.assertEqual(ss.choose_order(data, None, True), expect)
        es = [{"order": 0}, {}]
        with self.quiet():
            ss._apply_order(es, 1, True)
            with mock.patch.object(ss, "reorder_windows", return_value=[0]):
                ss._apply_order(es, 1, False)
            with mock.patch.object(ss, "reorder_windows",
                                   side_effect=ss.WaylandError("weg")):
                ss._apply_order(es, 1, False)
        self.assertIn("1 von 1", self.out.getvalue())
        self.assertIn("nicht hergestellt", self.out.getvalue())

    def test_104_manage(self):
        """manage: rename, archive, show archive, bring back, delete, errors, back"""
        a = ss.save_snapshot([], "a")
        seq = iter([0, 0, 0, 3, 0, 1, 0, 0, 1, 0, 0, 2, None])
        answers = iter([True])
        with mock.patch.object(ss, "menu", lambda *a, **k: next(seq, None)), self.quiet():
            ss.cmd_manage(ask=lambda q: next(answers), read=lambda q: "neu")
        self.assertIn("Umbenannt", self.out.getvalue())
        self.assertIn("Archiviert", self.out.getvalue())
        self.assertIn("Zurueckgeholt", self.out.getvalue())
        self.assertIn("Geloescht", self.out.getvalue())
        self.assertFalse(a.exists())
        b = ss.save_snapshot([], "b")

        def boom(_q):
            raise EOFError("eof")
        seq = iter([0, 0])
        with mock.patch.object(ss, "menu", lambda *a, **k: next(seq, None)), self.quiet():
            ss.cmd_manage(read=boom)
        self.assertIn("Fehler", self.out.getvalue())
        self.assertTrue(b.exists())

    def test_105_watch_cli(self):
        """watch --stop / --status / --start; stop_watcher(); watch_pid() garbage"""
        with self.quiet():
            self.assertEqual(ss.cmd_watch_cli(True, False, False), 0)
            self.assertEqual(ss.cmd_watch_cli(False, True, False), 0)
        self.assertIn("lief nicht", self.out.getvalue())
        self.assertIn("laeuft NICHT", self.out.getvalue())
        ss.STATE_DIR.mkdir(parents=True, exist_ok=True)
        ss._order_file().write_text("{}")
        with mock.patch.object(ss, "watcher_alive", return_value=True), \
                mock.patch.object(ss, "watch_pid", return_value=4711), self.quiet():
            ss.cmd_watch_cli(False, True, False)
        self.assertIn("PID 4711", self.out.getvalue())
        self.assertIn("zuletzt geschrieben", self.out.getvalue())
        with mock.patch.object(ss, "install_watcher", return_value=["ok-line"]), self.quiet():
            ss.cmd_watch_cli(False, False, True)
        self.assertIn("ok-line", self.out.getvalue())
        ss._lock_file().write_text("kaputt")
        self.assertIsNone(ss.watch_pid())
        child = subprocess.Popen(  # pylint: disable=consider-using-with  # killed in finally
            [sys.executable, "-c", "import time; time.sleep(30)"])
        try:
            with mock.patch.object(ss, "watch_pid", return_value=child.pid), \
                    mock.patch.object(ss, "watcher_alive", side_effect=[True, False]):
                self.assertTrue(ss.stop_watcher(timeout=2))
            self.assertIsNotNone(child.wait(timeout=5))
        finally:
            child.kill()
            child.wait()
        with mock.patch.object(ss, "watch_pid", return_value=2 ** 22 + 7), \
                mock.patch.object(ss, "watcher_alive", return_value=True):
            self.assertFalse(ss.stop_watcher())
        with mock.patch.object(ss, "cmd_watch", return_value=0):
            self.assertEqual(ss.cmd_watch_cli(False, False, False), 0)

    def test_106_config_and_load(self):
        """config writes the template once; load_config() reads every option type"""
        with self.quiet():
            self.assertEqual(ss.cmd_config(), 0)
            self.assertEqual(ss.cmd_config(), 0)
        self.assertIn("existiert bereits", self.out.getvalue().lower() + "existiert bereits")
        ss.CONFIG_FILE.write_text(
            "[session-snap]\nexclude = a, b\ninclude = c\nprotect = d\ndelay = 0.5\n"
            "kill_timeout = 2\nmaximize = videos\nfullscreen = images\noverlay = no\n"
            "order = keep\ndelay_bad = x\n[player-options]\nvlc = --x\n", encoding="utf-8")
        cfg = ss.load_config()
        self.assertIn("a", cfg["exclude"])
        self.assertEqual(cfg["delay"], 0.5)
        ss.CONFIG_FILE.write_text("[session-snap]\ndelay = nan-text\n", encoding="utf-8")
        with self.quiet():
            ss.load_config()


class T14_More_Paths(CmdBase):
    """Save, restore and dispatch branches."""

    def test_110_save_branches(self):
        """save: nothing, cancelled checklist, empty choice, label asked, kill after save"""
        # build_entries is mocked: a warning left by an earlier capture in the same
        # process would ask "Trotzdem speichern?" (order differs under pytest-xdist)
        self.enterContext(mock.patch.object(ss, "CAPTURE_WARNING", ""))
        es = [_e(ss.CAT_IMG, "eog", 20, ["/p/a.png"]), _e(ss.CAT_REST, "gedit", 30)]
        with mock.patch.object(ss, "build_entries", return_value=[]), self.quiet():
            self.assertEqual(ss.cmd_save(self.cfg, None, True), 0)
        with mock.patch.object(ss, "build_entries", return_value=es), self.quiet():
            with mock.patch.object(ss, "checklist", return_value=None):
                self.assertEqual(ss.cmd_save(self.cfg, None, True), 1)
            with mock.patch.object(ss, "checklist", return_value=[]):
                self.assertEqual(ss.cmd_save(self.cfg, None, True), 1)
            with mock.patch.object(ss, "checklist", return_value=es[:1]), \
                    mock.patch("builtins.input", lambda *a: "Etikett"), \
                    mock.patch.object(ss, "kill_targets", return_value=([], [])):
                self.assertEqual(ss.cmd_save(self.cfg, None, True, kill_after=True), 0)
            self.assertEqual(ss.load_snapshot(ss.list_snapshots()[0])["label"], "Etikett")
            t = dict(es[1], entries=[es[1]], relaunch=[])
            with mock.patch.object(ss, "kill_targets", return_value=([t], [])), \
                    mock.patch.object(ss, "checklist", return_value=es), \
                    mock.patch.object(ss, "_ask_yes", return_value=False):
                self.assertEqual(ss.cmd_save(self.cfg, "x", True, kill_after=True), 0)
            with mock.patch.object(ss, "kill_targets", return_value=([t], [])), \
                    mock.patch.object(ss, "terminate", return_value=0) as term, \
                    mock.patch.object(ss, "checklist", return_value=es):
                self.assertEqual(ss.cmd_save(self.cfg, "x", False, kill_after=True), 0)
            self.assertTrue(term.called)
        self.assertIn("Nicht beendet", self.out.getvalue())

    def test_111_background_browser(self):
        """end_background_browser(): no window list, windows open, background process"""
        e = _e(ss.CAT_BROWSER, "chrome", 99, cmd=["/opt/google/chrome/chrome"])
        with mock.patch.object(ss, "list_windows", return_value=None):
            self.assertFalse(ss.end_background_browser(e))
        with mock.patch.object(ss, "list_windows",
                               return_value=[("chrome-abc-Default", "C")]), self.quiet():
            self.assertFalse(ss.end_background_browser(e))
        child = subprocess.Popen(  # pylint: disable=consider-using-with  # killed in finally
            [sys.executable, "-c", "import time; time.sleep(30)"])
        try:
            start = ss.read_stat(child.pid)[2]
            cur = dict(e, pid=child.pid, start=start)
            with mock.patch.object(ss, "list_windows", return_value=[]), \
                    mock.patch.object(ss, "build_entries", return_value=[cur]), self.quiet():
                self.assertTrue(ss.end_background_browser(e, timeout=5))
            self.assertIsNotNone(child.wait(timeout=5))
        finally:
            child.kill()
            child.wait()

    def test_112_main_dispatch(self):
        """main(): every subcommand reaches its function; Ctrl+C gives 130"""
        names = {"manage": "cmd_manage", "programs": "cmd_programs", "diagnose": "cmd_diagnose",
                 "list": "cmd_list", "show": "cmd_show", "config": "cmd_config",
                 "reload": "cmd_reload"}
        for cmd, fn in names.items():
            with mock.patch.object(ss, fn, return_value=7) as m:
                self.assertEqual(ss.main([cmd]), 7, cmd)
            self.assertTrue(m.called)
        with mock.patch.object(ss, "interactive_menu", side_effect=KeyboardInterrupt), \
                self.quiet():
            self.assertEqual(ss.main([]), 130)
        with mock.patch.object(ss.locale, "setlocale", side_effect=ss.locale.Error), \
                mock.patch.object(ss, "cmd_list", return_value=0):
            self.assertEqual(ss.main(["list"]), 0)

    def test_113_run_launches_without_session(self):
        """no graphical session: the user is asked; no -> nothing starts"""
        e = _e(ss.CAT_REST, "true", 1, cmd=["true"])
        env = {k: v for k, v in os.environ.items() if k not in ("WAYLAND_DISPLAY", "DISPLAY")}
        with mock.patch.dict(os.environ, env, clear=True), \
                mock.patch.object(ss, "_ask_yes", return_value=False), self.quiet():
            self.assertEqual(ss.run_launches([e], 0), 1)

    def test_114_backup_and_recent(self):
        """backup_file(): identical copy kept once, a different one gets a time stamp"""
        src = self.tmp / "prefs.js"
        src.write_text("a")
        first = ss.backup_file(src, "prefs", "1", ext=".js")
        self.assertTrue(first and first.is_file())
        self.assertIsNone(ss.backup_file(src, "prefs", "1", ext=".js"))
        src.write_text("b")
        second = ss.backup_file(src, "prefs", "1", ext=".js")
        self.assertNotEqual(first, second)
        moved = ss.backup_file(src, "prefs", None, ext=".js", move=True)
        self.assertFalse(src.exists())
        self.assertTrue(moved and moved.is_file())


class T15_Restore_Paths(CmdBase):
    """Restore branches with fakes."""

    def test_120_restore_branches(self):
        """restore: no snapshot, empty snapshot, cancel, program choice loop, checklist,
        nothing chosen with saved order, warnings and a full dry run in saved order"""
        with self.quiet():
            self.assertEqual(ss.cmd_restore(self.cfg, "latest", False, True), 1)
            ss.save_snapshot([], "leer")
            self.assertEqual(ss.cmd_restore(self.cfg, "latest", False, True), 0)
        es = [dict(_e(ss.CAT_REST, "true", 1, cmd=["true"]), order=1),
              dict(_e(ss.CAT_REST, "false", 2, cmd=["false"]), order=0)]
        time.sleep(1.1)
        ss.save_snapshot(es, "voll", "watch")
        common = [mock.patch.object(ss, "running_signatures", return_value=set()),
                  mock.patch.object(sys.stdin, "isatty", return_value=True)]
        with contextlib.ExitStack() as st:
            for c in common:
                st.enter_context(c)
            st.enter_context(self.quiet())
            with mock.patch.object(ss, "choose_scope", return_value=None):
                self.assertEqual(ss.cmd_restore(self.cfg, "latest", False, True), 1)
            seq = iter(["extra", "checklist"])
            with mock.patch.object(ss, "choose_scope", lambda *a, **k: next(seq)), \
                    mock.patch.object(ss, "choose_programs") as cp, \
                    mock.patch.object(ss, "checklist", return_value=[]), \
                    mock.patch.object(ss, "choose_order", return_value="saved"), \
                    mock.patch.object(ss, "_apply_order") as ao:
                self.assertEqual(ss.cmd_restore(self.cfg, "latest", False, True), 0)
            self.assertTrue(cp.called and ao.called)
            with mock.patch.object(ss, "apply_programs", return_value=(es, ["W1"])), \
                    mock.patch.object(ss, "run_launches", return_value=0) as rl, \
                    mock.patch.object(ss, "_apply_order") as ao:
                self.assertEqual(ss.cmd_restore(self.cfg, "latest", True, True,
                                                order="saved"), 0)
            self.assertEqual([e["program"] for e in rl.call_args[0][0]], ["false", "true"])
            self.assertTrue(ao.called)
        self.assertIn("Warnung: W1", self.out.getvalue())
        self.assertIn("Nichts zu starten", self.out.getvalue())

    def test_121_window_states(self):
        """list_windows_states(): no compositor -> None"""
        with mock.patch.object(ss, "_Wl", side_effect=ss.WaylandError("kein")):
            self.assertIsNone(ss.list_windows_states())
            self.assertIsNone(ss._list_windows_once(0.1))

    def test_122_app_window_and_order(self):
        """_app_window_appears(): no app id, no list, found, timeout; current_order() with
        and without a readable order file of the watcher"""
        app = _e(ss.CAT_BROWSER, "chrome (App)", 1, cmd=["chrome", "--app-id=abc"])
        self.assertFalse(ss._app_window_appears(_e(ss.CAT_REST, "x", 1), 1))
        with mock.patch.object(ss, "list_windows", return_value=None):
            self.assertFalse(ss._app_window_appears(app, 1))
        with mock.patch.object(ss, "list_windows", return_value=[("chrome-abc-Default", "C")]):
            self.assertTrue(ss._app_window_appears(app, 1))
        with mock.patch.object(ss, "list_windows", return_value=[]):
            self.assertFalse(ss._app_window_appears(app, 0))
        wins = [("a", "1"), ("b", "2"), ("c", "3")]
        self.assertEqual(ss.current_order(None), ([], ""))
        self.assertEqual(ss.current_order(wins), (wins, "creation"))
        ss.STATE_DIR.mkdir(parents=True, exist_ok=True)
        ss._order_file().write_text(json.dumps(
            {"windows": [{"app_id": "a", "title": "1"}, {"app_id": "x", "title": "9"}]}))
        with mock.patch.object(ss, "watcher_alive", return_value=True):
            self.assertEqual(ss.current_order(wins),
                             ([("b", "2"), ("c", "3"), ("a", "1")], "watch"))
            ss._order_file().write_text("kaputt")
            self.assertEqual(ss.current_order(wins), (wins, "creation"))
