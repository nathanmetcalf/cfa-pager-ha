"""Audio orchestration: sound the alert, open the stream, close it when it goes quiet.

Deliberately built on plain media_player services rather than anything specific to one
player integration. media_player.play_media takes an `announce` flag, and any player that
advertises the ANNOUNCE feature will duck or pause what it is playing, play the
announcement, then resume. Music Assistant does; so do several others. That keeps this
portable instead of hard-wiring one backend.

The idle window works the way the pager does: a callout opens the stream and sets a
deadline, every further callout pushes the deadline out, and when it passes the stream
stops. A stream started by hand has no deadline and plays until stopped.
"""

from __future__ import annotations

import logging

from homeassistant.components.media_player import (
    ATTR_MEDIA_ANNOUNCE,
    ATTR_MEDIA_CONTENT_ID,
    ATTR_MEDIA_CONTENT_TYPE,
    DOMAIN as MEDIA_PLAYER_DOMAIN,
    SERVICE_PLAY_MEDIA,
    MediaType,
)
from homeassistant.const import ATTR_ENTITY_ID, SERVICE_MEDIA_STOP
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.dispatcher import async_dispatcher_send
from homeassistant.helpers.event import async_call_later
from homeassistant.helpers.network import NoURLAvailableError, get_url

from .const import SIGNAL_UPDATE

_LOGGER = logging.getLogger(__name__)

# Path the alert tone is served from. Static paths are served without authentication,
# which is required: a speaker fetching the file cannot present a Home Assistant token.
ALERT_URL_PATH = "/cfa_pager/alert.mp3"

# Roughly the tone's length, so the stream is not started over the top of it.
ALERT_LENGTH_SECONDS = 2.0


class AudioController:
    """Drives one media player: alert tone, stream selection, and the idle window."""

    def __init__(self, hass: HomeAssistant, feed) -> None:
        self.hass = hass
        self.feed = feed
        self.playing = False
        self.manual = False
        self.deadline: float | None = None
        self._cancel_timer = None

    # -- configuration read live, so an options change needs no restart --------------

    @property
    def settings(self) -> dict:
        return {**self.feed.entry.data, **self.feed.entry.options}

    @property
    def player(self) -> str:
        return self.settings.get("media_player") or ""

    @property
    def streams(self) -> list[dict]:
        return self.feed.streams

    @property
    def current_stream(self) -> dict | None:
        streams = self.streams
        if not streams:
            return None
        return streams[self.feed.stream_index % len(streams)]

    @property
    def play_seconds(self) -> int:
        return int(self.settings.get("play_seconds", 900))

    def stream_url(self, stream: dict) -> str:
        """The stream URL with credentials injected, for feeds behind HTTP Basic auth."""
        url = stream.get("url", "")
        user = self.settings.get("stream_username") or ""
        password = self.settings.get("stream_password") or ""
        if user and "://" in url and "@" not in url.split("://", 1)[1].split("/", 1)[0]:
            scheme, rest = url.split("://", 1)
            return f"{scheme}://{user}:{password}@{rest}"
        return url

    def alert_url(self) -> str | None:
        """Absolute URL for the tone. A speaker needs one it can fetch itself."""
        try:
            base = get_url(self.hass, prefer_external=False, allow_internal=True)
        except NoURLAvailableError:
            _LOGGER.warning("No Home Assistant URL available, cannot play the alert tone")
            return None
        return f"{base.rstrip('/')}{ALERT_URL_PATH}"

    # -- actions ---------------------------------------------------------------------

    async def async_alert(self) -> None:
        """Sound the tone as an announcement, so a running stream is ducked not stopped."""
        if not self.player or not self.settings.get("alert_enabled", True):
            return
        url = self.alert_url()
        if not url:
            return
        _LOGGER.info("Sounding the alert tone on %s", self.player)
        await self.hass.services.async_call(
            MEDIA_PLAYER_DOMAIN,
            SERVICE_PLAY_MEDIA,
            {
                ATTR_ENTITY_ID: self.player,
                ATTR_MEDIA_CONTENT_ID: url,
                ATTR_MEDIA_CONTENT_TYPE: MediaType.MUSIC,
                ATTR_MEDIA_ANNOUNCE: True,
            },
            blocking=True,
        )

    async def async_play_stream(self, manual: bool = False) -> None:
        """Open the selected stream. A manual start has no idle deadline."""
        stream = self.current_stream
        if not self.player or not stream or not stream.get("url"):
            _LOGGER.debug("No player or stream configured, not starting playback")
            return
        _LOGGER.info("Playing %s on %s", stream["name"], self.player)
        await self.hass.services.async_call(
            MEDIA_PLAYER_DOMAIN,
            SERVICE_PLAY_MEDIA,
            {
                ATTR_ENTITY_ID: self.player,
                ATTR_MEDIA_CONTENT_ID: self.stream_url(stream),
                ATTR_MEDIA_CONTENT_TYPE: MediaType.MUSIC,
            },
            blocking=False,
        )
        self.playing = True
        self.manual = manual
        if manual:
            self._clear_timer()
            self.deadline = None
        self._notify()

    async def async_stop(self) -> None:
        if not self.player:
            return
        _LOGGER.info("Stopping playback on %s", self.player)
        self._clear_timer()
        self.deadline = None
        self.playing = False
        self.manual = False
        await self.hass.services.async_call(
            MEDIA_PLAYER_DOMAIN,
            SERVICE_MEDIA_STOP,
            {ATTR_ENTITY_ID: self.player},
            blocking=False,
        )
        self._notify()

    async def async_on_callout(self) -> None:
        """A page matched: tone first, then the stream, then extend the window."""
        if not self.settings.get("audio_enabled", True):
            _LOGGER.debug("Audio disabled in options, ignoring the callout")
            return
        await self.async_alert()
        # Give the tone room rather than talking over it.
        async_call_later(self.hass, ALERT_LENGTH_SECONDS, self._after_alert)

    @callback
    def _after_alert(self, _now) -> None:
        self.hass.async_create_task(self._start_and_extend())

    async def _start_and_extend(self) -> None:
        if not self.playing:
            await self.async_play_stream(manual=False)
        else:
            self.manual = False
        self._extend()

    async def async_next_stream(self) -> None:
        """Move to the next stream, restarting only if something was already playing."""
        if len(self.streams) > 1:
            self.feed.stream_index = (self.feed.stream_index + 1) % len(self.streams)
        if self.playing:
            await self.async_play_stream(manual=self.manual)
        self._notify()

    async def async_select_stream(self, name: str) -> None:
        for index, stream in enumerate(self.streams):
            if stream["name"] == name:
                self.feed.stream_index = index
                break
        if self.playing:
            await self.async_play_stream(manual=self.manual)
        self._notify()

    # -- idle window -----------------------------------------------------------------

    def _extend(self) -> None:
        """Push the deadline out. Each callout buys another full window."""
        self._clear_timer()
        seconds = self.play_seconds
        self.deadline = self.hass.loop.time() + seconds
        self._cancel_timer = async_call_later(self.hass, seconds, self._expired)
        _LOGGER.debug("Idle window set to %ss", seconds)
        self._notify()

    @callback
    def _expired(self, _now) -> None:
        self._cancel_timer = None
        if self.manual:
            return
        _LOGGER.info("No callout for %ss, stopping the stream", self.play_seconds)
        self.hass.async_create_task(self.async_stop())

    def _clear_timer(self) -> None:
        if self._cancel_timer is not None:
            self._cancel_timer()
            self._cancel_timer = None

    @property
    def remaining(self) -> int:
        if not self.deadline or self.manual:
            return 0
        return max(0, int(self.deadline - self.hass.loop.time()))

    def _notify(self) -> None:
        async_dispatcher_send(self.hass, SIGNAL_UPDATE)
