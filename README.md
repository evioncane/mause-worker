# Mause Worker

Prevents your computer from sleeping and keeps your Microsoft Teams status **Online** — no configuration required. Runs on **Windows** and **macOS**.

## How it works

| | Windows | macOS |
|---|---|---|
| Block sleep | `SetThreadExecutionState` API | a managed `caffeinate` process |
| Keep Teams online | imperceptible mouse nudge via `SendInput` | imperceptible mouse nudge via a Quartz `CGEvent` |

The nudge is a +1 / -1 pixel move sent every 4 minutes, which resets the OS idle timer that Teams reads to decide you are Away.

Both platforms run as a tray / menu bar icon with toggles for each feature.

## Requirements

- Windows 10 / 11, or macOS 12+
- Python 3.8+
- Dependencies:

```
pip install -r requirements.txt
```

### macOS: Accessibility permission (required for Teams)

**Without this, Teams will still go Away.** macOS silently discards synthetic mouse events from untrusted processes, so the nudge does nothing and you get no error — sleep prevention keeps working, which makes the failure easy to miss.

1. Run the check:

   ```bash
   mause check               # or: python worker.py --check
   ```

   A macOS permission dialog appears the first time.

2. Open **System Settings → Privacy & Security → Accessibility** and enable the app you launch Mause Worker from — the **terminal** (Terminal, iTerm, your IDE), not `python`. macOS attributes the permission to the launching app.

3. Run the check again from that same app. You want:

   ```
   Mouse nudge        : DELIVERED — Teams will be kept online
   ```

If you launch from a different terminal later, grant it there too. Use `--no-teams` if you only want sleep prevention and don't care about presence.

### macOS: screen lock

Teams also reports Away once the screen locks, which `caffeinate` does not prevent. The nudge normally stops the screen saver from ever starting, but if your lock delay is shorter than the nudge interval, lower the interval:

```bash
mause --interval 120
```

## Usage

The platform is detected automatically — `--mac` just forces the macOS backend.

```bash
# Default: keep system + display awake, Teams online (tray / menu bar icon)
python worker.py

# Force the macOS backend
python worker.py --mac

# Allow display to sleep, but keep system and Teams awake
python worker.py --no-display

# Sleep prevention only, skip the Teams nudge
python worker.py --no-teams

# Automatically stop after N minutes
python worker.py --duration 60

# Headless mode (no icon) — press Ctrl+C to stop
python worker.py --no-tray

# Check that the Teams nudge can actually work on this machine
python worker.py --check

# Nudge every 2 minutes instead of the default 4
python worker.py --interval 120
```

## Installing the `mause` command

Put `mause` on your PATH so you can start it from any directory:

```bash
./install.sh
```

This symlinks `mause.sh` into `~/.local/bin/mause`, adding that directory to your
PATH in `~/.zshrc` if it is not there already (it backs the file up first, and
inserts above any pinned trailing block such as SDKMAN's). Open a new terminal
afterwards. To choose a different directory, or to undo it:

```bash
MAUSE_BIN_DIR=/opt/homebrew/bin ./install.sh
./install.sh --uninstall            # remove the symlink
```

The symlink points back at this checkout rather than copying it, so `git pull`
updates the command — and moving the project directory breaks the link.

## Running in the background

Mause Worker runs as a detached background process, so it survives closing the
terminal. Typing `mause` on its own starts it:

```bash
mause             # start in the background (with the menu bar icon)
mause stop        # stop it
mause status      # running? for how long? what is it holding?
mause check       # verify the Teams nudge is actually delivered
mause restart     # stop, then start
mause log         # follow the log (Ctrl+C stops following, not the worker)
mause help        # the same list
```

Without installing, `./mause.sh` from the project directory takes exactly the
same commands — `./mause.sh`, `./mause.sh stop`, and so on. The explicit
`mause start` spelling still works too.

Any worker option is forwarded to `worker.py`:

```bash
mause --no-display --duration 120
mause --no-teams
mause --headless      # no menu bar icon (the icon is shown by default)
```

The PID and log live in `.run/` next to the script (gitignored). Starting twice is a no-op — it reports the running PID instead of launching a second copy. Use `restart` to change options.

### Stopping it

```bash
mause stop
```

This sends `SIGTERM`, which lets the worker drop its wake lock cleanly, then waits up to 10 seconds before escalating to `SIGKILL`. Confirm with:

```bash
mause status                           # -> "not running"
pmset -g assertions | grep caffeinate  # -> no entry for the worker
```

**If you lose the PID file** (deleted `.run/`, or you started `worker.py` by hand):

```bash
pkill -f "worker.py"      # stop every Mause Worker instance
```

You can also quit from the menu bar icon itself: click the coffee cup and choose
**Quit Mause Worker**. That drops the wake lock and exits the process, exactly as
`mause stop` does. **If you started it in the foreground**, `Ctrl+C` works too.

You never need to kill `caffeinate` yourself. It is started with `-w <worker pid>`, so it exits with the worker even if the worker is `SIGKILL`ed — no power assertion is ever left behind.

## Tray / menu bar icon

The icon is on by default, both in the foreground and via `mause`.
Right-click it on Windows, or click it on macOS, to toggle options or quit:

| Item | Does |
|---|---|
| Awake: ON / OFF | Pauses or resumes without quitting; the cup greys out while paused |
| Keep display on | Toggles display sleep prevention (on by default) |
| Keep Teams online | Toggles the mouse nudge (on by default) |
| Quit Mause Worker | Drops the wake lock and closes the program |

## Troubleshooting

**Teams still goes Away.** Run `mause check`. If it reports `DROPPED by macOS`, the Accessibility permission is missing — see above. This is by far the most common cause.

**It works in the foreground but not in the background.** The background process inherits the Accessibility grant of the terminal that started it. Grant permission to that terminal, then `mause restart`.

## Notes

- On macOS the `caffeinate` helper is started with `-w <pid>`, so it exits with Mause Worker even if the process is killed — no assertion is ever left behind. Check with `pmset -g assertions`.
- `-s` (prevent system sleep) only applies while on AC power; on battery macOS still honours your normal sleep settings.
