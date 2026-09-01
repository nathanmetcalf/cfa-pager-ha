"""Sensors for the CFA Pager feed."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
import logging

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorStateClass,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.dispatcher import async_dispatcher_connect
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.event import async_track_time_interval
from homeassistant.helpers.restore_state import RestoreEntity
from homeassistant.util import dt as dt_util

from . import incidents as incidents_module
from .const import (
    CONF_INCIDENT_INTERVAL,
    CONF_INCIDENT_MAX,
    CONF_INCIDENT_RADIUS,
    CONF_INCIDENT_URL,
    DEFAULT_INCIDENT_INTERVAL,
    DEFAULT_INCIDENT_MAX,
    DEFAULT_INCIDENT_RADIUS,
    DEFAULT_INCIDENT_URL,
    DEFAULT_USER_AGENT,
    DOMAIN,
    SIGNAL_UPDATE,
)

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    feed = hass.data[DOMAIN][entry.entry_id]
    settings = {**entry.data, **entry.options}
    entities = [LastCalloutSensor(feed), CalloutsTodaySensor(feed), PagesSeenSensor(feed)]
    if feed.page_limit:
        entities.append(RecentPagesSensor(feed))
    if settings.get(CONF_INCIDENT_URL, DEFAULT_INCIDENT_URL):
        entities.append(IncidentsSensor(feed, settings))
    async_add_entities(entities)


class PagerEntity(SensorEntity):
    """Shared plumbing: one device, and redraw when the feed says something changed."""

    _attr_should_poll = False
    _attr_has_entity_name = True

    def __init__(self, feed) -> None:
        self._feed = feed
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, feed.entry.entry_id)},
            name="CFA Pager",
            manufacturer="pocsag.info",
            model=f"{feed.broker}:{feed.port}",
        )

    async def async_added_to_hass(self) -> None:
        self.async_on_remove(
            async_dispatcher_connect(self.hass, SIGNAL_UPDATE, self._updated)
        )

    @callback
    def _updated(self) -> None:
        self.async_write_ha_state()


class LastCalloutSensor(PagerEntity, RestoreEntity):
    """When your brigades were last paged, with the page and recent history attached.

    The state is a timestamp because entity states are capped at 255 characters, so page
    text belongs in an attribute. Restoring on startup repopulates the rolling list, which
    is what makes the callout history survive a Home Assistant restart with no database.
    """

    _attr_name = "Last callout"
    _attr_unique_id = "cfa_pager_last_callout"
    _attr_icon = "mdi:fire-truck"
    _attr_device_class = SensorDeviceClass.TIMESTAMP

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        last = await self.async_get_last_state()
        if last is None or not self._feed.callouts:
            restored = (last.attributes.get("callouts") if last else None) or []
            if restored:
                self._feed.callouts = list(restored)
                self._feed.last_callout = restored[0]
                self._feed.last_page_at = restored[0].get("ts", 0.0)
                _LOGGER.info("Restored %s callouts from the previous run", len(restored))

    @property
    def native_value(self) -> datetime | None:
        callout = self._feed.last_callout
        if not callout:
            return None
        return datetime.fromtimestamp(callout["ts"], tz=timezone.utc)

    @property
    def extra_state_attributes(self) -> dict:
        callout = self._feed.last_callout or {}
        return {
            "capcode": callout.get("capcode"),
            "alphacode": callout.get("alphacode"),
            "description": callout.get("description"),
            "agency": callout.get("agency"),
            "text": callout.get("text"),
            "topic": callout.get("topic"),
            "brigade": callout.get("brigade"),
            # A copy, not the feed's own list: Home Assistant skips the state write
            # when the new attributes compare equal to the stored ones, and the same
            # list object always does.
            "callouts": list(self._feed.callouts),
            "watching": [
                f"{label} ({code})"
                for code, label in sorted(self._feed.labels.items(), key=lambda kv: int(kv[0]))
            ],
        }


class CalloutsTodaySensor(PagerEntity):
    """Callouts since local midnight, counted from the rolling list.

    At roughly 1.5 callouts a day a 100-entry list covers about two months, so this needs
    no database and no SQL.
    """

    _attr_name = "Callouts today"
    _attr_unique_id = "cfa_pager_callouts_today"
    _attr_icon = "mdi:counter"
    _attr_state_class = SensorStateClass.MEASUREMENT

    @property
    def native_value(self) -> int:
        midnight = dt_util.start_of_local_day().timestamp()
        return sum(1 for c in self._feed.callouts if c.get("ts", 0) >= midnight)

    @property
    def extra_state_attributes(self) -> dict:
        now = dt_util.utcnow().timestamp()
        return {
            "last_7_days": sum(
                1 for c in self._feed.callouts if c.get("ts", 0) >= now - 7 * 86400
            ),
            "last_30_days": sum(
                1 for c in self._feed.callouts if c.get("ts", 0) >= now - 30 * 86400
            ),
            "in_history": len(self._feed.callouts),
        }


class PagesSeenSensor(PagerEntity):
    """Every page on the feed, matched or not. Diagnostic, and proves ingestion is live."""

    _attr_name = "Pages seen"
    _attr_unique_id = "cfa_pager_pages_seen"
    _attr_icon = "mdi:radio-tower"
    _attr_state_class = SensorStateClass.TOTAL_INCREASING
    _attr_entity_registry_enabled_default = True

    @property
    def native_value(self) -> int:
        return self._feed.pages_seen

    @property
    def extra_state_attributes(self) -> dict:
        return {
            "suppressed_duplicates": self._feed.suppressed,
            "last_page_at": self._feed.last_page_at or None,
            "broker": f"{self._feed.broker}:{self._feed.port}",
            "client_id": self._feed.client_id,
        }


class RecentPagesSensor(PagerEntity):
    """The last N pages on the feed, matched or not, for a traffic view on a dashboard.

    The list lives in an attribute because a state is capped at 255 characters. This
    entity changes on every page, roughly 670 times a day, so exclude it from the recorder
    and from InfluxDB: nothing downstream wants that history, and both would store a fresh
    copy of the whole list each time.
    """

    _attr_name = "Recent pages"
    _attr_unique_id = "cfa_pager_pages_list"
    _attr_icon = "mdi:format-list-bulleted"

    @property
    def native_value(self) -> int:
        return len(self._feed.pages)

    @property
    def extra_state_attributes(self) -> dict:
        # list(), for the reason given on the callouts attribute above. This entity is
        # where it bites: once the list reaches its cap the length stops changing too,
        # so state and attributes both look unchanged and the list freezes on screen.
        return {"limit": self._feed.page_limit, "pages": list(self._feed.pages)}


class IncidentsSensor(PagerEntity):
    """Going incidents within a radius of home, nearest first.

    Polls on its own timer rather than off the pager feed: the two are unrelated, and a
    quiet pager night is exactly when you still want to see what is going on nearby.

    This entity changes on every poll and carries the whole list in its attributes, so
    exclude it from the recorder unless you want that history.
    """

    _attr_name = "Nearby incidents"
    _attr_unique_id = "cfa_pager_incidents"
    _attr_icon = "mdi:map-marker-alert"

    def __init__(self, feed, settings: dict) -> None:
        super().__init__(feed)
        self._url = settings.get(CONF_INCIDENT_URL, DEFAULT_INCIDENT_URL)
        self._radius = settings.get(CONF_INCIDENT_RADIUS, DEFAULT_INCIDENT_RADIUS)
        self._interval = settings.get(CONF_INCIDENT_INTERVAL, DEFAULT_INCIDENT_INTERVAL)
        self._max = settings.get(CONF_INCIDENT_MAX, DEFAULT_INCIDENT_MAX)
        self._incidents: list[dict] = []
        self._error: str | None = None
        self._updated: datetime | None = None

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        await self._refresh()
        self.async_on_remove(
            async_track_time_interval(
                self.hass, self._scheduled, timedelta(seconds=self._interval)
            )
        )

    @callback
    def _scheduled(self, _now) -> None:
        self.hass.async_create_task(self._refresh())

    async def _refresh(self) -> None:
        """Fetch and filter. A failure keeps the previous list rather than emptying it."""
        try:
            rows = await self.hass.async_add_executor_job(
                incidents_module.nearby,
                self._url,
                DEFAULT_USER_AGENT,
                self.hass.config.latitude,
                self.hass.config.longitude,
                self._radius,
                self._max,
            )
        except Exception as err:  # network, gzip and JSON all raise their own
            self._error = f"{type(err).__name__}: {err}"
            _LOGGER.warning("Could not fetch incidents: %s", err)
            self.async_write_ha_state()
            return
        self._incidents = rows
        self._error = None
        self._updated = dt_util.utcnow()
        self.async_write_ha_state()

    @property
    def native_value(self) -> int:
        return len(self._incidents)

    @property
    def extra_state_attributes(self) -> dict:
        return {
            "incidents": list(self._incidents),
            "radius_km": self._radius,
            "nearest_km": self._incidents[0]["km"] if self._incidents else None,
            "home": [self.hass.config.latitude, self.hass.config.longitude],
            "last_updated": self._updated.isoformat() if self._updated else None,
            "attribution": "VicEmergency",
            "error": self._error,
        }
