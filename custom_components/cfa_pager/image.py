"""Animated rain radar as an image entity.

Built here rather than fetched from elsewhere, so the integration is self-contained: a
single scan image from the Bureau is one frame, and the animation is composited from the
last few scans plus the static background, topography, locations and range layers.

An image entity is the right home for this. A camera would re-serve it as a still, and a
plain HTTP view would need its own authentication. This way the frontend gets the bytes
through /api/image_proxy with Home Assistant's own auth, and any picture card can show it.
"""

from __future__ import annotations

from datetime import timedelta
import logging

from homeassistant.components.image import ImageEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.event import async_track_time_interval
from homeassistant.util import dt as dt_util

from .const import (
    CONF_RADAR_FRAMES,
    CONF_RADAR_INTERVAL,
    CONF_RADAR_PRODUCT,
    DEFAULT_RADAR_FRAMES,
    DEFAULT_RADAR_FTP_HOST,
    DEFAULT_RADAR_INTERVAL,
    DEFAULT_RADAR_PRODUCT,
    DOMAIN,
)
from .radar import RadarBuilder

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    feed = hass.data[DOMAIN][entry.entry_id]
    settings = {**entry.data, **entry.options}
    product = settings.get(CONF_RADAR_PRODUCT, DEFAULT_RADAR_PRODUCT)
    if not product:
        _LOGGER.info("Radar disabled, no product configured")
        return
    async_add_entities([RadarImage(hass, feed, settings)])


class RadarImage(ImageEntity):
    """The last few radar scans, composited into an animated GIF."""

    _attr_has_entity_name = True
    _attr_name = "Rain radar"
    _attr_unique_id = "cfa_pager_radar"
    _attr_content_type = "image/gif"
    _attr_should_poll = False

    def __init__(self, hass: HomeAssistant, feed, settings: dict) -> None:
        super().__init__(hass)
        self._feed = feed
        self._product = settings.get(CONF_RADAR_PRODUCT, DEFAULT_RADAR_PRODUCT)
        self._interval = settings.get(CONF_RADAR_INTERVAL, DEFAULT_RADAR_INTERVAL)
        self._builder = RadarBuilder(
            product=self._product,
            host=DEFAULT_RADAR_FTP_HOST,
            frames=settings.get(CONF_RADAR_FRAMES, DEFAULT_RADAR_FRAMES),
        )
        self._bytes: bytes | None = None
        self._frames = 0
        self._newest = ""
        self._error: str | None = None
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, feed.entry.entry_id)},
            name="CFA Pager",
            manufacturer="pocsag.info",
            model=f"{feed.broker}:{feed.port}",
        )

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
        """Rebuild the loop. A failure keeps the previous image rather than blanking it."""
        try:
            image, frames, newest = await self.hass.async_add_executor_job(
                self._builder.build
            )
        except Exception as err:  # ftplib, Pillow and the network all raise their own
            self._error = f"{type(err).__name__}: {err}"
            _LOGGER.warning("Could not build the %s radar loop: %s", self._product, err)
            self.async_write_ha_state()
            return
        self._bytes = image
        self._frames = frames
        self._newest = newest
        self._error = None
        self._attr_image_last_updated = dt_util.utcnow()
        _LOGGER.debug(
            "Radar %s rebuilt: %s frames, newest %s, %s bytes",
            self._product, frames, newest, len(image),
        )
        self.async_write_ha_state()

    async def async_image(self) -> bytes | None:
        return self._bytes

    @property
    def available(self) -> bool:
        return self._bytes is not None

    @property
    def extra_state_attributes(self) -> dict:
        return {
            "product": self._product,
            "frames": self._frames,
            "newest_scan_utc": self._newest,
            "size_bytes": len(self._bytes) if self._bytes else 0,
            "interval_seconds": self._interval,
            "attribution": "Bureau of Meteorology",
            "error": self._error,
        }
