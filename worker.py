#!/usr/bin/env python3
"""
Caffeine for Windows — prevents the computer from sleeping.

Usage:
  python caffeine.py                   # Keep system + display awake (tray icon)
  python caffeine.py --no-display      # Keep system awake, allow display to sleep
  python caffeine.py --duration 60     # Auto-stop after 60 minutes
  python caffeine.py --no-tray         # Headless mode, Ctrl+C to stop
"""

import argparse
import ctypes
import signal
import sys
import threading
import time
from typing import Optional

# Windows API execution state flags
ES_CONTINUOUS       = 0x80000000
ES_SYSTEM_REQUIRED  = 0x00000001
ES_DISPLAY_REQUIRED = 0x00000002


class Caffeine:
    def __init__(self, display: bool = True):
        self.display = display
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
    # Heartbeat — re-asserts the state every minute as a safety net
    # ------------------------------------------------------------------

    def _heartbeat(self, interval: int = 58) -> None:
        while self.is_active():
            time.sleep(interval)
            if self.is_active():
                self._apply_state()

    # ------------------------------------------------------------------
    # Headless mode
    # ------------------------------------------------------------------

    def run_headless(self, duration_sec: Optional[int] = None) -> None:
        def _shutdown(sig=None, frame=None):
            self.deactivate()
            print("\nCaffeine deactivated. Sleep is now allowed.")
            sys.exit(0)

        signal.signal(signal.SIGINT, _shutdown)
        signal.signal(signal.SIGTERM, _shutdown)

        self.activate()
        threading.Thread(target=self._heartbeat, daemon=True).start()

        msg = "Caffeine active. Press Ctrl+C to stop."
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
            display_label = "Keep display on"
            return pystray.Menu(
                pystray.MenuItem(
                    display_label,
                    _on_toggle_display,
                    checked=lambda _: self.display,
                ),
                pystray.Menu.SEPARATOR,
                pystray.MenuItem("Quit", _on_quit),
            )

        def _on_toggle_display(icon, item) -> None:
            self.display = not self.display
            self._apply_state()
            icon.icon = _make_icon(active=True)

        def _on_quit(icon, _=None) -> None:
            self.deactivate()
            print("\nCaffeine deactivated. Sleep is now allowed.")
            icon.stop()

        icon = pystray.Icon(
            name="Caffeine",
            icon=_make_icon(active=True),
            title="Caffeine — keeping awake",
            menu=_build_menu(),
        )
        state["icon"] = icon

        self.activate()
        threading.Thread(target=self._heartbeat, daemon=True).start()

        if duration_sec:
            def _auto_stop() -> None:
                time.sleep(duration_sec)
                if self.is_active():
                    _on_quit(icon)
            threading.Thread(target=_auto_stop, daemon=True).start()

        print("Caffeine active. Right-click the system tray icon to control it.")
        icon.run()


# ----------------------------------------------------------------------
# Entry point
# ----------------------------------------------------------------------

def main() -> None:
    parser = argparse.ArgumentParser(
        description="Caffeine for Windows — prevent your computer from sleeping.",
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
        "--no-tray",
        action="store_true",
        help="Run without a system tray icon (headless mode).",
    )
    args = parser.parse_args()

    duration_sec = args.duration * 60 if args.duration else None
    app = Caffeine(display=not args.no_display)

    mode = "system only" if args.no_display else "system + display"
    suffix = f" for {args.duration} minute(s)" if args.duration else ""
    print(f"Caffeine — keeping awake ({mode}){suffix}.")

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
