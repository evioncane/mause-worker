#!/usr/bin/env python3
"""
Mause Worker — prevents the computer from sleeping and keeps Teams online.

Runs on Windows and macOS. The platform is detected automatically; pass --mac
to force the macOS backend.

Usage:
  python worker.py                     # Keep system + display awake, Teams online (tray icon)
  python worker.py --mac               # Force the macOS backend
  python worker.py --no-display        # Keep system awake, allow display to sleep
  python worker.py --no-teams          # Skip Teams mouse-nudge (sleep prevention only)
  python worker.py --duration 60       # Auto-stop after 60 minutes
  python worker.py --no-tray           # Headless mode, Ctrl+C to stop
"""

import argparse
import atexit
import ctypes
import os
import shutil
import signal
import subprocess
import sys
import threading
import time
from typing import List, Optional


# ----------------------------------------------------------------------
# Platform backends
# ----------------------------------------------------------------------

class SleepBackend:
    """Platform-specific sleep prevention and idle-timer reset."""

    name = "unknown"

    def apply_state(self, display: bool) -> None:
        """Assert (or re-assert) that the system — and optionally the display — stays awake."""
        raise NotImplementedError

    def release(self) -> None:
        """Drop the assertion so normal power management resumes."""
        raise NotImplementedError

    def nudge(self) -> None:
        """Move the mouse imperceptibly so the OS idle timer — and Teams — resets."""
        raise NotImplementedError

    def warn_if_unready(self, teams: bool) -> None:
        """Print any platform-specific setup the user still has to do."""

    def diagnose(self) -> int:
        """Print a readiness report. Returns a process exit code."""
        print(f"Backend: {self.name}")
        return 0


# --- Windows -----------------------------------------------------------

# Windows API execution state flags
ES_CONTINUOUS       = 0x80000000
ES_SYSTEM_REQUIRED  = 0x00000001
ES_DISPLAY_REQUIRED = 0x00000002

# SendInput structures for mouse movement
INPUT_MOUSE      = 0
MOUSEEVENTF_MOVE = 0x0001  # relative movement


def _windows_input_type():
    """Build the SendInput structures. Deferred — ctypes.wintypes is Windows-only."""
    import ctypes.wintypes

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

    return _INPUT


class WindowsBackend(SleepBackend):
    """Uses SetThreadExecutionState to block sleep and SendInput to nudge the mouse."""

    name = "Windows"

    def __init__(self) -> None:
        if not hasattr(ctypes, "windll"):
            raise SystemExit("The Windows backend requires Windows.")
        self._input_type = _windows_input_type()

    def apply_state(self, display: bool) -> None:
        flags = ES_CONTINUOUS | ES_SYSTEM_REQUIRED
        if display:
            flags |= ES_DISPLAY_REQUIRED
        ctypes.windll.kernel32.SetThreadExecutionState(flags)

    def release(self) -> None:
        ctypes.windll.kernel32.SetThreadExecutionState(ES_CONTINUOUS)

    def nudge(self) -> None:
        """Send a +1 / -1 relative mouse move — resets Teams' GetLastInputInfo timer."""
        inputs = (self._input_type * 2)()
        for index, dx in enumerate((1, -1)):
            inputs[index].type      = INPUT_MOUSE
            inputs[index].mi.dx     = dx
            inputs[index].mi.dy     = 0
            inputs[index].mi.dwFlags = MOUSEEVENTF_MOVE
        ctypes.windll.user32.SendInput(2, inputs, ctypes.sizeof(self._input_type))


# --- macOS -------------------------------------------------------------

APPLICATION_SERVICES = (
    "/System/Library/Frameworks/ApplicationServices.framework/ApplicationServices"
)
CORE_FOUNDATION = "/System/Library/Frameworks/CoreFoundation.framework/CoreFoundation"

KCG_EVENT_MOUSE_MOVED = 5
KCG_HID_EVENT_TAP     = 0


class _CGPoint(ctypes.Structure):
    _fields_ = [("x", ctypes.c_double), ("y", ctypes.c_double)]


class MacBackend(SleepBackend):
    """Uses `caffeinate` to block sleep and a Quartz mouse event to nudge the idle timer."""

    name = "macOS"

    def __init__(self) -> None:
        if sys.platform != "darwin":
            raise SystemExit("--mac was passed, but this is not macOS.")
        self._caffeinate: Optional[subprocess.Popen] = None
        self._caffeinate_command: Optional[List[str]] = None
        self._quartz = self._load_quartz()
        self._core_foundation = self._load_core_foundation()
        atexit.register(self.release)

    # -- framework loading ---------------------------------------------

    @staticmethod
    def _load_quartz():
        try:
            quartz = ctypes.cdll.LoadLibrary(APPLICATION_SERVICES)
        except OSError:
            return None
        quartz.CGEventCreate.argtypes = [ctypes.c_void_p]
        quartz.CGEventCreate.restype = ctypes.c_void_p
        quartz.CGEventGetLocation.argtypes = [ctypes.c_void_p]
        quartz.CGEventGetLocation.restype = _CGPoint
        quartz.CGEventCreateMouseEvent.argtypes = [
            ctypes.c_void_p, ctypes.c_uint32, _CGPoint, ctypes.c_uint32,
        ]
        quartz.CGEventCreateMouseEvent.restype = ctypes.c_void_p
        quartz.CGEventPost.argtypes = [ctypes.c_uint32, ctypes.c_void_p]
        quartz.CGEventPost.restype = None
        quartz.AXIsProcessTrusted.argtypes = []
        quartz.AXIsProcessTrusted.restype = ctypes.c_bool
        quartz.AXIsProcessTrustedWithOptions.argtypes = [ctypes.c_void_p]
        quartz.AXIsProcessTrustedWithOptions.restype = ctypes.c_bool
        return quartz

    @staticmethod
    def _load_core_foundation():
        try:
            core_foundation = ctypes.cdll.LoadLibrary(CORE_FOUNDATION)
        except OSError:
            return None
        core_foundation.CFRelease.argtypes = [ctypes.c_void_p]
        core_foundation.CFRelease.restype = None
        core_foundation.CFDictionaryCreate.argtypes = [
            ctypes.c_void_p,
            ctypes.POINTER(ctypes.c_void_p),
            ctypes.POINTER(ctypes.c_void_p),
            ctypes.c_long,
            ctypes.c_void_p,
            ctypes.c_void_p,
        ]
        core_foundation.CFDictionaryCreate.restype = ctypes.c_void_p
        return core_foundation

    def _release_event(self, event) -> None:
        if event and self._core_foundation is not None:
            self._core_foundation.CFRelease(event)

    # -- sleep prevention ----------------------------------------------

    def apply_state(self, display: bool) -> None:
        # -i: no idle sleep, -s: no system sleep on AC, -d: no display sleep,
        # -w: exit if this process dies, so no assertion can outlive us.
        command = ["caffeinate", "-i", "-s"]
        if display:
            command.append("-d")
        command += ["-w", str(os.getpid())]

        running = self._caffeinate is not None and self._caffeinate.poll() is None
        if running and command == self._caffeinate_command:
            return

        self._stop_caffeinate()
        try:
            self._caffeinate = subprocess.Popen(
                command,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
        except FileNotFoundError:
            raise SystemExit("`caffeinate` was not found — the macOS backend needs macOS.")
        self._caffeinate_command = command

    def release(self) -> None:
        self._stop_caffeinate()

    def _stop_caffeinate(self) -> None:
        process = self._caffeinate
        self._caffeinate = None
        self._caffeinate_command = None
        if process is None or process.poll() is not None:
            return
        process.terminate()
        try:
            process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            process.kill()

    # -- idle-timer nudge ----------------------------------------------

    def nudge(self) -> None:
        """Post a +1 / -1 mouse move through the HID event tap — resets HIDIdleTime."""
        if self._quartz is None:
            return
        probe = self._quartz.CGEventCreate(None)
        if not probe:
            return
        origin = self._quartz.CGEventGetLocation(probe)
        self._release_event(probe)

        for offset in (1.0, 0.0):
            target = _CGPoint(origin.x + offset, origin.y)
            event = self._quartz.CGEventCreateMouseEvent(
                None, KCG_EVENT_MOUSE_MOVED, target, 0
            )
            if not event:
                continue
            self._quartz.CGEventPost(KCG_HID_EVENT_TAP, event)
            self._release_event(event)

    # -- Accessibility permission --------------------------------------

    def is_trusted(self) -> bool:
        """True when macOS will actually deliver our synthetic mouse events."""
        return bool(self._quartz is not None and self._quartz.AXIsProcessTrusted())

    def _prompt_options(self):
        """CFDictionary {kAXTrustedCheckOptionPrompt: true}, or None if unavailable."""
        if self._quartz is None or self._core_foundation is None:
            return None
        try:
            prompt_key      = ctypes.c_void_p.in_dll(self._quartz, "kAXTrustedCheckOptionPrompt")
            true_value      = ctypes.c_void_p.in_dll(self._core_foundation, "kCFBooleanTrue")
            key_callbacks   = ctypes.c_void_p.in_dll(self._core_foundation, "kCFTypeDictionaryKeyCallBacks")
            value_callbacks = ctypes.c_void_p.in_dll(self._core_foundation, "kCFTypeDictionaryValueCallBacks")
        except ValueError:
            return None
        keys   = (ctypes.c_void_p * 1)(prompt_key)
        values = (ctypes.c_void_p * 1)(true_value)
        return self._core_foundation.CFDictionaryCreate(
            None, keys, values, 1,
            ctypes.byref(key_callbacks), ctypes.byref(value_callbacks),
        )

    def request_accessibility(self) -> bool:
        """Check trust, raising the system permission dialog if we are not trusted yet."""
        if self._quartz is None:
            return False
        options = self._prompt_options()
        if options is None:
            return self.is_trusted()
        trusted = bool(self._quartz.AXIsProcessTrustedWithOptions(options))
        self._release_event(options)
        return trusted

    def nudge_is_delivered(self) -> Optional[bool]:
        """Post a large move and read the cursor back — the ground truth for delivery.

        AXIsProcessTrusted can disagree with reality after a permission change,
        so this measures what macOS actually did. None if Quartz is unavailable.
        """
        if self._quartz is None:
            return None
        probe = self._quartz.CGEventCreate(None)
        if not probe:
            return None
        origin = self._quartz.CGEventGetLocation(probe)
        self._release_event(probe)

        self._post_move(origin.x + 40, origin.y)
        time.sleep(0.3)

        probe = self._quartz.CGEventCreate(None)
        landed = self._quartz.CGEventGetLocation(probe)
        self._release_event(probe)

        delivered = abs(landed.x - (origin.x + 40)) < 5
        self._post_move(origin.x, origin.y)  # put the cursor back
        return delivered

    def _post_move(self, x_position: float, y_position: float) -> None:
        event = self._quartz.CGEventCreateMouseEvent(
            None, KCG_EVENT_MOUSE_MOVED, _CGPoint(x_position, y_position), 0
        )
        if event:
            self._quartz.CGEventPost(KCG_HID_EVENT_TAP, event)
            self._release_event(event)

    def warn_if_unready(self, teams: bool) -> None:
        if not teams:
            return
        if self._quartz is None:
            print("WARNING: Quartz could not be loaded — the Teams nudge will do nothing.")
            return
        if self.request_accessibility():
            return
        print()
        print("=" * 72)
        print("  TEAMS NUDGE DISABLED — macOS Accessibility permission not granted.")
        print()
        print("  Without it macOS silently discards the mouse nudge, so Teams WILL")
        print("  still go Away. Sleep prevention is unaffected.")
        print()
        print("  Fix: System Settings -> Privacy & Security -> Accessibility, then")
        print("  add and enable the app you launch this from (Terminal, iTerm, your")
        print("  IDE). A permission dialog should have just appeared. Restart Mause")
        print("  Worker afterwards, and confirm with:  python worker.py --check")
        print("=" * 72)
        print()

    def diagnose(self) -> int:
        print(f"Backend            : {self.name}")
        caffeinate_ok = self._caffeinate_available()
        print(f"caffeinate         : {'found' if caffeinate_ok else 'NOT FOUND'}")
        print(f"Quartz             : {'loaded' if self._quartz else 'NOT LOADED'}")

        trusted = self.is_trusted()
        print(f"Accessibility      : {'granted' if trusted else 'NOT GRANTED'}")

        print("Mouse nudge        : testing (do not touch the mouse)...", end=" ", flush=True)
        delivered = self.nudge_is_delivered()
        if delivered is None:
            print("could not test")
        elif delivered:
            print("DELIVERED — Teams will be kept online")
        else:
            print("DROPPED by macOS — Teams will still go Away")

        if not delivered:
            print()
            print("Grant Accessibility to the app you launch Mause Worker from:")
            print("  System Settings -> Privacy & Security -> Accessibility")
            print("Then run this check again from that same app.")
            self.request_accessibility()
            return 1
        return 0

    @staticmethod
    def _caffeinate_available() -> bool:
        return shutil.which("caffeinate") is not None


def select_backend(force_mac: bool = False) -> SleepBackend:
    if force_mac or sys.platform == "darwin":
        return MacBackend()
    if sys.platform == "win32":
        return WindowsBackend()
    raise SystemExit(
        f"Unsupported platform: {sys.platform}. Mause Worker runs on Windows and macOS."
    )


# ----------------------------------------------------------------------
# Worker
# ----------------------------------------------------------------------

class MauseWorker:
    # Teams marks users Away after 5 minutes of no input; nudge every 4 minutes.
    _TEAMS_NUDGE_INTERVAL = 240

    def __init__(self, backend: SleepBackend, display: bool = True, teams: bool = True,
                 nudge_interval: Optional[int] = None):
        self.backend = backend
        self.display = display
        self.teams = teams
        self.nudge_interval = nudge_interval or self._TEAMS_NUDGE_INTERVAL
        self._active = False
        self._lock = threading.Lock()
        self._heartbeats_running = False

    # ------------------------------------------------------------------
    # Core state
    # ------------------------------------------------------------------

    def _apply_state(self) -> None:
        self.backend.apply_state(self.display)

    def activate(self) -> None:
        with self._lock:
            self._active = True
        self._apply_state()

    def deactivate(self) -> None:
        with self._lock:
            self._active = False
        self.backend.release()

    def is_active(self) -> bool:
        with self._lock:
            return self._active

    # ------------------------------------------------------------------
    # Heartbeats
    # ------------------------------------------------------------------

    def _heartbeat(self, interval: int = 58) -> None:
        """Re-asserts the wake lock every minute, skipping ticks while paused."""
        while True:
            time.sleep(interval)
            if self.is_active():
                self._apply_state()

    def _teams_heartbeat(self) -> None:
        """Nudges the mouse every 4 minutes to keep Teams from going Away."""
        while True:
            time.sleep(self.nudge_interval)
            if self.is_active() and self.teams:
                self.backend.nudge()

    def _start_heartbeats(self) -> None:
        """Start the daemon heartbeats once; they idle through any pause."""
        if self._heartbeats_running:
            return
        self._heartbeats_running = True
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
            draw  = ImageDraw.Draw(img)
            cup   = (255, 165, 0) if active else (110, 110, 110)
            steam = (180, 210, 255)

            # Cup body (tapered)
            draw.polygon([(14, 30), (50, 30), (46, 57), (18, 57)], fill=cup)
            # Rim
            draw.rectangle([(11, 25), (53, 32)], fill=cup)
            # Handle
            draw.arc([(46, 34), (60, 52)], start=270, end=90, fill=cup, width=4)
            # Steam wisps (only when active)
            if active:
                for x_center in (22, 32, 42):
                    draw.arc([(x_center - 5, 6), (x_center + 5, 22)], start=200, end=340,
                             fill=steam, width=2)
            return img

        def _build_menu() -> "pystray.Menu":
            return pystray.Menu(
                pystray.MenuItem(
                    lambda _: "Awake: ON" if self.is_active() else "Awake: OFF",
                    _on_toggle_awake,
                    default=True,
                ),
                pystray.Menu.SEPARATOR,
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
                pystray.MenuItem("Quit Mause Worker", _on_quit),
            )

        def _on_toggle_awake(icon, item) -> None:
            """Pause or resume without leaving — the icon dims while paused."""
            if self.is_active():
                self.deactivate()
            else:
                self.activate()
                self._start_heartbeats()
            icon.icon = _make_icon(active=self.is_active())
            icon.title = (
                "Mause Worker - keeping awake" if self.is_active()
                else "Mause Worker - paused"
            )

        def _on_toggle_display(icon, item) -> None:
            self.display = not self.display
            self._apply_state()

        def _on_toggle_teams(icon, item) -> None:
            self.teams = not self.teams

        def _on_quit(icon, _=None) -> None:
            self.deactivate()
            print("\nMause Worker deactivated. Sleep is now allowed.", flush=True)
            icon.stop()

        icon = pystray.Icon(
            name="Mause Worker",
            icon=_make_icon(active=True),
            title="Mause Worker - keeping awake",
            menu=_build_menu(),
        )

        self.activate()
        self._start_heartbeats()

        if duration_sec:
            def _auto_stop() -> None:
                time.sleep(duration_sec)
                if self.is_active():
                    _on_quit(icon)
            threading.Thread(target=_auto_stop, daemon=True).start()

        where = "menu bar" if self.backend.name == "macOS" else "system tray"
        print(f"Mause Worker active. Use the {where} icon to control it "
              f"(Quit Mause Worker closes it).", flush=True)
        icon.run()
        # run() returns once Quit stops the loop; release again in case the icon
        # was still active, then let the daemon heartbeats die with the process.
        self.deactivate()


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
        "--mac",
        action="store_true",
        help="Force the macOS backend (caffeinate + Quartz). Detected automatically otherwise.",
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
        "--interval",
        type=int,
        metavar="SECONDS",
        help="Seconds between Teams nudges (default 240). Keep it below both the "
             "Teams Away threshold and your screen-saver delay.",
    )
    parser.add_argument(
        "--check",
        action="store_true",
        help="Report whether sleep prevention and the Teams nudge can actually work, then exit.",
    )
    parser.add_argument(
        "--no-tray", "--headless",
        dest="no_tray",
        action="store_true",
        help="Run without a tray / menu bar icon (headless mode).",
    )
    args = parser.parse_args()

    backend = select_backend(force_mac=args.mac)

    if args.check:
        sys.exit(backend.diagnose())

    duration_sec = args.duration * 60 if args.duration else None
    app = MauseWorker(
        backend,
        display=not args.no_display,
        teams=not args.no_teams,
        nudge_interval=args.interval,
    )

    mode = "system only" if args.no_display else "system + display"
    teams_note = "" if args.no_teams else " + Teams online"
    suffix = f" for {args.duration} minute(s)" if args.duration else ""
    print(f"Mause Worker on {backend.name} — keeping awake ({mode}{teams_note}){suffix}.")
    backend.warn_if_unready(teams=not args.no_teams)

    if args.no_tray:
        app.run_headless(duration_sec)
    else:
        try:
            import pystray   # noqa: F401
            from PIL import Image  # noqa: F401
            app.run_tray(duration_sec)
        except ImportError:
            print("Note: install pystray and Pillow for a tray / menu bar icon.")
            print("      pip install -r requirements.txt")
            app.run_headless(duration_sec)


if __name__ == "__main__":
    main()
