"""CFA Pager: watch a POCSAG feed and raise Home Assistant events for your brigades.

Home Assistant's own MQTT integration is limited to a single broker (its manifest sets
single_config_entry), so this opens its own client to the public pager feed rather than
needing a broker bridge. paho-mqtt is already a Home Assistant dependency, so this adds
none of its own.

paho runs its own network thread. Nothing here touches Home Assistant from that thread
directly: every callback hands off with hass.loop.call_soon_threadsafe, because blocking or
mutating state from a foreign thread is how integrations wedge the event loop.

Configuration is via the UI. A YAML block is still accepted once and imported into a config
entry, so an existing setup is not lost.
"""

from __future__ import annotations

import logging
import time

import paho.mqtt.client as mqtt
import voluptuous as vol

from homeassistant.config_entries import SOURCE_IMPORT, ConfigEntry
from homeassistant.const import (
    CONF_PASSWORD,
    CONF_USERNAME,
    EVENT_HOMEASSISTANT_STOP,
)
from homeassistant.core import HomeAssistant
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers.dispatcher import async_dispatcher_send
from homeassistant.helpers.typing import ConfigType

from . import lookup
from .const import (
    CONF_BRIGADES,
    CONF_BROKER,
    CONF_CAPCODES,
    CONF_CLIENT_ID,
    CONF_DEDUPE_SECONDS,
    CONF_HISTORY,
    CONF_PAGE_HISTORY,
    CONF_PORT,
    CONF_TOPICS,
    DEFAULT_BROKER,
    DEFAULT_CLIENT_ID,
    DEFAULT_DEDUPE_SECONDS,
    DEFAULT_HISTORY,
    DEFAULT_PAGE_HISTORY,
    DEFAULT_PORT,
    DEFAULT_TLS,
    DEFAULT_TOPICS,
    DOMAIN,
    CONF_TLS,
    CONF_TLS_INSECURE,
    EVENT_CALLOUT,
    EVENT_PAGE,
    PLATFORMS,
    SIGNAL_UPDATE,
)
from .matcher import Deduper, dedupe_key, parse_page

_LOGGER = logging.getLogger(__name__)

# Accepted for a one-time import into a config entry. New setups use the UI.
CONFIG_SCHEMA = vol.Schema(
    {
        DOMAIN: vol.Schema(
            {
                vol.Optional(CONF_BROKER, default=DEFAULT_BROKER): cv.string,
                vol.Optional(CONF_PORT, default=DEFAULT_PORT): cv.port,
                vol.Optional(CONF_TOPICS, default=DEFAULT_TOPICS): vol.All(
                    cv.ensure_list, [cv.string]
                ),
                vol.Optional(CONF_CLIENT_ID, default=DEFAULT_CLIENT_ID): cv.string,
                vol.Optional(CONF_USERNAME): cv.string,
                vol.Optional(CONF_PASSWORD): cv.string,
                vol.Optional(CONF_TLS, default=DEFAULT_TLS): cv.boolean,
                vol.Optional(CONF_TLS_INSECURE, default=False): cv.boolean,
                vol.Optional(CONF_CAPCODES): vol.All(cv.ensure_list, [cv.string]),
                vol.Optional(CONF_BRIGADES): vol.All(cv.ensure_list, [cv.string]),
                vol.Optional(
                    CONF_DEDUPE_SECONDS, default=DEFAULT_DEDUPE_SECONDS
                ): vol.Coerce(float),
                vol.Optional(CONF_HISTORY, default=DEFAULT_HISTORY): cv.positive_int,
                vol.Optional(
                    CONF_PAGE_HISTORY, default=DEFAULT_PAGE_HISTORY
                ): cv.positive_int,
            }
        )
    },
    extra=vol.ALLOW_EXTRA,
)


class PagerFeed:
    """Owns the MQTT connection and the derived state the entities read."""

    def __init__(self, hass: HomeAssistant, entry: ConfigEntry) -> None:
        self.hass = hass
        self.entry = entry
        settings = {**entry.data, **entry.options}

        self.broker = settings.get(CONF_BROKER, DEFAULT_BROKER)
        self.port = settings.get(CONF_PORT, DEFAULT_PORT)
        self.topics = settings.get(CONF_TOPICS, DEFAULT_TOPICS)
        # Must differ from any other client on the same broker. Two connections sharing an
        # id kick each other off in a flap loop and both go silent.
        self.client_id = settings.get(CONF_CLIENT_ID) or DEFAULT_CLIENT_ID
        self.username = settings.get(CONF_USERNAME) or ""
        self.password = settings.get(CONF_PASSWORD) or ""
        self.use_tls = bool(settings.get(CONF_TLS, DEFAULT_TLS))
        self.tls_insecure = bool(settings.get(CONF_TLS_INSECURE, False))
        self.brigades = settings.get(CONF_BRIGADES, [])
        self.labels = lookup.resolve_many(self.brigades)[0]
        self.capcodes = set(self.labels)
        self.history_limit = settings.get(CONF_HISTORY, DEFAULT_HISTORY)
        self.page_limit = settings.get(CONF_PAGE_HISTORY, DEFAULT_PAGE_HISTORY)
        self.deduper = Deduper(settings.get(CONF_DEDUPE_SECONDS, DEFAULT_DEDUPE_SECONDS))

        self.connected = False
        self.pages_seen = 0
        self.suppressed = 0
        self.last_page_at: float = 0.0
        self.last_callout: dict | None = None
        self.callouts: list[dict] = []
        self.pages: list[dict] = []
        self._client: mqtt.Client | None = None

    # -- lifecycle -------------------------------------------------------------------

    def start(self) -> None:
        client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2, client_id=self.client_id)
        client.reconnect_delay_set(min_delay=1, max_delay=60)
        client.on_connect = self._on_connect
        client.on_disconnect = self._on_disconnect
        client.on_message = self._on_message
        if self.username:
            client.username_pw_set(self.username, self.password or None)
        if self.use_tls:
            client.tls_set()
            if self.tls_insecure:
                client.tls_insecure_set(True)
        self._client = client
        _LOGGER.info(
            "Connecting to %s:%s as %s (tls=%s, auth=%s), watching %s brigades",
            self.broker, self.port, self.client_id, self.use_tls,
            bool(self.username), len(self.capcodes),
        )
        client.connect_async(self.broker, self.port, keepalive=60)
        client.loop_start()

    def stop(self) -> None:
        if self._client is None:
            return
        self._client.disconnect()
        self._client.loop_stop()
        self._client = None
        self.connected = False

    # -- paho callbacks, all on paho's thread ----------------------------------------

    def _on_connect(self, client, userdata, flags, reason_code, properties=None):
        if reason_code != 0:
            _LOGGER.error("Pager feed refused the connection: %s", reason_code)
            return
        self.connected = True
        for topic in self.topics:
            client.subscribe(topic, qos=0)
            _LOGGER.info("Subscribed to %s", topic)
        self._notify()

    def _on_disconnect(self, client, userdata, *args):
        self.connected = False
        _LOGGER.warning("Pager feed disconnected, paho will retry")
        self._notify()

    def _on_message(self, client, userdata, message):
        try:
            payload = message.payload.decode("utf-8", errors="replace")
            page = parse_page(payload)
        except Exception:  # a malformed page must never kill the network thread
            _LOGGER.exception("Could not handle a message on %s", message.topic)
            return
        if page is None:
            return

        now = time.time()
        page["topic"] = message.topic
        page["ts"] = now
        self.pages_seen += 1
        self.last_page_at = now

        # Trimmed copy for display. The full page goes out as an event for automations;
        # this list is only what a dashboard needs, so the attribute stays small.
        if self.page_limit:
            self.pages.insert(0, {
                "ts": now,
                "capcode": page["capcode"],
                "alphacode": page["alphacode"],
                "description": page["description"],
                "agency": page["agency"],
                "text": page["text"][:160],
            })
            del self.pages[self.page_limit :]

        matched = page["capcode"] in self.capcodes
        if matched and self.deduper.is_duplicate(dedupe_key(page), now):
            self.suppressed += 1
            _LOGGER.debug("Duplicate page for %s within the window", page["capcode"])
            matched = False
        if matched:
            page["brigade"] = self.labels.get(page["capcode"]) or page["description"]
            self.last_callout = page
            self.callouts.insert(0, page)
            del self.callouts[self.history_limit :]
            _LOGGER.info(
                "CALLOUT %s (%s): %s", page["capcode"], page["brigade"], page["text"]
            )

        # Hand off to the event loop; never fire events from paho's thread.
        self.hass.loop.call_soon_threadsafe(self._publish, page, matched)

    # -- event loop side -------------------------------------------------------------

    def _publish(self, page: dict, matched: bool) -> None:
        self.hass.bus.async_fire(EVENT_PAGE, page)
        if matched:
            self.hass.bus.async_fire(EVENT_CALLOUT, page)
        async_dispatcher_send(self.hass, SIGNAL_UPDATE)

    def _notify(self) -> None:
        self.hass.loop.call_soon_threadsafe(
            async_dispatcher_send, self.hass, SIGNAL_UPDATE
        )


async def async_setup(hass: HomeAssistant, config: ConfigType) -> bool:
    """Import a YAML configuration into a config entry, once."""
    conf = config.get(DOMAIN)
    if conf is None:
        return True
    if not conf.get(CONF_BRIGADES) and conf.get(CONF_CAPCODES):
        conf = {**conf, CONF_BRIGADES: conf[CONF_CAPCODES]}
    _LOGGER.info("Importing the YAML configuration into a config entry")
    hass.async_create_task(
        hass.config_entries.flow.async_init(
            DOMAIN, context={"source": SOURCE_IMPORT}, data=conf
        )
    )
    return True


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    feed = PagerFeed(hass, entry)
    hass.data.setdefault(DOMAIN, {})[entry.entry_id] = feed

    await hass.async_add_executor_job(feed.start)
    entry.async_on_unload(
        hass.bus.async_listen_once(EVENT_HOMEASSISTANT_STOP, lambda _e: feed.stop())
    )
    # Saving options reloads the entry, so brigade changes apply without a restart.
    entry.async_on_unload(entry.add_update_listener(_async_reload_entry))
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    unloaded = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    feed = hass.data[DOMAIN].pop(entry.entry_id, None)
    if feed is not None:
        await hass.async_add_executor_job(feed.stop)
    return unloaded


async def _async_reload_entry(hass: HomeAssistant, entry: ConfigEntry) -> None:
    await hass.config_entries.async_reload(entry.entry_id)
