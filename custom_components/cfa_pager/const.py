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
CONF_INCIDENT_URL = "incident_url"
CONF_INCIDENT_RADIUS = "incident_radius"
CONF_INCIDENT_INTERVAL = "incident_interval"
CONF_INCIDENT_MAX = "incident_max"
CONF_RADAR_PRODUCT = "radar_product"
CONF_RADAR_FRAMES = "radar_frames"
CONF_RADAR_INTERVAL = "radar_interval"

DEFAULT_BROKER = "pocsag.info"
DEFAULT_PORT = 1883
DEFAULT_TLS_PORT = 8883
DEFAULT_TLS = False
DEFAULT_TOPICS = ["agency/#"]
DEFAULT_DEDUPE_SECONDS = 90
DEFAULT_HISTORY = 100
DEFAULT_PAGE_HISTORY = 50

# Rain radar. Products are <IDR><station><range>, where range 1 is 512 km, 2 is 256 km,
# 3 is 128 km and 4 is 64 km. IDR952 is Rainbow at 256 km.
# Nearby incidents. The default feed is VicEmergency; any GeoJSON feed of point features
# with similar properties works, which is why the URL is configurable.
DEFAULT_INCIDENT_URL = "https://emergency.vic.gov.au/public/osom-geojson.json"
DEFAULT_INCIDENT_RADIUS = 100
DEFAULT_INCIDENT_INTERVAL = 120
DEFAULT_INCIDENT_MAX = 40
DEFAULT_USER_AGENT = "cfa-pager-ha (Home Assistant integration)"

DEFAULT_RADAR_PRODUCT = "IDR952"
DEFAULT_RADAR_FRAMES = 6
DEFAULT_RADAR_INTERVAL = 300
DEFAULT_RADAR_FTP_HOST = "ftp.bom.gov.au"

# Feed is considered dead if nothing at all arrives for this long. Measured rate is about
# one page every two minutes, and the quietest observed gap is well inside this.
FEED_STALE_SECONDS = 1800

DEFAULT_CLIENT_ID = "ha-cfa-pager"

SERVICE_CLEAR_HISTORY = "clear_history"
ATTR_CALLOUTS = "callouts"
ATTR_PAGES = "pages"

SIGNAL_UPDATE = f"{DOMAIN}_update"

PLATFORMS = ["sensor", "binary_sensor", "image"]
