# Ticketmaster Ticket Monitor

Polls a single Ticketmaster event and alerts the moment inventory appears
(including one-off drops and Face Value Exchange listings). Runs from WSL
and sends alerts via Telegram, a Windows balloon notification, and a beep.

## Requirements

- WSL2 on Windows (the balloon/beep/auto-open-URL uses `powershell.exe`
  interop). The monitor still works on plain Linux but those three channels
  become no-ops.
- Python 3.11+
- Chromium on Linux (`/usr/bin/chromium`) — needed by cortex-scout to
  render the event page past Ticketmaster's Akamai bot wall. On Debian 12:
  ```
  sudo apt-get install -y chromium
  ```
- A built `cortex-scout` binary at
  `cortex-scout/mcp-server/target/release/cortex-scout`. Without it the
  monitor falls back to API-only mode, which cannot detect low-inventory
  drops on this event (the Discovery API's `priceRanges` is empty even
  when a ticket is live on the page).
- A free Ticketmaster Discovery API key from
  https://developer.ticketmaster.com/

## Setup

```bash
# venv (built without pip, then pip is bootstrapped)
python3 -m venv --without-pip .venv
curl -sS https://bootstrap.pypa.io/get-pip.py | .venv/bin/python3
.venv/bin/pip install -r requirements.txt

# API key
export TICKETMASTER_API_KEY="your-key"   # overrides config.TM_API_KEY if set
```

Edit `config.py` to point at your event and set Telegram bot credentials.

## Running

```bash
./start.sh                       # alert on any available ticket
./start.sh --max-price 200       # only alert when lowest price <= $200
```

`start.sh` handles:
- Killing any stale cortex-scout still holding port 5000 from a prior run
- Discovering a Chromium binary and exporting `CHROME_EXECUTABLE`
- Waiting for `/health` to respond before starting the Python monitor
- Forwarding CLI args (e.g. `--max-price`) through to `monitor.py`

Stop with `Ctrl+C`; the launcher traps SIGINT/TERM and shuts cortex-scout
down cleanly.

## Configuration (`config.py`)

| Field | Purpose |
|---|---|
| `EVENT_ID` / `EVENT_URL_ID` / `EVENT_URL` / `EVENT_NAME` | Target event |
| `TM_API_KEY` | Discovery API key (env var `TICKETMASTER_API_KEY` overrides) |
| `CORTEX_SCOUT_URL` | Where the scout HTTP server listens (default `127.0.0.1:5000`) |
| `CHECK_INTERVAL_SECS` / `JITTER_SECS` | Poll cadence; 20s + 5s jitter keeps API quota safe and matches scout cycle time |
| `NOTIFY_SOUND` / `NOTIFY_DESKTOP` | Toggle Windows beep / balloon |
| `NOTIFY_REPEAT_INTERVAL_SECS` | Suppress repeat alerts within this window |
| `TELEGRAM_ENABLED` / `TELEGRAM_BOT_TOKEN` / `TELEGRAM_CHAT_ID` | Telegram channel config |

## How it works

Two data sources run every cycle:

1. **Discovery API** (`check_via_api`) — reads `dates.status.code`,
   `sales.public.*`, and `priceRanges`. Authoritative for the sale window
   state; for `onsale` it treats missing `priceRanges` as sold out.
2. **cortex-scout** (`check_via_cortex_scout`) — drives a real Chromium
   via `browser_automate`: navigate to `ticketmaster.com` (warms Akamai
   cookies in the persistent profile at `~/.cortex-scout/agent_profile`),
   navigate to the event URL, snapshot the `body`. The snapshot text is
   classified by looking for a `$NN.NN` price plus section/row markers,
   or explicit phrases like "Face Value Exchange Ticket".

Result precedence in `run_check()`: any `AVAILABLE` from any source wins;
otherwise the first non-`UNKNOWN` result; otherwise `UNKNOWN`.

When `AVAILABLE`, `notify()` fires Telegram + Windows balloon + beep +
auto-opens the event URL in the default browser. Each channel is isolated
in a try/except so a failing notifier cannot kill the polling loop.

## Troubleshooting

**`No Brave/Chrome/Chromium executable found`**
Install chromium (`sudo apt-get install -y chromium`). cortex-scout
auto-discovers `/usr/bin/chromium`.

**`Address already in use: 0.0.0.0:5000`**
A previous cortex-scout is still running (possibly in stopped `T` state
after a Ctrl+Z). `start.sh` now SIGKILLs stragglers automatically; if
that fails, run `pkill -9 -f target/release/cortex-scout`.

**`CDP fetch hit challenge iframe/content signature`**
Akamai bot wall. Harmless — the monitor uses `browser_automate` (with a
persistent warm profile) which gets past it. The warning is from an
unrelated `scrape_url` code path and can be ignored.

**Monitor alert for a ticket but the listing is gone when I click**
Drops on this event sell in seconds. Keep the auto-opened browser tab
ready to click through checkout.

**`subprocess.TimeoutExpired` from PowerShell**
Fixed — notifications are now fire-and-forget via `Popen`. If you see
this again, pull the latest `monitor.py`.
