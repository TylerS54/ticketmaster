#!/usr/bin/env bash
#
# Ticketmaster Ticket Monitor - Launcher
#
# Starts cortex-scout (if available) and the Python monitor.
#
# Usage:
#   export TICKETMASTER_API_KEY="your-key"
#   ./start.sh                       # alert on any available ticket
#   ./start.sh --max-price 200       # only alert when lowest price <= $200
#
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
VENV_DIR="$SCRIPT_DIR/.venv"
SCOUT_BIN="$SCRIPT_DIR/cortex-scout/mcp-server/target/release/cortex-scout"
SCOUT_PID=""

cleanup() {
    echo ""
    echo "[launcher] Shutting down..."
    if [ -n "$SCOUT_PID" ] && kill -0 "$SCOUT_PID" 2>/dev/null; then
        echo "[launcher] Stopping cortex-scout (PID $SCOUT_PID)"
        kill "$SCOUT_PID" 2>/dev/null || true
        wait "$SCOUT_PID" 2>/dev/null || true
    fi
    echo "[launcher] Done."
}

trap cleanup EXIT INT TERM

# Check API key
if [ -z "${TICKETMASTER_API_KEY:-}" ]; then
    echo "WARNING: TICKETMASTER_API_KEY not set."
    echo "Get a free key at: https://developer.ticketmaster.com/"
    echo "Set it with: export TICKETMASTER_API_KEY=\"your-key\""
    echo ""
fi

# Activate venv
if [ -d "$VENV_DIR" ]; then
    source "$VENV_DIR/bin/activate"
else
    echo "ERROR: Virtual environment not found at $VENV_DIR"
    echo "Run: python3 -m venv --without-pip $VENV_DIR"
    echo "     curl -sS https://bootstrap.pypa.io/get-pip.py | $VENV_DIR/bin/python3"
    echo "     $VENV_DIR/bin/pip install -r requirements.txt"
    exit 1
fi

# Discover a Chrome/Chromium binary. cortex-scout's auto-discovery misses
# Debian's chromium-headless-shell, so we probe common locations ourselves
# and export CHROME_EXECUTABLE as an explicit override when found.
find_chrome() {
    local candidates=(
        "${CHROME_EXECUTABLE:-}"
        "$(command -v brave-browser 2>/dev/null || true)"
        "$(command -v google-chrome 2>/dev/null || true)"
        "$(command -v chromium 2>/dev/null || true)"
        "$(command -v chromium-browser 2>/dev/null || true)"
        /usr/bin/chromium
        /usr/bin/chromium-browser
        /usr/bin/google-chrome
        /usr/bin/brave-browser
    )
    for c in "${candidates[@]}"; do
        [ -n "$c" ] && [ -x "$c" ] && { echo "$c"; return 0; }
    done
    return 1
}

# Start cortex-scout if binary exists and a real browser is available
if [ -x "$SCOUT_BIN" ]; then
    # Kill any stale cortex-scout still holding port 5000 from a prior run.
    # Use SIGKILL because stopped (T-state) processes ignore SIGTERM, and a
    # stopped scout still pins its listening socket.
    if ss -tln 2>/dev/null | grep -q ":5000 "; then
        echo "[launcher] Port 5000 busy, killing stale cortex-scout..."
        pkill -9 -f "target/release/cortex-scout" 2>/dev/null || true
        # Also free the port if something else holds it (best-effort)
        fuser -k -9 5000/tcp 2>/dev/null || true
        sleep 2
    fi

    if CHROME_BIN="$(find_chrome)"; then
        echo "[launcher] Using browser: $CHROME_BIN"
        export CHROME_EXECUTABLE="$CHROME_BIN"
        export RUST_LOG=warn
        export CORTEX_SCOUT_TOOL_TIMEOUT_SECS=90
        export CORTEX_SCOUT_BROWSER_LAUNCH_TIMEOUT_SECS=20
        export CORTEX_SCOUT_SCRAPE_STAGE_TIMEOUT_SECS=30
        cd "$SCRIPT_DIR"
        "$SCOUT_BIN" &
        SCOUT_PID=$!
        echo "[launcher] cortex-scout started (PID $SCOUT_PID), waiting for /health..."
        # Poll the health endpoint instead of a blind sleep
        for _ in $(seq 1 20); do
            if curl -s -o /dev/null --max-time 1 http://127.0.0.1:5000/health 2>/dev/null; then
                echo "[launcher] cortex-scout ready."
                break
            fi
            sleep 0.5
        done
    else
        echo "[launcher] No Chrome/Chromium/Brave binary found."
        echo "[launcher] Install one to enable scout scraping:"
        echo "           sudo apt-get install -y chromium"
        echo "[launcher] Running in API-only mode."
    fi
else
    echo "[launcher] cortex-scout binary not found at $SCOUT_BIN"
    echo "[launcher] Running in API-only mode (build cortex-scout for enhanced scraping)"
fi

# Start the monitor (forward any CLI args, e.g. --max-price 200)
echo "[launcher] Starting ticket monitor..."
cd "$SCRIPT_DIR"
python3 monitor.py "$@"
