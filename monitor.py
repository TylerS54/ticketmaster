#!/usr/bin/env python3
"""
Ticketmaster Ticket Availability Monitor

Polls the Ticketmaster Discovery API (and optionally cortex-scout) to detect
when tickets become available for a sold-out event. Sends desktop notifications
and plays an alert sound when tickets are found.

Usage:
    export TICKETMASTER_API_KEY="your-key-here"
    python3 monitor.py

Get a free API key at: https://developer.ticketmaster.com/
"""

import argparse
import json
import logging
import os
import random
import re
import shutil
import subprocess
import sys
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from enum import Enum
from typing import Optional

import requests

import config

# Cached once at import — powershell.exe only exists under WSL interop. On a
# plain Linux host (EC2, bare metal) this is None and all desktop-notification
# paths short-circuit without logging warnings every cycle.
_POWERSHELL_EXE = shutil.which("powershell.exe")


def _env_bool(name: str, default: Optional[bool] = None) -> Optional[bool]:
    """Parse a tri-state boolean env var. Returns None if unset/blank."""
    raw = os.environ.get(name, "").strip().lower()
    if raw in ("1", "true", "yes", "on"):
        return True
    if raw in ("0", "false", "no", "off"):
        return False
    return default

# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
log = logging.getLogger("tm-monitor")

# ---------------------------------------------------------------------------
# Types
# ---------------------------------------------------------------------------

class TicketStatus(Enum):
    AVAILABLE = "available"
    SOLD_OUT = "sold_out"
    OFF_SALE = "off_sale"
    CANCELLED = "cancelled"
    UNKNOWN = "unknown"


@dataclass(frozen=True)
class CheckResult:
    status: TicketStatus
    source: str  # "api" or "cortex-scout"
    message: str
    raw_data: Optional[dict] = None
    price_range: Optional[str] = None
    ticket_limit: Optional[int] = None
    # Lowest ticket price visible on the page/API, in USD. Used for
    # `--max-price` threshold filtering.
    lowest_price_usd: Optional[float] = None


# ---------------------------------------------------------------------------
# API Key
# ---------------------------------------------------------------------------

def get_api_key() -> str:
    key = os.environ.get("TICKETMASTER_API_KEY", "").strip()
    if not key:
        key = config.TM_API_KEY.strip()
    return key


def get_telegram_bot_token() -> str:
    return (
        os.environ.get("TELEGRAM_BOT_TOKEN", "").strip()
        or config.TELEGRAM_BOT_TOKEN.strip()
    )


def get_telegram_chat_id() -> str:
    return (
        os.environ.get("TELEGRAM_CHAT_ID", "").strip()
        or str(config.TELEGRAM_CHAT_ID).strip()
    )


def telegram_enabled() -> bool:
    override = _env_bool("TELEGRAM_ENABLED")
    if override is not None:
        return override
    return config.TELEGRAM_ENABLED


def desktop_notifications_enabled() -> bool:
    """Windows balloon/beep/URL-open only fire when WSL PowerShell is present.

    Env var NOTIFY_DESKTOP (0/1) overrides config.NOTIFY_DESKTOP. Even when
    explicitly enabled, we still need powershell.exe to actually exist —
    otherwise Popen would raise FileNotFoundError every cycle.
    """
    if not _POWERSHELL_EXE:
        return False
    override = _env_bool("NOTIFY_DESKTOP")
    if override is not None:
        return override
    return config.NOTIFY_DESKTOP


def sound_notifications_enabled() -> bool:
    """Beep runs through PowerShell too, so same gating as desktop."""
    if not _POWERSHELL_EXE:
        return False
    override = _env_bool("NOTIFY_SOUND")
    if override is not None:
        return override
    return config.NOTIFY_SOUND


# ---------------------------------------------------------------------------
# Ticketmaster Discovery API checker
# ---------------------------------------------------------------------------

def check_via_api(api_key: str) -> CheckResult:
    """Query the Ticketmaster Discovery API for event ticket status."""
    url = f"{config.TM_API_BASE}/events/{config.EVENT_ID}.json"
    params = {"apikey": api_key}

    try:
        resp = requests.get(url, params=params, timeout=15)
        resp.raise_for_status()
    except requests.exceptions.HTTPError as exc:
        if exc.response is not None and exc.response.status_code == 429:
            return CheckResult(
                status=TicketStatus.UNKNOWN,
                source="api",
                message="Rate limited by Ticketmaster API, will retry next cycle",
            )
        return CheckResult(
            status=TicketStatus.UNKNOWN,
            source="api",
            message=f"API HTTP error: {exc}",
        )
    except requests.exceptions.RequestException as exc:
        return CheckResult(
            status=TicketStatus.UNKNOWN,
            source="api",
            message=f"API request failed: {exc}",
        )

    data = resp.json()

    # Extract dates/sales info
    dates = data.get("dates", {})
    status_obj = dates.get("status", {})
    status_code = status_obj.get("code", "").lower()

    # Extract price ranges if present
    price_range = None
    lowest_price_usd: Optional[float] = None
    price_ranges = data.get("priceRanges", [])
    if price_ranges:
        pr = price_ranges[0]
        currency = pr.get("currency", "USD")
        low = pr.get("min", "?")
        high = pr.get("max", "?")
        price_range = f"{currency} {low} - {high}"
        if isinstance(low, (int, float)):
            lowest_price_usd = float(low)

    # Extract ticket limit
    ticket_limit = None
    limits = data.get("ticketLimit", {})
    if limits:
        ticket_limit = limits.get("info")

    # Map API status to our enum
    status_map = {
        "offsale": TicketStatus.OFF_SALE,
        "cancelled": TicketStatus.CANCELLED,
        "postponed": TicketStatus.OFF_SALE,
        "rescheduled": TicketStatus.OFF_SALE,
    }

    # "onsale" just means the sale window is open, NOT that inventory exists.
    # We treat it as sold-out by default and look for positive signals.
    if status_code in status_map:
        ticket_status = status_map[status_code]
    elif status_code == "onsale":
        # Sale window is open. Check for signs of actual inventory:
        # 1) priceRanges appearing (often absent when sold out)
        # 2) New presale windows opening
        has_prices = bool(price_ranges)

        if has_prices:
            ticket_status = TicketStatus.AVAILABLE
        else:
            # No price data = likely sold out but sale window still open
            ticket_status = TicketStatus.SOLD_OUT
    else:
        ticket_status = TicketStatus.UNKNOWN

    # Check sale window boundaries
    sales = data.get("sales", {})
    public_sales = sales.get("public", {})
    start_str = public_sales.get("startDateTime", "")
    end_str = public_sales.get("endDateTime", "")
    now = datetime.now(timezone.utc)

    if start_str and end_str:
        try:
            start_dt = datetime.fromisoformat(start_str.replace("Z", "+00:00"))
            end_dt = datetime.fromisoformat(end_str.replace("Z", "+00:00"))
            if now < start_dt:
                ticket_status = TicketStatus.OFF_SALE
                return CheckResult(
                    status=ticket_status,
                    source="api",
                    message=f"Public sale hasn't started yet (starts {start_str})",
                    raw_data=data,
                    price_range=price_range,
                )
            if now > end_dt:
                ticket_status = TicketStatus.SOLD_OUT
        except (ValueError, TypeError):
            pass

    message = f"API status: {status_code} | prices: {'yes' if price_range else 'none'}"
    if ticket_status == TicketStatus.AVAILABLE:
        message = f"TICKETS AVAILABLE! Prices appeared: {price_range}"

    return CheckResult(
        status=ticket_status,
        source="api",
        message=message,
        raw_data=data,
        price_range=price_range,
        ticket_limit=ticket_limit,
        lowest_price_usd=lowest_price_usd,
    )


# ---------------------------------------------------------------------------
# Cortex-Scout checker (optional fallback / secondary confirmation)
# ---------------------------------------------------------------------------

def cortex_scout_available() -> bool:
    """Check if cortex-scout HTTP server is running."""
    try:
        resp = requests.get(
            f"{config.CORTEX_SCOUT_URL}/health",
            timeout=3,
        )
        return resp.status_code == 200
    except requests.exceptions.RequestException:
        return False


# Strong "tickets exist" signals: a price with cents, or phrases that only
# appear on the rendered listings/seatmap page when inventory is showing.
_PRICE_RE = re.compile(r"\$\d{1,4}\.\d{2}")
_AVAILABLE_PHRASES = (
    "face value exchange ticket",
    "buy now, pay later",
    "add to cart",
    "select seats",
    "choose seats",
)
# Strong "no inventory" signals from TM's rendered empty state.
_SOLD_OUT_PHRASES = (
    "currently unavailable",
    "no tickets available",
    "this event is sold out",
    "no tickets remain",
)


def _classify_page_text(
    text: str,
) -> tuple[TicketStatus, str, Optional[float]]:
    """Classify the rendered event page body text into a ticket status.

    TM's ticket-listings page text when inventory exists contains both a
    dollar-and-cents price and section/row markers. When truly sold out, the
    page falls back to an empty-state message. Anything else is UNKNOWN.

    Returns (status, message, lowest_price_usd). The price is the minimum of
    all `$NN.NN` occurrences on the page (TM sorts by lowest by default, but
    we take min explicitly so ordering changes don't fool us).
    """
    lowered = text.lower()

    prices = [float(m.lstrip("$")) for m in _PRICE_RE.findall(text)]
    lowest_price = min(prices) if prices else None

    has_seat_marker = bool(re.search(r"\brow\s+\d", lowered)) or bool(
        re.search(r"\bsec(?:tion)?\s+[a-z0-9]", lowered)
    )
    has_phrase_hit = any(p in lowered for p in _AVAILABLE_PHRASES)

    sold_out_hit = next((p for p in _SOLD_OUT_PHRASES if p in lowered), None)

    if (lowest_price is not None and has_seat_marker) or has_phrase_hit:
        price_str = f"${lowest_price:.2f}" if lowest_price is not None else "?"
        return (
            TicketStatus.AVAILABLE,
            f"TICKETS DETECTED on page (lowest price: {price_str})",
            lowest_price,
        )

    if sold_out_hit:
        return (
            TicketStatus.SOLD_OUT,
            f"Page shows empty state: '{sold_out_hit}'",
            None,
        )

    return (
        TicketStatus.UNKNOWN,
        "Page rendered but no clear buy/sold signals",
        None,
    )


def _extract_snapshot_text(mcp_result: dict) -> Optional[str]:
    """Pull the snapshot step's `text` field out of a browser_automate response.

    The MCP response wraps the step list as a JSON string with a trailing
    `Tool timing: ...` annotation, so we use raw_decode to parse only the
    leading JSON array and ignore the suffix.
    """
    try:
        content = mcp_result["result"]["content"]
    except (KeyError, TypeError):
        return None
    decoder = json.JSONDecoder()
    for item in content:
        raw = item.get("text") if isinstance(item, dict) else None
        if not raw:
            continue
        try:
            steps, _ = decoder.raw_decode(raw.lstrip())
        except (ValueError, TypeError):
            continue
        if not isinstance(steps, list):
            continue
        for step in steps:
            if step.get("action") != "snapshot":
                continue
            result = step.get("result") or {}
            snap_text = result.get("text")
            if snap_text:
                return snap_text
    return None


def check_via_cortex_scout() -> CheckResult:
    """Drive a real Chromium via cortex-scout's persistent agent profile.

    The agent profile at ~/.cortex-scout/agent_profile persists Akamai
    cookies across calls. The first navigate to the TM homepage seeds those
    cookies so the event page load passes the bot wall; the snapshot returns
    the rendered listings text, which we classify.
    """
    if not cortex_scout_available():
        return CheckResult(
            status=TicketStatus.UNKNOWN,
            source="cortex-scout",
            message="cortex-scout not running, skipping browser check",
        )

    payload = {
        "jsonrpc": "2.0",
        "id": 1,
        "method": "tools/call",
        "params": {
            "name": "browser_automate",
            "arguments": {
                "steps": [
                    {"action": "navigate", "target": "https://www.ticketmaster.com/"},
                    {"action": "navigate", "target": config.EVENT_URL},
                    {"action": "snapshot", "target": "body"},
                ]
            },
        },
    }

    try:
        resp = requests.post(
            f"{config.CORTEX_SCOUT_URL}/mcp",
            json=payload,
            timeout=90,
        )
        resp.raise_for_status()
        result = resp.json()
    except requests.exceptions.RequestException as exc:
        return CheckResult(
            status=TicketStatus.UNKNOWN,
            source="cortex-scout",
            message=f"cortex-scout browser_automate failed: {exc}",
        )

    snapshot_text = _extract_snapshot_text(result)
    if not snapshot_text:
        return CheckResult(
            status=TicketStatus.UNKNOWN,
            source="cortex-scout",
            message="browser_automate returned no snapshot (challenge or timeout)",
            raw_data=result,
        )

    status, message, lowest_price = _classify_page_text(snapshot_text)
    return CheckResult(
        status=status,
        source="cortex-scout",
        message=message,
        raw_data=result,
        lowest_price_usd=lowest_price,
    )


# ---------------------------------------------------------------------------
# Notifications
# ---------------------------------------------------------------------------

def _wsl_powershell(script: str) -> None:
    """Fire a PowerShell script via WSL interop without blocking.

    Notifications (balloon, beep, open URL) are all fire-and-forget. The
    balloon script in particular does `Start-Sleep -Seconds 15` to keep the
    tray icon alive while the tip is shown, so we must not wait on it.

    No-op on non-WSL hosts (EC2, bare Linux) — powershell.exe won't exist.
    """
    if not _POWERSHELL_EXE:
        return
    try:
        subprocess.Popen(
            [_POWERSHELL_EXE, "-NoProfile", "-Command", script],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            start_new_session=True,
        )
    except OSError as exc:
        log.warning("PowerShell launch failed: %s", exc)


def notify_windows_balloon(title: str, body: str) -> None:
    """Send a Windows balloon notification from WSL via PowerShell."""
    safe_title = title.replace("'", "''")
    safe_body = body.replace("'", "''").replace("\n", " | ")

    ps_script = f"""
Add-Type -AssemblyName System.Windows.Forms
$balloon = New-Object System.Windows.Forms.NotifyIcon
$balloon.Icon = [System.Drawing.SystemIcons]::Warning
$balloon.BalloonTipIcon = 'Warning'
$balloon.BalloonTipTitle = '{safe_title}'
$balloon.BalloonTipText = '{safe_body}'
$balloon.Visible = $true
$balloon.ShowBalloonTip(30000)
Start-Sleep -Seconds 15
$balloon.Dispose()
"""
    _wsl_powershell(ps_script)


def notify_windows_sound() -> None:
    """Play a loud alert sound on Windows from WSL."""
    ps_script = """
[System.Console]::Beep(1000, 500)
[System.Console]::Beep(1500, 500)
[System.Console]::Beep(1000, 500)
[System.Console]::Beep(1500, 500)
[System.Console]::Beep(2000, 800)
"""
    _wsl_powershell(ps_script)


def notify_windows_open_url() -> None:
    """Open the Ticketmaster event page in the default Windows browser."""
    _wsl_powershell(f"Start-Process '{config.EVENT_URL}'")


def notify_telegram(title: str, body: str) -> None:
    """Send a Telegram message to the configured chat."""
    token = get_telegram_bot_token()
    chat_id = get_telegram_chat_id()
    if not token or not chat_id:
        log.warning("[Telegram] Missing token or chat id; skipping")
        return

    url = f"https://api.telegram.org/bot{token}/sendMessage"
    text = f"*{title}*\n\n{body}\n\n[Buy Tickets]({config.EVENT_URL})"

    try:
        resp = requests.post(
            url,
            json={
                "chat_id": chat_id,
                "text": text,
                "parse_mode": "Markdown",
                "disable_web_page_preview": False,
            },
            timeout=10,
        )
        if resp.status_code == 200:
            log.info("[Telegram] Alert sent to chat %s", chat_id)
        else:
            log.warning("[Telegram] Failed (%s): %s", resp.status_code, resp.text)
    except requests.exceptions.RequestException as exc:
        log.warning("[Telegram] Send failed: %s", exc)


def notify(result: CheckResult) -> None:
    """Send notifications via all configured channels."""
    title = "TICKETS FOUND!"
    body = f"{config.EVENT_NAME}\n{result.message}"

    log.info("=" * 60)
    log.info(title)
    log.info(body)
    log.info("=" * 60)

    # Each channel is isolated: a broken notifier must not kill the monitor.
    channels: list[tuple[str, callable]] = []
    if telegram_enabled():
        channels.append(("telegram", lambda: notify_telegram(title, body)))
    if desktop_notifications_enabled():
        channels.append(("balloon", lambda: notify_windows_balloon(title, body)))
    if sound_notifications_enabled():
        channels.append(("sound", notify_windows_sound))

    for name, fn in channels:
        try:
            fn()
        except Exception as exc:  # noqa: BLE001 — defensive: notifiers must not kill the loop
            log.warning("[notify:%s] failed: %s", name, exc)

    # Auto-open the Ticketmaster page in the browser (WSL only)
    if desktop_notifications_enabled():
        notify_windows_open_url()

    # Terminal bell as backup
    print("\a" * 5, end="", flush=True)


# ---------------------------------------------------------------------------
# Main loop
# ---------------------------------------------------------------------------

def run_check(api_key: str) -> CheckResult:
    """Run all available checkers and return the most informative result."""
    results: list[CheckResult] = []

    # Primary: API check
    if api_key:
        api_result = check_via_api(api_key)
        results.append(api_result)
        log.info("[API] %s", api_result.message)
    else:
        log.warning("No API key set -- skipping Discovery API check")

    # Secondary: cortex-scout scrape
    scout_result = check_via_cortex_scout()
    results.append(scout_result)
    if scout_result.status != TicketStatus.UNKNOWN:
        log.info("[Scout] %s", scout_result.message)

    # Prefer AVAILABLE from any source
    for r in results:
        if r.status == TicketStatus.AVAILABLE:
            return r

    # Otherwise return the first non-UNKNOWN result
    for r in results:
        if r.status != TicketStatus.UNKNOWN:
            return r

    # All unknown
    if results:
        return results[0]

    return CheckResult(
        status=TicketStatus.UNKNOWN,
        source="none",
        message="No checkers available (set TICKETMASTER_API_KEY or start cortex-scout)",
    )


def _parse_args(argv: Optional[list[str]] = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description="Ticketmaster ticket availability monitor."
    )
    # CLI takes precedence; falls back to the MAX_PRICE env var so it can be
    # set from SSM / systemd EnvironmentFile without editing ExecStart.
    env_default: Optional[float] = None
    raw = os.environ.get("MAX_PRICE", "").strip()
    if raw:
        try:
            env_default = float(raw)
        except ValueError:
            log.warning("Ignoring invalid MAX_PRICE env var: %r", raw)
    p.add_argument(
        "--max-price",
        type=float,
        default=env_default,
        metavar="USD",
        help=(
            "Only fire alerts when the lowest detected ticket price is at or "
            "below this threshold (in USD). Defaults to the MAX_PRICE env "
            "var. Detections above the threshold are logged but not alerted. "
            "Detections with no price (API path when priceRanges empty, or "
            "phrase-only scout match) are also suppressed when this is set."
        ),
    )
    return p.parse_args(argv)


def main() -> None:
    args = _parse_args()
    max_price = args.max_price
    api_key = get_api_key()

    log.info("=" * 60)
    log.info("Ticketmaster Ticket Monitor")
    log.info("Event: %s", config.EVENT_NAME)
    log.info("URL:   %s", config.EVENT_URL)
    log.info("Check interval: %ds (+%ds jitter)", config.CHECK_INTERVAL_SECS, config.JITTER_SECS)
    log.info("API key: %s", "configured" if api_key else "NOT SET")
    log.info("cortex-scout: %s", "running" if cortex_scout_available() else "not detected")
    log.info(
        "Desktop alerts: %s (PowerShell %s)",
        "on" if desktop_notifications_enabled() else "off",
        "available" if _POWERSHELL_EXE else "not found — assuming non-WSL host",
    )
    log.info("Telegram: %s", "on" if telegram_enabled() else "off")
    if max_price is not None:
        log.info("Price threshold: alerts only when lowest price <= $%.2f", max_price)
    log.info("=" * 60)

    if not api_key and not cortex_scout_available():
        log.error(
            "No data sources available! Set TICKETMASTER_API_KEY env var "
            "or start cortex-scout. Get a free API key at: "
            "https://developer.ticketmaster.com/"
        )
        sys.exit(1)

    # Send startup message to Telegram
    if telegram_enabled():
        sources = []
        if api_key:
            sources.append("Discovery API")
        if cortex_scout_available():
            sources.append("cortex-scout")
        notify_telegram(
            "Ticket Monitor Started",
            f"Watching: {config.EVENT_NAME}\n"
            f"Checking every ~{config.CHECK_INTERVAL_SECS}s\n"
            f"Sources: {', '.join(sources)}",
        )

    check_count = 0
    last_notify_time = 0.0

    try:
        while True:
            check_count += 1
            now_str = datetime.now().strftime("%H:%M:%S")
            log.info("--- Check #%d at %s ---", check_count, now_str)

            result = run_check(api_key)

            if result.status == TicketStatus.AVAILABLE:
                price = result.lowest_price_usd
                over_threshold = (
                    max_price is not None
                    and price is not None
                    and price > max_price
                )
                price_unknown_blocked = max_price is not None and price is None

                if over_threshold:
                    log.info(
                        "AVAILABLE at $%.2f but over threshold $%.2f — not alerting",
                        price,
                        max_price,
                    )
                elif price_unknown_blocked:
                    log.info(
                        "AVAILABLE but no price extracted; threshold $%.2f set — not alerting",
                        max_price,
                    )
                else:
                    now = time.time()
                    if now - last_notify_time >= config.NOTIFY_REPEAT_INTERVAL_SECS:
                        notify(result)
                        last_notify_time = now
                    else:
                        log.info("Tickets still available (suppressing repeat alert)")
            elif result.status == TicketStatus.SOLD_OUT:
                log.info("Still sold out. Waiting...")
            elif result.status == TicketStatus.OFF_SALE:
                log.info("Event is off-sale. Waiting...")
            else:
                log.info("Status unknown. Waiting...")

            sleep_time = config.CHECK_INTERVAL_SECS + random.uniform(0, config.JITTER_SECS)
            log.info("Next check in %.0f seconds", sleep_time)
            time.sleep(sleep_time)

    except KeyboardInterrupt:
        log.info("\nMonitor stopped by user.")
        sys.exit(0)


if __name__ == "__main__":
    main()
