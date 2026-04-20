"""Configuration for the Ticketmaster ticket monitor."""

# --- Event Details ---
EVENT_ID = "vv1kFZ_778G7HWnD"  # Discovery API ID
EVENT_URL_ID = "0200644110F3DABB"  # URL slug ID
EVENT_URL = (
    "https://www.ticketmaster.com/noah-kahan-the-great-divide-tour-"
    "philadelphia-pennsylvania-06-26-2026/event/0200644110F3DABB"
)
EVENT_NAME = "Noah Kahan - The Great Divide Tour (Philadelphia, 06/26/2026)"

# --- Ticketmaster Discovery API ---
# Get a free API key at: https://developer.ticketmaster.com/
TM_API_KEY = ""

# API endpoint for event details
TM_API_BASE = "https://app.ticketmaster.com/discovery/v2"

# --- Cortex-Scout (optional, for enhanced scraping) ---
CORTEX_SCOUT_URL = "http://127.0.0.1:5000"

# --- Polling ---
# Floor is set by two things:
#   1. Ticketmaster Discovery API quota: 5,000 requests/day = ~17s between
#      calls sustained. Going faster will eventually earn you 429s.
#   2. browser_automate cycle time: ~10-15s per check (two navigations +
#      snapshot). Setting the interval below the cycle time just means the
#      next check starts immediately and you burn scout/chromium CPU.
# 20s + up to 5s jitter = ~48-56 checks/minute of headroom on the API side
# and a steady cadence for the scout.
CHECK_INTERVAL_SECS = 20
JITTER_SECS = 5

# --- Notifications ---
NOTIFY_SOUND = True  # Play a beep on ticket detection
NOTIFY_DESKTOP = True  # Windows balloon notification via WSL
NOTIFY_REPEAT_INTERVAL_SECS = 300  # Re-alert every N seconds while available

# --- Telegram ---
TELEGRAM_ENABLED = True
TELEGRAM_BOT_TOKEN = ""
TELEGRAM_CHAT_ID = ""
