#!/usr/bin/env bash
#
# Mause Worker background control script.
#
#   mause [worker options]             Start in the background (the default)
#   mause stop                         Stop it (clean shutdown)
#   mause status                       Is it running, and what is it holding?
#   mause check                        Can the Teams nudge actually work here?
#   mause restart [worker options]     Stop then start
#   mause log                          Follow the log (Ctrl+C to stop following)
#   mause help                         This text
#
# Any worker option is forwarded: mause --no-display --duration 120
# The menu bar icon is shown by default; pass --headless to run without it.
#
# Works the same as ./mause.sh from the project directory, or as `mause` when
# installed on PATH:  ./install.sh

set -euo pipefail

# Resolve where this script really lives, following any symlink chain, so the
# command can sit on PATH as `mause` while .venv and worker.py stay beside the
# original file. Without this, a symlinked launcher would look for the
# virtualenv next to the symlink.
resolve_script_path() {
    local source="${BASH_SOURCE[0]}"
    local containing_dir
    while [[ -L "$source" ]]; do
        containing_dir="$(cd -- "$(dirname -- "$source")" && pwd -P)"
        source="$(readlink -- "$source")"
        [[ "$source" != /* ]] && source="$containing_dir/$source"
    done
    containing_dir="$(cd -- "$(dirname -- "$source")" && pwd -P)"
    printf '%s\n' "$containing_dir/$(basename -- "$source")"
}

SCRIPT_PATH="$(resolve_script_path)"
SCRIPT_DIR="$(dirname -- "$SCRIPT_PATH")"

# How this was invoked, so the hints we print back are copy-pasteable: `mause`
# when run from PATH, `./mause.sh` when run from the project directory.
INVOKED_AS="$(basename -- "$0")"
[[ "$INVOKED_AS" == *.sh ]] && INVOKED_AS="./$INVOKED_AS"
RUN_DIR="$SCRIPT_DIR/.run"
PID_FILE="$RUN_DIR/worker.pid"
LOG_FILE="$RUN_DIR/worker.log"
VENV_PYTHON="$SCRIPT_DIR/.venv/bin/python"
WORKER_SCRIPT="$SCRIPT_DIR/worker.py"
SHUTDOWN_GRACE_SECONDS=10


usage() {
    # Print the header comment block, stopping at the first line of real code,
    # with the examples rewritten to match how this was invoked.
    awk 'NR > 2 { if (/^#/) { sub(/^# ?/, ""); print } else { exit } }' "$SCRIPT_PATH" \
        | sed -e "s|^  mause |  $INVOKED_AS |" -e "s|: mause |: $INVOKED_AS |"
}


# Echo the recorded PID, but only if that process is alive and really is our
# worker — a stale PID file can otherwise point at an unrelated recycled PID.
worker_pid() {
    [[ -f "$PID_FILE" ]] || return 1

    local recorded_pid
    recorded_pid="$(cat "$PID_FILE" 2>/dev/null || true)"
    [[ "$recorded_pid" =~ ^[0-9]+$ ]] || return 1

    local command_line
    command_line="$(ps -p "$recorded_pid" -o args= 2>/dev/null || true)"
    [[ "$command_line" == *"worker.py"* ]] || return 1

    echo "$recorded_pid"
}


start() {
    local existing_pid
    if existing_pid="$(worker_pid)"; then
        echo "Mause Worker is already running (pid $existing_pid). Use '$INVOKED_AS restart' to reload options."
        return 0
    fi

    if [[ ! -x "$VENV_PYTHON" ]]; then
        echo "No virtualenv found at $SCRIPT_DIR/.venv" >&2
        echo "Create one first:  python3 -m venv .venv && .venv/bin/pip install -r requirements.txt" >&2
        return 1
    fi

    # The menu bar icon is the point of running this in the background — it is
    # how you see that it is on and how you quit it. --headless (or the worker's
    # own --no-tray) opts out.
    local forwarded=()
    local want_tray=1
    local argument
    for argument in "$@"; do
        case "$argument" in
            --tray)                want_tray=1 ;;
            --headless|--no-tray)  want_tray=0 ;;
            *)                     forwarded+=("$argument") ;;
        esac
    done
    if [[ $want_tray -eq 0 ]]; then
        forwarded+=("--no-tray")
    fi

    mkdir -p "$RUN_DIR"
    {
        echo
        echo "=== started $(date '+%Y-%m-%d %H:%M:%S') ==="
    } >>"$LOG_FILE"

    # -u keeps the log unbuffered, so 'status' and 'log' show output immediately.
    nohup "$VENV_PYTHON" -u "$WORKER_SCRIPT" ${forwarded[@]+"${forwarded[@]}"} >>"$LOG_FILE" 2>&1 &
    local new_pid=$!
    echo "$new_pid" >"$PID_FILE"

    # Give it a moment to fail loudly (bad option, missing dependency) rather
    # than reporting a PID that is already gone.
    sleep 2
    if ! kill -0 "$new_pid" 2>/dev/null; then
        rm -f "$PID_FILE"
        echo "Mause Worker failed to start. Last lines of $LOG_FILE:" >&2
        tail -n 15 "$LOG_FILE" >&2
        return 1
    fi

    echo "Mause Worker started (pid $new_pid)."
    if [[ $want_tray -eq 1 ]]; then
        echo "  quit:  click the coffee cup in the menu bar -> Quit Mause Worker"
    fi
    echo "  stop:  $INVOKED_AS stop"
    echo "  log:   $INVOKED_AS log"
}


stop() {
    local running_pid
    if ! running_pid="$(worker_pid)"; then
        echo "Mause Worker is not running."
        rm -f "$PID_FILE"
        return 0
    fi

    # SIGTERM triggers the worker's own shutdown handler, which drops the wake
    # lock before exiting.
    kill -TERM "$running_pid" 2>/dev/null || true

    local waited=0
    while kill -0 "$running_pid" 2>/dev/null && [[ $waited -lt $SHUTDOWN_GRACE_SECONDS ]]; do
        sleep 1
        waited=$((waited + 1))
    done

    if kill -0 "$running_pid" 2>/dev/null; then
        echo "Did not exit within ${SHUTDOWN_GRACE_SECONDS}s — sending SIGKILL."
        kill -KILL "$running_pid" 2>/dev/null || true
        sleep 1
    fi

    rm -f "$PID_FILE"
    echo "Mause Worker stopped (pid $running_pid). Sleep is allowed again."
}


status() {
    local running_pid
    if ! running_pid="$(worker_pid)"; then
        echo "Mause Worker: not running."
        [[ -f "$LOG_FILE" ]] && echo "Log: $LOG_FILE"
        return 1
    fi

    echo "Mause Worker: running"
    echo "  pid:     $running_pid"
    echo "  uptime:  $(ps -p "$running_pid" -o etime= | tr -d ' ')"
    echo "  command: $(ps -p "$running_pid" -o args= | sed "s|$SCRIPT_DIR/||g")"
    echo "  log:     $LOG_FILE"

    if [[ "$(uname -s)" == "Darwin" ]]; then
        # Report only the assertions held by our own caffeinate child; the
        # system has plenty of unrelated ones.
        local helper_pid
        helper_pid="$(pgrep -P "$running_pid" caffeinate 2>/dev/null | head -n 1 || true)"
        if [[ -z "$helper_pid" ]]; then
            echo "  holding: no caffeinate helper found (unexpected)"
        else
            local assertions
            assertions="$(pmset -g assertions \
                | grep -E "^ *pid $helper_pid\(caffeinate\)" \
                | sed -E 's/.*\] [0-9:]+ ([A-Za-z]+) named.*/\1/' \
                | paste -sd ',' - | sed 's/,/, /g')"
            echo "  holding: ${assertions:-none}"
        fi
    fi
}


check() {
    if [[ ! -x "$VENV_PYTHON" ]]; then
        echo "No virtualenv found at $SCRIPT_DIR/.venv" >&2
        return 1
    fi
    "$VENV_PYTHON" "$WORKER_SCRIPT" --check
}


follow_log() {
    if [[ ! -f "$LOG_FILE" ]]; then
        echo "No log yet at $LOG_FILE — run '$INVOKED_AS' first."
        return 1
    fi
    tail -n 20 -f "$LOG_FILE"
}


# Starting is the common case, so a bare `mause` starts it. A leading option
# means the same thing with options: `mause --duration 60`.
command_name="${1:-start}"
case "$command_name" in
    -h|--help|help) usage; exit 0 ;;
    -*)             command_name="start" ;;
    *)              [[ $# -gt 0 ]] && shift ;;
esac

case "$command_name" in
    start)   start "$@" ;;
    stop)    stop ;;
    restart) stop; start "$@" ;;
    status)  status ;;
    check)   check ;;
    log)     follow_log ;;
    *)
        echo "Unknown command: $command_name" >&2
        echo >&2
        usage >&2
        exit 2
        ;;
esac
