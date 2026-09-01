"""Constants for the CFA Pager integration."""

DOMAIN = "cfa_pager"

# Fired for every page that survives matching and deduplication. Automations hook this.
EVENT_CALLOUT = "cfa_pager_callout"

# Fired for every page received, matched or not, for the traffic view and feed liveness.
EVENT_PAGE = "cfa_pager_page"

CONF_BROKER = "broker"
CONF_TLS = "tls"
CONF_TLS_INSECURE = "tls_insecure"
CONF_PORT = "port"
CONF_TOPICS = "topics"
CONF_CLIENT_ID = "client_id"
CONF_CAPCODES = "capcodes"
# What the user typed: station names, alphacodes or raw capcodes. Resolved at
# setup so the UI can keep showing the names rather than numbers.
CONF_BRIGADES = "brigades"
CONF_DEDUPE_SECONDS = "dedupe_seconds"
CONF_HISTORY = "history"
# How many recent pages to keep for display, matched or not. Kept separate from the
# callout history because the volume is three orders of magnitude higher.
CONF_PAGE_HISTORY = "page_history"

DEFAULT_BROKER = "pocsag.info"
DEFAULT_PORT = 1883
DEFAULT_TLS_PORT = 8883
DEFAULT_TLS = False
DEFAULT_TOPICS = ["agency/#"]
DEFAULT_DEDUPE_SECONDS = 90
DEFAULT_HISTORY = 100
DEFAULT_PAGE_HISTORY = 50

# Feed is considered dead if nothing at all arrives for this long. Measured rate is about
# one page every two minutes, and the quietest observed gap is well inside this.
FEED_STALE_SECONDS = 1800

DEFAULT_CLIENT_ID = "ha-cfa-pager"

SERVICE_CLEAR_HISTORY = "clear_history"
ATTR_CALLOUTS = "callouts"
ATTR_PAGES = "pages"

SIGNAL_UPDATE = f"{DOMAIN}_update"

PLATFORMS = ["sensor", "binary_sensor"]
