"""Connectivity and liveness for the CFA Pager feed.

Two different failures need telling apart: the socket being down, and the socket being up
while nothing arrives. The second is the one that would otherwise look like a quiet night.
"""

from __future__ import annotations

from datetime import timedelta
import time

from homeassistant.components.binary_sensor import (
    BinarySensorDeviceClass,
    BinarySensorEntity,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.dispatcher import async_dispatcher_connect
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.event import async_track_time_interval

from .const import DOMAIN, FEED_STALE_SECONDS, SIGNAL_UPDATE


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    feed = hass.data[DOMAIN][entry.entry_id]
    async_add_entities([FeedConnected(feed), FeedAlive(feed)])


class FeedBinary(BinarySensorEntity):
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


class FeedConnected(FeedBinary):
    """Whether the MQTT socket to the pager feed is up."""

    _attr_name = "Feed connected"
    _attr_unique_id = "cfa_pager_feed_connected"
    _attr_device_class = BinarySensorDeviceClass.CONNECTIVITY

    @property
    def is_on(self) -> bool:
        return self._feed.connected


class FeedAlive(FeedBinary):
    """Whether pages are actually arriving.

    Goes to problem after FEED_STALE_SECONDS of complete silence. Needs its own timer:
    with nothing arriving there is no message to trigger a recalculation.
    """

    _attr_name = "Feed stale"
    _attr_unique_id = "cfa_pager_feed_stale"
    _attr_device_class = BinarySensorDeviceClass.PROBLEM

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        self.async_on_remove(
            async_track_time_interval(
                self.hass, self._tick, timedelta(seconds=60)
            )
        )

    @callback
    def _tick(self, _now) -> None:
        self.async_write_ha_state()

    @property
    def is_on(self) -> bool:
        if not self._feed.last_page_at:
            return False  # nothing yet is not the same as gone quiet
        return (time.time() - self._feed.last_page_at) > FEED_STALE_SECONDS

    @property
    def extra_state_attributes(self) -> dict:
        age = time.time() - self._feed.last_page_at if self._feed.last_page_at else None
        return {
            "seconds_since_last_page": int(age) if age is not None else None,
            "stale_after_seconds": FEED_STALE_SECONDS,
        }
