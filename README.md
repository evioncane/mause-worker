# Mause Worker

Prevents your Windows computer from sleeping and keeps your Microsoft Teams status **Online** — no configuration required.

## How it works

- Uses the Windows `SetThreadExecutionState` API to block system sleep
- Sends an imperceptible mouse nudge every 4 minutes via `SendInput` to reset Teams' idle timer
- Runs as a system tray icon with toggles for each feature

## Requirements

- Windows 10 / 11
- Python 3.8+
- Dependencies:

```
pip install -r requirements.txt
```

## Usage

```bash
# Default: keep system + display awake, Teams online (system tray icon)
python worker.py

# Allow display to sleep, but keep system and Teams awake
python worker.py --no-display

# Sleep prevention only, skip the Teams nudge
python worker.py --no-teams

# Automatically stop after N minutes
python worker.py --duration 60

# Headless mode (no tray icon) — press Ctrl+C to stop
python worker.py --no-tray
```

## Tray icon

Right-click the tray icon to toggle options or quit:

| Option | Default |
|---|---|
| Keep display on | ✅ on |
| Keep Teams online | ✅ on |
| Quit | — |
