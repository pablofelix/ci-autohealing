#!/usr/bin/env bash
# test-watchdog.sh — Continuous test runner using inotifywait
# Watches src/ for Python changes and runs affected tests.
# Uses a PID lockfile to avoid overlapping runs.
#
# Usage:
#   ./scripts/test-watchdog.sh          # Start watching
#   ./scripts/test-watchdog.sh stop     # Stop the daemon
#   ./scripts/test-watchdog.sh status   # Check if running
#
# Requires: inotify-tools (sudo dnf install inotify-tools)

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="$(dirname "$SCRIPT_DIR")"
LOCKFILE="$PROJECT_DIR/.test-results/watchdog.pid"
LOGFILE="$PROJECT_DIR/.test-results/watchdog.log"
RESULTS="$PROJECT_DIR/.test-results/watchdog-results.txt"

mkdir -p "$PROJECT_DIR/.test-results"

_log() { echo "[$(date '+%H:%M:%S')] $*" >> "$LOGFILE"; }

_is_running() {
    if [ -f "$LOCKFILE" ]; then
        local pid
        pid=$(cat "$LOCKFILE")
        if kill -0 "$pid" 2>/dev/null; then
            return 0
        fi
        rm -f "$LOCKFILE"
    fi
    return 1
}

cmd_stop() {
    if _is_running; then
        local pid
        pid=$(cat "$LOCKFILE")
        kill "$pid" 2>/dev/null || true
        rm -f "$LOCKFILE"
        echo "Watchdog stopped (pid $pid)"
    else
        echo "Watchdog is not running"
    fi
}

cmd_status() {
    if _is_running; then
        local pid
        pid=$(cat "$LOCKFILE")
        echo "Watchdog running (pid $pid)"
        if [ -f "$RESULTS" ]; then
            echo "Last result:"
            tail -5 "$RESULTS"
        fi
    else
        echo "Watchdog is not running"
    fi
}

_find_test_for() {
    local changed_file="$1"
    local basename
    basename=$(basename "$changed_file" .py)

    # If it's already a test file, run it directly
    if [[ "$basename" == test_* ]]; then
        echo "$changed_file"
        return
    fi

    # Look for corresponding test file
    local test_file="$PROJECT_DIR/src/tests/test_${basename}.py"
    if [ -f "$test_file" ]; then
        echo "$test_file"
        return
    fi

    # Fallback: run all unit tests
    echo "$PROJECT_DIR/src/tests/"
}

_run_tests() {
    local target="$1"
    local start
    start=$(date +%s)

    _log "Running: pytest $target"

    local result
    if python3 -m pytest "$target" -x -q --tb=short -m "not slow" --ignore=src/tests/e2e 2>&1; then
        result="PASS"
    else
        result="FAIL"
    fi

    local elapsed=$(( $(date +%s) - start ))
    local summary="[$result] $(date -Iseconds) ${target##*/} (${elapsed}s)"
    echo "$summary" >> "$RESULTS"
    _log "$summary"

    # Keep only last 50 results
    if [ -f "$RESULTS" ] && [ "$(wc -l < "$RESULTS")" -gt 50 ]; then
        tail -50 "$RESULTS" > "$RESULTS.tmp" && mv "$RESULTS.tmp" "$RESULTS"
    fi
}

cmd_watch() {
    if ! command -v inotifywait > /dev/null 2>&1; then
        echo "inotify-tools is not installed."
        echo "Install: sudo dnf install inotify-tools"
        exit 1
    fi

    if _is_running; then
        echo "Watchdog is already running (pid $(cat "$LOCKFILE"))"
        exit 1
    fi

    echo "Starting test watchdog (watching src/ for .py changes)..."
    echo "Log: $LOGFILE"
    echo "Results: $RESULTS"
    echo "Stop with: $0 stop"

    echo $$ > "$LOCKFILE"
    _log "Watchdog started (pid $$)"

    trap 'rm -f "$LOCKFILE"; _log "Watchdog stopped"; exit 0' INT TERM

    # Debounce: wait 2s after last change before running tests
    local last_run=0

    inotifywait -m -r -e modify,create --include '\.py$' "$PROJECT_DIR/src/" 2>/dev/null | \
    while read -r dir event file; do
        local now
        now=$(date +%s)

        # Debounce: skip if we ran less than 2s ago
        if (( now - last_run < 2 )); then
            continue
        fi
        last_run=$now

        local changed="${dir}${file}"
        _log "Changed: $changed"

        local test_target
        test_target=$(_find_test_for "$changed")

        _run_tests "$test_target" &
        wait $! 2>/dev/null || true
    done
}

case "${1:-watch}" in
    watch)  cmd_watch ;;
    stop)   cmd_stop ;;
    status) cmd_status ;;
    *)
        echo "Usage: $0 [watch|stop|status]"
        exit 1
        ;;
esac
