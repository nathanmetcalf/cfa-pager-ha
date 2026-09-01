#!/usr/bin/env python3
"""Home Assistant WebSocket client for the Stream Deck panel.

Holds the current state of the six entities the deck cares about, and turns key presses
into service calls. The deck renders from a snapshot dict, so it never blocks on the
network.

Threading: this class owns one thread running an asyncio loop. Everything the panel calls
(status, toggle_listen, change_volume, next_district) is called from other threads and
only touches a lock-protected dict or drops a message on an outbox queue, which the loop
drains. Nothing crosses a thread boundary without one of those two.

State comes from a `state` trigger subscription rather than the whole state_changed
stream, so Home Assistant filters it server side. A plain state trigger also fires on
attribute-only changes, which is required here: the player's volume lives in an
attribute.
"""

from __future__ import annotations

import asyncio
import json
import logging
import threading

import websockets

LOG = logging.getLogger("cfa-deck.ha")

# Enough to notice a dead link on a wall panel without hammering a sleeping laptop.
PING_INTERVAL = 20
BACKOFF_START = 3
BACKOFF_MAX = 30


class HomeAssistant:
    """One WebSocket session to Home Assistant, with automatic reconnect."""

    def __init__(self, url: str, token: str, entities: dict) -> None:
        self.url = url
        self.token = token
        self.entities = entities
        self._states: dict[str, dict] = {}
        self._lock = threading.Lock()
        self._online = False
        self._loop: asyncio.AbstractEventLoop | None = None
        self._outbox: asyncio.Queue | None = None
        self._stopping = threading.Event()
        self._id = 0
        self.thread = threading.Thread(target=self._run, name="ha-client", daemon=True)

    def start(self) -> None:
        self.thread.start()

    def shutdown(self) -> None:
        self._stopping.set()
        if self._loop is not None:
            self._loop.call_soon_threadsafe(self._loop.stop)
        self.thread.join(timeout=5)

    # -- what the panel reads --------------------------------------------------------

    def status(self) -> dict:
        """A snapshot for the deck. Reads only the lock-protected copy of the states."""
        with self._lock:
            online = self._online
            states = {eid: dict(state) for eid, state in self._states.items()}

        def get(name):
            return states.get(self.entities.get(name, ""), {})

        listen = get("listen")
        player = get("player")
        district = get("district")
        level = player.get("attributes", {}).get("volume_level")
        try:
            callouts = int(float(get("callouts").get("state", 0)))
        except (TypeError, ValueError):
            callouts = 0

        return {
            "online": online,
            "listening": listen.get("state") == "on",
            "manual": bool(listen.get("attributes", {}).get("manual")),
            "district": district.get("state"),
            "volume": int(round((level or 0) * 100)),
            "callouts": callouts,
            "feed_connected": get("feed_connected").get("state") == "on",
            "feed_stale": get("feed_stale").get("state") == "on",
        }

    # -- what the panel presses ------------------------------------------------------

    def toggle_listen(self) -> None:
        self._call("switch", "toggle", self.entities["listen"])

    def change_volume(self, delta: int) -> None:
        """Move the player's volume by delta percent.

        volume_set rather than volume_up: the level is read back from the player's own
        attribute, and not every player integration implements the stepped services.
        """
        target = max(0.0, min(1.0, (self.status()["volume"] + delta) / 100))
        self._call("media_player", "volume_set", self.entities["player"],
                   {"volume_level": round(target, 2)})

    def next_district(self) -> None:
        with self._lock:
            state = dict(self._states.get(self.entities["district"], {}))
        options = state.get("attributes", {}).get("options") or []
        if not options:
            LOG.warning("No district options known yet, ignoring the press")
            return
        try:
            index = options.index(state.get("state")) + 1
        except ValueError:
            index = 0
        self._call("select", "select_option", self.entities["district"],
                   {"option": options[index % len(options)]})

    def _call(self, domain: str, service: str, entity_id: str, data: dict | None = None) -> None:
        message = {
            "type": "call_service",
            "domain": domain,
            "service": service,
            "service_data": data or {},
            "target": {"entity_id": entity_id},
        }
        loop, outbox = self._loop, self._outbox
        if loop is None or outbox is None or not self._online:
            LOG.warning("Not connected to Home Assistant, dropping %s.%s", domain, service)
            return
        LOG.info("Calling %s.%s on %s %s", domain, service, entity_id, data or "")
        loop.call_soon_threadsafe(outbox.put_nowait, message)

    # -- the loop --------------------------------------------------------------------

    def _run(self) -> None:
        asyncio.run(self._main())

    async def _main(self) -> None:
        self._loop = asyncio.get_running_loop()
        backoff = BACKOFF_START
        while not self._stopping.is_set():
            try:
                await self._session()
                backoff = BACKOFF_START
            except Exception as exc:
                LOG.warning("Home Assistant connection failed: %s", exc)
            self._set_online(False)
            if self._stopping.is_set():
                return
            await asyncio.sleep(backoff)
            backoff = min(BACKOFF_MAX, backoff * 2)

    async def _session(self) -> None:
        self._outbox = asyncio.Queue()
        async with websockets.connect(
            self.url, max_size=None, ping_interval=PING_INTERVAL, ping_timeout=PING_INTERVAL
        ) as socket:
            await self._authenticate(socket)

            for state in await self._request(socket, {"type": "get_states"}):
                if state["entity_id"] in self.entities.values():
                    self._store(state["entity_id"], state)

            await self._request(socket, {
                "type": "subscribe_trigger",
                "trigger": {"platform": "state",
                            "entity_id": sorted(set(self.entities.values()))},
            })
            self._set_online(True)
            LOG.info("Connected to Home Assistant, watching %s entities", len(set(self.entities.values())))

            writer = asyncio.create_task(self._writer(socket))
            try:
                async for raw in socket:
                    self._handle(json.loads(raw))
            finally:
                writer.cancel()

    async def _authenticate(self, socket) -> None:
        hello = json.loads(await socket.recv())
        if hello.get("type") != "auth_required":
            raise RuntimeError(f"unexpected greeting: {hello.get('type')}")
        await socket.send(json.dumps({"type": "auth", "access_token": self.token}))
        result = json.loads(await socket.recv())
        if result.get("type") != "auth_ok":
            raise RuntimeError(f"authentication rejected: {result.get('message')}")

    async def _request(self, socket, payload: dict):
        """Send one command and wait for its result, handling events seen while waiting."""
        self._id += 1
        payload["id"] = self._id
        await socket.send(json.dumps(payload))
        while True:
            message = json.loads(await socket.recv())
            if message.get("id") != self._id or message.get("type") != "result":
                self._handle(message)
                continue
            if not message.get("success"):
                raise RuntimeError(f"{payload['type']} failed: {message.get('error')}")
            return message.get("result")

    async def _writer(self, socket) -> None:
        """One task owns sending, because concurrent sends can interleave frames."""
        while True:
            message = await self._outbox.get()
            self._id += 1
            message["id"] = self._id
            await socket.send(json.dumps(message))

    def _handle(self, message: dict) -> None:
        if message.get("type") == "result" and not message.get("success"):
            LOG.warning("Home Assistant rejected a command: %s", message.get("error"))
            return
        if message.get("type") != "event":
            return
        trigger = (message.get("event") or {}).get("variables", {}).get("trigger") or {}
        new_state = trigger.get("to_state")
        if new_state:
            self._store(new_state["entity_id"], new_state)

    def _store(self, entity_id: str, state: dict) -> None:
        with self._lock:
            self._states[entity_id] = {"state": state.get("state"),
                                       "attributes": state.get("attributes") or {}}

    def _set_online(self, online: bool) -> None:
        with self._lock:
            changed = self._online != online
            self._online = online
        if changed:
            LOG.info("Home Assistant %s", "online" if online else "offline")
