"""Busy overlay of session-snap: a Wayland layer-shell surface above all windows.

Runs as its own process with the system Python (``/usr/bin/python3``), because the
installed session-snap lives in a venv without PyGObject. Needs GTK 3 and gtk-layer-shell:
``python3-gi``, ``gir1.2-gtk-3.0`` and ``gir1.2-gtklayershell-0.1``.

Protocol (one JSON object per line):
    stdin  (from session-snap): {"title": str, "text": str, "done": int, "total": int,
                                 "keyboard": bool}  - every key optional
    stdout (to session-snap):   "ready" once the surface is shown,
                                "unavailable: <reason>" and exit 3 if it cannot be shown,
                                "cancel" when the user pressed Esc / Ctrl+C or the button.
End of stdin (session-snap finished or died) closes the overlay.

Styles (first argument): "fullscreen" covers all outputs with a dim layer and takes the
keyboard and the mouse; "corner" is a box in the upper right corner that takes only the keyboard.
"""
from __future__ import annotations

import json
import math
import sys
import threading
import time

try:
    import gi  # pylint: disable=import-error
    gi.require_version("Gtk", "3.0")
    gi.require_version("Gdk", "3.0")
    gi.require_version("GtkLayerShell", "0.1")
    from gi.repository import GLib, Gdk, Gtk, GtkLayerShell  # pylint: disable=import-error
except (ImportError, ValueError) as _exc:  # pragma: no cover - depends on the system
    print(f"unavailable: {_exc}", flush=True)
    sys.exit(3)

try:
    gi.require_foreign("cairo")
    HAVE_CAIRO = True
except ImportError:  # pragma: no cover - depends on the system
    HAVE_CAIRO = False
# GtkLayerShellEdge values (include/gtk-layer-shell.h): left 0, right 1, upper 2, lower 3
EDGE_LEFT, EDGE_RIGHT, EDGE_UPPER, EDGE_LOWER, EDGES = range(5)
CANCEL_GRACE = 5                 # seconds the overlay stays after a cancel
TEXT_SPINNER = "\u25d0\u25d3\u25d1\u25d2"   # quarter circles, turning

CSS = b"""
window.ssnap-dim { background-color: rgba(0, 0, 0, 0.45); }
.ssnap-card {
    background-color: rgba(30, 30, 30, 0.95); color: #f0f0f0;
    border-radius: 14px; padding: 22px 28px;
}
.ssnap-card label, progressbar text { color: #f0f0f0; }
.ssnap-card button label { color: #202020; }
.ssnap-title { font-size: 15pt; font-weight: bold; }
.ssnap-hint { color: #b8b8b8; font-size: 9pt; }
progressbar trough, progressbar progress { min-height: 10px; }
"""


def fmt_eta(seconds: float) -> str:
    """Remaining time as text.

    Args:
        seconds: Estimated seconds left.

    Returns:
        str: For example 'noch ca. 1:05 min'.
    """
    s = max(int(seconds + 0.5), 0)
    return f"noch ca. {s // 60}:{s % 60:02d} min"


class OverlayWindow:  # pylint: disable=too-many-instance-attributes
    """The layer-shell window with spinner, progress bar, step text and time left."""

    def __init__(self, style: str):
        self.style = style
        self.started = time.monotonic()
        self.phase_start = self.started
        self.phase_total = 0
        self.cancelled = False
        self.win = Gtk.Window()
        GtkLayerShell.init_for_window(self.win)
        GtkLayerShell.set_layer(self.win, GtkLayerShell.Layer.OVERLAY)
        GtkLayerShell.set_namespace(self.win, "session-snap")
        GtkLayerShell.set_keyboard_mode(self.win, GtkLayerShell.KeyboardMode.EXCLUSIVE)
        provider = Gtk.CssProvider()
        provider.load_from_data(CSS)
        Gtk.StyleContext.add_provider_for_screen(
            Gdk.Screen.get_default(), provider, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION)
        card = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=10)
        card.set_size_request(460, -1)
        card.get_style_context().add_class("ssnap-card")
        if style == "fullscreen":
            for edge in range(EDGES):
                GtkLayerShell.set_anchor(self.win, edge_of(edge), True)
            GtkLayerShell.set_exclusive_zone(self.win, -1)   # cover panels too
            self.win.get_style_context().add_class("ssnap-dim")
            card.set_halign(Gtk.Align.CENTER)
            card.set_valign(Gtk.Align.CENTER)
        else:
            GtkLayerShell.set_anchor(self.win, edge_of(EDGE_UPPER), True)
            GtkLayerShell.set_anchor(self.win, edge_of(EDGE_RIGHT), True)
            GtkLayerShell.set_margin(self.win, edge_of(EDGE_UPPER), 16)
            GtkLayerShell.set_margin(self.win, edge_of(EDGE_RIGHT), 16)
        self.win.set_app_paintable(True)
        visual = Gdk.Screen.get_default().get_rgba_visual()
        if visual is not None:
            self.win.set_visual(visual)
        head = Gtk.Box(spacing=12)
        # Own spinner (rotating arc drawn with cairo): Gtk.Spinner needs the
        # "process-working-symbolic" icon of the theme and stays empty without it.
        # Without python3-gi-cairo a text spinner is used instead.
        self.angle = 0.0
        self.step = 0
        if HAVE_CAIRO:
            self.spinner = Gtk.DrawingArea()
            self.spinner.set_size_request(26, 26)
            self.spinner.connect("draw", self.draw_spinner)
        else:
            self.spinner = Gtk.Label(label=TEXT_SPINNER[0])
            self.spinner.get_style_context().add_class("ssnap-title")
        GLib.timeout_add(60, self.tick)
        self.title = Gtk.Label(label="session-snap arbeitet", xalign=0)
        self.title.get_style_context().add_class("ssnap-title")
        head.pack_start(self.spinner, False, False, 0)
        head.pack_start(self.title, True, True, 0)
        self.text = Gtk.Label(label="", xalign=0)
        self.text.set_line_wrap(True)
        self.progress = Gtk.ProgressBar(show_text=True)
        self.eta = Gtk.Label(label="", xalign=0)
        hint = Gtk.Label(label="Bitte nicht eingreifen.  Esc oder Strg+C: Abbrechen", xalign=0)
        hint.get_style_context().add_class("ssnap-hint")
        button = Gtk.Button(label="Abbrechen")
        button.connect("clicked", lambda _b: self.cancel())
        for w in (head, self.text, self.progress, self.eta, hint):
            card.pack_start(w, False, False, 0)
        card.pack_end(button, False, False, 0)
        self.win.add(card)
        self.win.connect("key-press-event", self.on_key)
        self.card = card
        self.pointer_visible: "bool | None" = None
        if style == "fullscreen":
            # the pointer is hidden over the dimmed area and shown over the card only
            self.win.add_events(Gdk.EventMask.POINTER_MOTION_MASK)
            self.win.connect("motion-notify-event", self.on_motion)
            self.win.connect("realize", lambda _w: self.show_pointer(False))
        self.win.connect("destroy", lambda _w: Gtk.main_quit())
        self.win.show_all()
        self.progress.hide()
        self.eta.hide()

    def show_pointer(self, visible: bool) -> None:
        """Show or hide the mouse pointer over the overlay window.

        Args:
            visible: True = normal arrow, False = no pointer.
        """
        gdk_win = self.win.get_window()
        if gdk_win is None or visible == self.pointer_visible:
            return
        self.pointer_visible = visible
        name = "default" if visible else "none"
        gdk_win.set_cursor(Gdk.Cursor.new_from_name(gdk_win.get_display(), name))

    def on_motion(self, _w, event) -> bool:
        """Show the pointer only while it is over the card.

        Args:
            event (Gdk.EventMotion): Pointer position in window coordinates.

        Returns:
            bool: False, let GTK handle the event as well.
        """
        box = self.card.get_allocation()
        inside = (box.x <= event.x < box.x + box.width
                  and box.y <= event.y < box.y + box.height)
        self.show_pointer(inside)
        return False

    def tick(self) -> bool:
        """Advance the spinner by one step.

        Returns:
            bool: True, keep the timer running.
        """
        self.angle = (self.angle + 0.35) % (2 * math.pi)
        self.step += 1
        if isinstance(self.spinner, Gtk.Label):
            self.spinner.set_text(TEXT_SPINNER[(self.step // 3) % len(TEXT_SPINNER)])
        else:
            self.spinner.queue_draw()
        return True

    def draw_spinner(self, area, cr) -> bool:
        """Draw a grey ring with a bright rotating arc.

        Args:
            area (Gtk.DrawingArea): The drawing area.
            cr (cairo.Context): Cairo context.

        Returns:
            bool: False, drawing done.
        """
        w, h = area.get_allocated_width(), area.get_allocated_height()
        r = min(w, h) / 2 - 3
        cr.set_line_width(3.5)
        cr.set_source_rgba(1, 1, 1, 0.18)
        cr.arc(w / 2, h / 2, r, 0, 2 * math.pi)
        cr.stroke()
        cr.set_source_rgb(0.21, 0.52, 0.89)
        cr.arc(w / 2, h / 2, r, self.angle, self.angle + 1.6)
        cr.stroke()
        return False

    def on_key(self, _w, event) -> bool:
        """Esc or Ctrl+C cancels; every other key is swallowed.

        Args:
            event (Gdk.EventKey): Key event.

        Returns:
            bool: True, the key is consumed.
        """
        ctrl = bool(event.state & Gdk.ModifierType.CONTROL_MASK)
        if event.keyval == Gdk.KEY_Escape or (ctrl and event.keyval in (Gdk.KEY_c, Gdk.KEY_C)):
            self.cancel()
        return True

    def cancel(self) -> None:
        """Tell session-snap to stop. The overlay closes when session-snap ends its input,
        at the latest CANCEL_GRACE seconds later, or at once on a second cancel - so a
        session-snap that does not react can never keep the desktop blocked."""
        if self.cancelled:
            Gtk.main_quit()
            return
        self.cancelled = True
        print("cancel", flush=True)
        self.text.set_text("Wird abgebrochen …  (nochmals Esc: Anzeige sofort schliessen)")
        GLib.timeout_add_seconds(CANCEL_GRACE, quit_once)

    def update(self, msg: dict) -> bool:
        """Apply one message from session-snap.

        Args:
            msg: Decoded JSON message.

        Returns:
            bool: False (GLib idle callback runs once).
        """
        if "title" in msg:
            self.title.set_text(f"session-snap arbeitet: {msg['title']}")
        if "text" in msg:
            self.text.set_text(str(msg["text"]))
        if "keyboard" in msg:
            mode = (GtkLayerShell.KeyboardMode.EXCLUSIVE if msg["keyboard"]
                    else GtkLayerShell.KeyboardMode.NONE)
            GtkLayerShell.set_keyboard_mode(self.win, mode)
        total, done = int(msg.get("total", 0) or 0), int(msg.get("done", 0) or 0)
        if total > 0:
            if total != self.phase_total or done == 0:
                self.phase_total, self.phase_start = total, time.monotonic()
            self.progress.set_fraction(min(done / total, 1.0))
            self.progress.set_text(f"{done} / {total}")
            self.progress.show()
            elapsed = time.monotonic() - self.phase_start
            if 0 < done < total and elapsed > 1.0:
                self.eta.set_text(fmt_eta(elapsed / done * (total - done)))
                self.eta.show()
            else:
                self.eta.hide()
        elif "text" in msg:
            self.progress.hide()
            self.eta.hide()
        return False


def read_stdin(win: OverlayWindow) -> None:
    """Forward the lines from session-snap into the GTK main loop; quit at end of input.

    Args:
        win: The overlay window.
    """
    for line in sys.stdin:
        try:
            msg = json.loads(line)
        except ValueError:
            continue
        if isinstance(msg, dict):
            GLib.idle_add(win.update, msg)
    GLib.idle_add(Gtk.main_quit)


def edge_of(index: int):
    """Screen edge of gtk-layer-shell by its number.

    Args:
        index: One of EDGE_LEFT, EDGE_RIGHT, EDGE_UPPER, EDGE_LOWER.

    Returns:
        GtkLayerShell.Edge: The edge.
    """
    return GtkLayerShell.Edge(index)


def quit_once() -> bool:
    """End the GTK main loop (GLib timeout callback).

    Returns:
        bool: False, run once.
    """
    Gtk.main_quit()
    return False


def report_ready() -> bool:
    """Tell session-snap that the overlay is on screen (GLib idle callback).

    Returns:
        bool: False, run once.
    """
    print("ready", flush=True)
    return False


def main() -> int:
    """Show the overlay until stdin ends.

    Returns:
        int: Exit status (3 = cannot be shown).
    """
    style = sys.argv[1] if len(sys.argv) > 1 else "fullscreen"
    if not GtkLayerShell.is_supported():
        print("unavailable: compositor without layer shell", flush=True)
        return 3
    win = OverlayWindow(style)
    threading.Thread(target=read_stdin, args=(win,), daemon=True).start()
    GLib.idle_add(report_ready)
    Gtk.main()
    return 0


if __name__ == "__main__":
    sys.exit(main())
