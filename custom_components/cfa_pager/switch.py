"""Listen switch: open or close the scanner stream by hand.

On is a manual listen, which has no idle window and plays until switched off, matching the
behaviour of the physical panel. A callout converts it into a timed session.
"""

from __future__ import annotations

from homeassistant.components.switch import SwitchEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.dispatcher import async_dispatcher_connect
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import CONF_MEDIA_PLAYER, DOMAIN, SIGNAL_UPDATE


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    feed = hass.data[DOMAIN][entry.entry_id]
    settings = {**entry.data, **entry.options}
    if not settings.get(CONF_MEDIA_PLAYER):
        return
    async_add_entities([ListenSwitch(feed)])


class ListenSwitch(SwitchEntity):
    _attr_has_entity_name = True
    _attr_name = "Listen"
    _attr_unique_id = "cfa_pager_listen"
    _attr_icon = "mdi:play-circle-outline"
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
    def is_on(self) -> bool:
        return self._feed.audio.playing

    @property
    def extra_state_attributes(self) -> dict:
        audio = self._feed.audio
        stream = audio.current_stream or {}
        return {
            "stream": stream.get("name"),
            "manual": audio.manual,
            "stops_in_seconds": audio.remaining,
            "media_player": audio.player or None,
        }

    async def async_turn_on(self, **kwargs) -> None:
        await self._feed.audio.async_play_stream(manual=True)

    async def async_turn_off(self, **kwargs) -> None:
        await self._feed.audio.async_stop()
