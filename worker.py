#!/usr/bin/env python3
"""
Mause Worker — prevents the computer from sleeping and keeps Teams online.

Usage:
  python worker.py                     # Keep system + display awake, Teams online (tray icon)
  python worker.py --no-display        # Keep system awake, allow display to sleep
  python worker.py --no-teams          # Skip Teams mouse-nudge (sleep prevention only)
  python worker.py --duration 60       # Auto-stop after 60 minutes
  python worker.py --no-tray           # Headless mode, Ctrl+C to stop
"""

import argparse
import ctypes
import ctypes.wintypes
import signal
import sys
import threading
import time
from typing import Optional

# Windows API execution state flags
ES_CONTINUOUS       = 0x80000000
ES_SYSTEM_REQUIRED  = 0x00000001
ES_DISPLAY_REQUIRED = 0x00000002

# SendInput structures for mouse movement
INPUT_MOUSE         = 0
MOUSEEVENTF_MOVE    = 0x0001  # relative movement


class _MOUSEINPUT(ctypes.Structure):
    _fields_ = [
        ("dx",          ctypes.c_long),
        ("dy",          ctypes.c_long),
        ("mouseData",   ctypes.wintypes.DWORD),
        ("dwFlags",     ctypes.wintypes.DWORD),
        ("time",        ctypes.wintypes.DWORD),
        ("dwExtraInfo", ctypes.POINTER(ctypes.c_ulong)),
    ]


class _INPUT_UNION(ctypes.Union):
    _fields_ = [("mi", _MOUSEINPUT)]


class _INPUT(ctypes.Structure):
    _anonymous_ = ("_u",)
    _fields_ = [
        ("type", ctypes.wintypes.DWORD),
        ("_u",   _INPUT_UNION),
    ]


def _nudge_mouse() -> None:
    """Send a +1 / -1 relative mouse move — resets Teams' GetLastInputInfo timer."""
    inputs = (_INPUT * 2)()
    for i, dx in enumerate((1, -1)):
        inputs[i].type   = INPUT_MOUSE
        inputs[i].mi.dx  = dx
        inputs[i].mi.dy  = 0
        inputs[i].mi.dwFlags = MOUSEEVENTF_MOVE
    ctypes.windll.user32.SendInput(2, inputs, ctypes.sizeof(_INPUT))


class MauseWorker:
    # Teams marks users Away after 5 minutes of no input; nudge every 4 minutes.
    _TEAMS_NUDGE_INTERVAL = 240

    def __init__(self, display: bool = True, teams: bool = True):
        self.display = display
        self.teams = teams
        self._active = False
        self._lock = threading.Lock()

    # ------------------------------------------------------------------
    # Core Windows API calls
    # ------------------------------------------------------------------

    def _apply_state(self) -> None:
        flags = ES_CONTINUOUS | ES_SYSTEM_REQUIRED
        if self.display:
            flags |= ES_DISPLAY_REQUIRED
        ctypes.windll.kernel32.SetThreadExecutionState(flags)

    def activate(self) -> None:
        with self._lock:
            self._active = True
        self._apply_state()

    def deactivate(self) -> None:
        with self._lock:
            self._active = False
        ctypes.windll.kernel32.SetThreadExecutionState(ES_CONTINUOUS)

    def is_active(self) -> bool:
        with self._lock:
            return self._active

    # ------------------------------------------------------------------
    # Heartbeats
    # ------------------------------------------------------------------

    def _heartbeat(self, interval: int = 58) -> None:
        """Re-asserts SetThreadExecutionState every minute."""
        while self.is_active():
            time.sleep(interval)
            if self.is_active():
                self._apply_state()

    def _teams_heartbeat(self) -> None:
        """Nudges the mouse every 4 minutes to keep Teams from going Away."""
        while self.is_active():
            time.sleep(self._TEAMS_NUDGE_INTERVAL)
            if self.is_active() and self.teams:
                _nudge_mouse()

    def _start_heartbeats(self) -> None:
        threading.Thread(target=self._heartbeat, daemon=True).start()
        threading.Thread(target=self._teams_heartbeat, daemon=True).start()

    # ------------------------------------------------------------------
    # Headless mode
    # ------------------------------------------------------------------

    def run_headless(self, duration_sec: Optional[int] = None) -> None:
        def _shutdown(sig=None, frame=None):
            self.deactivate()
            print("\nMause Worker deactivated. Sleep is now allowed.")
            sys.exit(0)

        signal.signal(signal.SIGINT, _shutdown)
        signal.signal(signal.SIGTERM, _shutdown)

        self.activate()
        self._start_heartbeats()

        msg = "Mause Worker active. Press Ctrl+C to stop."
        if duration_sec:
            print(msg + f" Auto-stopping in {duration_sec // 60} minute(s).")
        else:
            print(msg)

        deadline = time.monotonic() + duration_sec if duration_sec else None
        while True:
            if deadline and time.monotonic() >= deadline:
                _shutdown()
            time.sleep(1)

    # ------------------------------------------------------------------
    # System tray mode
    # ------------------------------------------------------------------

    def run_tray(self, duration_sec: Optional[int] = None) -> None:
        import pystray
        from PIL import Image, ImageDraw

        def _make_icon(active: bool = True) -> Image.Image:
            img = Image.new("RGBA", (64, 64), (0, 0, 0, 0))
            d = ImageDraw.Draw(img)
            cup   = (255, 165, 0) if active else (110, 110, 110)
            steam = (180, 210, 255)

            # Cup body (tapered)
            d.polygon([(14, 30), (50, 30), (46, 57), (18, 57)], fill=cup)
            # Rim
            d.rectangle([(11, 25), (53, 32)], fill=cup)
            # Handle
            d.arc([(46, 34), (60, 52)], start=270, end=90, fill=cup, width=4)
            # Steam wisps (only when active)
            if active:
                for x in (22, 32, 42):
                    d.arc([(x - 5, 6), (x + 5, 22)], start=200, end=340,
                          fill=steam, width=2)
            return img

        # Mutable state shared with callbacks
        state = {"icon": None}

        def _build_menu() -> pystray.Menu:
            return pystray.Menu(
                pystray.MenuItem(
                    "Keep display on",
                    _on_toggle_display,
                    checked=lambda _: self.display,
                ),
                pystray.MenuItem(
                    "Keep Teams online",
                    _on_toggle_teams,
                    checked=lambda _: self.teams,
                ),
                pystray.Menu.SEPARATOR,
                pystray.MenuItem("Quit", _on_quit),
            )

        def _on_toggle_display(icon, item) -> None:
            self.display = not self.display
            self._apply_state()

        def _on_toggle_teams(icon, item) -> None:
            self.teams = not self.teams

        def _on_quit(icon, _=None) -> None:
            self.deactivate()
            print("\nMause Worker deactivated. Sleep is now allowed.")
            icon.stop()

        icon = pystray.Icon(
            name="Mause Worker",
            icon=_make_icon(active=True),
            title="Mause Worker — keeping awake",
            menu=_build_menu(),
        )
        state["icon"] = icon

        self.activate()
        self._start_heartbeats()

        if duration_sec:
            def _auto_stop() -> None:
                time.sleep(duration_sec)
                if self.is_active():
                    _on_quit(icon)
            threading.Thread(target=_auto_stop, daemon=True).start()

        print("Mause Worker active. Right-click the system tray icon to control it.")
        icon.run()


# ----------------------------------------------------------------------
# Entry point
# ----------------------------------------------------------------------

def main() -> None:
    parser = argparse.ArgumentParser(
        description="Mause Worker — prevent your computer from sleeping and keep Teams online.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    parser.add_argument(
        "--no-display",
        action="store_true",
        help="Only prevent system sleep; allow the display to turn off.",
    )
    parser.add_argument(
        "--duration",
        type=int,
        metavar="MINUTES",
        help="Automatically deactivate after MINUTES minutes.",
    )
    parser.add_argument(
        "--no-teams",
        action="store_true",
        help="Disable the Teams presence nudge (mouse-move trick).",
    )
    parser.add_argument(
        "--no-tray",
        action="store_true",
        help="Run without a system tray icon (headless mode).",
    )
    args = parser.parse_args()

    duration_sec = args.duration * 60 if args.duration else None
    app = MauseWorker(display=not args.no_display, teams=not args.no_teams)

    mode = "system only" if args.no_display else "system + display"
    teams_note = "" if args.no_teams else " + Teams online"
    suffix = f" for {args.duration} minute(s)" if args.duration else ""
    print(f"Mause Worker — keeping awake ({mode}{teams_note}){suffix}.")

    if args.no_tray:
        app.run_headless(duration_sec)
    else:
        try:
            import pystray   # noqa: F401
            from PIL import Image  # noqa: F401
            app.run_tray(duration_sec)
        except ImportError:
            print("Note: install pystray and Pillow for a system tray icon.")
            print("      pip install pystray Pillow")
            app.run_headless(duration_sec)


if __name__ == "__main__":
    main()
