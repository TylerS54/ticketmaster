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
API_ENABLED = True  # Set False (or env API_ENABLED=0) to run scout-only

# API endpoint for event details
TM_API_BASE = "https://app.ticketmaster.com/discovery/v2"

# --- Cortex-Scout (optional, for enhanced scraping) ---
CORTEX_SCOUT_URL = "http://127.0.0.1:5000"

# --- Polling ---
# The two data sources poll on independent schedules:
#   - API: Discovery API quota is 5000 req/day = ~17s sustained. 30s stays
#     well under (~2880/day) with headroom for retries.
#   - Scout: browser_automate cycle is ~10-15s (two navigations + snapshot),
#     so 10s is the effective floor. Scout is what actually catches low-
#     inventory drops, so we run it 3x more often than the API.
# The main loop sleeps until whichever source is next due (+ small jitter).
API_CHECK_INTERVAL_SECS = 30
SCOUT_CHECK_INTERVAL_SECS = 10
JITTER_SECS = 2

# --- Notifications ---
NOTIFY_SOUND = True  # Play a beep on ticket detection
NOTIFY_DESKTOP = True  # Windows balloon notification via WSL
NOTIFY_REPEAT_INTERVAL_SECS = 300  # Re-alert every N seconds while available

# --- Telegram ---
TELEGRAM_ENABLED = True
TELEGRAM_BOT_TOKEN = ""
TELEGRAM_CHAT_ID = ""
