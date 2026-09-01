"""Stream selector, the equivalent of holding the LISTEN key on the physical panel."""

from __future__ import annotations

from homeassistant.components.select import SelectEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.dispatcher import async_dispatcher_connect
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import DOMAIN, SIGNAL_UPDATE


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    feed = hass.data[DOMAIN][entry.entry_id]
    if not feed.streams:
        return
    async_add_entities([StreamSelect(feed)])


class StreamSelect(SelectEntity):
    """Which stream a callout opens, and what a manual listen plays."""

    _attr_has_entity_name = True
    _attr_name = "Stream"
    _attr_unique_id = "cfa_pager_stream"
    _attr_icon = "mdi:radio"
    _attr_should_poll = False

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

    @property
    def options(self) -> list[str]:
        return [s["name"] for s in self._feed.streams]

    @property
    def current_option(self) -> str | None:
        streams = self._feed.streams
        if not streams:
            return None
        return streams[self._feed.stream_index % len(streams)]["name"]

    async def async_select_option(self, option: str) -> None:
        await self._feed.audio.async_select_stream(option)
