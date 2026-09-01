#!/usr/bin/env bash
#
# Mause Worker background control script.
#
#   ./mause.sh start [worker options]   Start in the background
#   ./mause.sh stop                     Stop it (clean shutdown)
#   ./mause.sh status                   Is it running, and what is it holding?
#   ./mause.sh check                    Can the Teams nudge actually work here?
#   ./mause.sh restart [worker options] Stop then start
#   ./mause.sh log                      Follow the log (Ctrl+C to stop following)
#
# Any worker option is forwarded: ./mause.sh start --no-display --duration 120
# Pass --tray to keep the menu bar icon; the default is headless.

set -euo pipefail

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)"
RUN_DIR="$SCRIPT_DIR/.run"
PID_FILE="$RUN_DIR/worker.pid"
LOG_FILE="$RUN_DIR/worker.log"
VENV_PYTHON="$SCRIPT_DIR/.venv/bin/python"
WORKER_SCRIPT="$SCRIPT_DIR/worker.py"
SHUTDOWN_GRACE_SECONDS=10


usage() {
    # Print the header comment block, stopping at the first line of real code.
    awk 'NR > 2 { if (/^#/) { sub(/^# ?/, ""); print } else { exit } }' "${BASH_SOURCE[0]}"
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
        echo "Mause Worker is already running (pid $existing_pid). Use './mause.sh restart' to reload options."
        return 0
    fi

    if [[ ! -x "$VENV_PYTHON" ]]; then
        echo "No virtualenv found at $SCRIPT_DIR/.venv" >&2
        echo "Create one first:  python3 -m venv .venv && .venv/bin/pip install -r requirements.txt" >&2
        return 1
    fi

    # Headless by default — a backgrounded process is driven by this script,
    # not by a menu bar icon. --tray opts back in.
    local forwarded=()
    local want_tray=0
    local argument
    for argument in "$@"; do
        if [[ "$argument" == "--tray" ]]; then
            want_tray=1
        else
            forwarded+=("$argument")
        fi
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
    echo "  stop:  ./mause.sh stop"
    echo "  log:   ./mause.sh log"
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
        echo "No log yet at $LOG_FILE — start Mause Worker first."
        return 1
    fi
    tail -n 20 -f "$LOG_FILE"
}


command_name="${1:-}"
[[ $# -gt 0 ]] && shift

case "$command_name" in
    start)   start "$@" ;;
    stop)    stop ;;
    restart) stop; start "$@" ;;
    status)  status ;;
    check)   check ;;
    log)     follow_log ;;
    ""|-h|--help|help) usage ;;
    *)
        echo "Unknown command: $command_name" >&2
        echo >&2
        usage >&2
        exit 2
        ;;
esac
