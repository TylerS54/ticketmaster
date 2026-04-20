# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this is

A single-purpose Python polling monitor that watches one Ticketmaster event and alerts when tickets become available. Target event is hard-coded in `config.py` (currently Noah Kahan, Philadelphia, 2026-06-26). Runs from WSL and uses PowerShell interop for Windows-side alerts.

## Running

```bash
# One-time setup (venv is intentionally built without pip, then pip is bootstrapped)
python3 -m venv --without-pip .venv
curl -sS https://bootstrap.pypa.io/get-pip.py | .venv/bin/python3
.venv/bin/pip install -r requirements.txt

# Run (launcher handles venv activation + optional cortex-scout)
export TICKETMASTER_API_KEY="..."   # optional; falls back to config.TM_API_KEY
./start.sh

# Or run the monitor directly
.venv/bin/python3 monitor.py
```

There is no test suite, lint config, or build step. `requirements.txt` has a single dependency (`requests`).

## Architecture

Two data sources, combined in `run_check()` (monitor.py:406):

1. **Ticketmaster Discovery API** (`check_via_api`) — primary. Authoritative for sale-window state (`offsale`/`cancelled`/`postponed`/`rescheduled`). For `onsale`, the API only reports that the *sale window* is open, not that inventory exists, so availability is inferred from the presence of `priceRanges` in the response. No prices → treated as sold out.
2. **cortex-scout** (`check_via_cortex_scout`) — optional secondary. HTTP MCP scraper at `CORTEX_SCOUT_URL` (default `127.0.0.1:5000`). Health-checked before use; scrapes the event page and keyword-matches for sold-out vs. purchase-flow signals. Binary lives under `cortex-scout/` (gitignored, Rust build under `mcp-server/target/release/cortex-scout`) and is started by `start.sh` when present.

Result precedence: any `AVAILABLE` wins; otherwise the first non-`UNKNOWN` result is returned. A 429 from the Discovery API maps to `UNKNOWN` (retry next cycle), not a failure.

The main loop polls every `CHECK_INTERVAL_SECS` + `JITTER_SECS` random jitter and suppresses repeat `AVAILABLE` notifications within `NOTIFY_REPEAT_INTERVAL_SECS`.

## Notification channels

All driven from `notify()` (monitor.py:373):

- **Telegram** via bot API (token + chat ID in `config.py`)
- **Windows balloon** and **beep pattern** via `powershell.exe` invoked through WSL interop (`_wsl_powershell`)
- **Auto-open event URL** in the Windows default browser via `Start-Process`
- Terminal bell as last-resort backup

The WSL → PowerShell bridge is load-bearing for desktop alerts; changes that break `powershell.exe` availability degrade silently (see the `FileNotFoundError` guard).

## Conventions specific to this repo

- `config.py` currently holds secrets in source (Ticketmaster API key, Telegram bot token, chat ID). Env var `TICKETMASTER_API_KEY` overrides `config.TM_API_KEY` but the other credentials have no env fallback. Do not commit new secrets; when editing, preserve the env-var-first pattern.
- `.gitignore` excludes the entire `cortex-scout/` directory — it's an external dependency cloned locally, not part of this project's source.
- Result objects (`CheckResult`) are frozen dataclasses; keep them immutable.
