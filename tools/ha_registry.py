#!/usr/bin/env python3
"""Rename or remove entity registry entries over the Home Assistant WebSocket API.

The entity registry is not exposed over REST, and editing .storage/core.entity_registry by
hand is unsafe while Home Assistant is running: it holds the registry in memory and will
write over the file. The WebSocket API is the supported route.

An entity id is fixed at first registration and never changes on its own, so an entity that
was registered before a naming change keeps the old id forever unless renamed here.

Usage:
  ha_registry.py list cfa                     # entries whose id contains a string
  ha_registry.py rename sensor.old sensor.new
  ha_registry.py remove sensor.orphan
"""

from __future__ import annotations

import asyncio
import json
import os
import sys

import websockets

URL = os.environ.get("HA_WS_URL", "ws://172.20.30.10:8123/api/websocket")
TOKEN_FILE = os.environ.get(
    "TOKEN_FILE", os.path.expanduser("~/.config/trailcam/ha_token")
)


class Registry:
    def __init__(self, socket) -> None:
        self.socket = socket
        self._id = 0

    async def send(self, payload: dict) -> dict:
        self._id += 1
        payload["id"] = self._id
        await self.socket.send(json.dumps(payload))
        while True:
            message = json.loads(await self.socket.recv())
            if message.get("id") == self._id and message.get("type") == "result":
                if not message.get("success"):
                    raise SystemExit(f"failed: {json.dumps(message.get('error'))}")
                return message.get("result")


async def connect():
    token = open(TOKEN_FILE).read().strip()
    socket = await websockets.connect(URL, max_size=None)
    hello = json.loads(await socket.recv())
    if hello.get("type") != "auth_required":
        raise SystemExit(f"unexpected greeting: {hello}")
    await socket.send(json.dumps({"type": "auth", "access_token": token}))
    result = json.loads(await socket.recv())
    if result.get("type") != "auth_ok":
        raise SystemExit(f"authentication failed: {result}")
    return socket


async def main() -> int:
    if len(sys.argv) < 2:
        print(__doc__)
        return 1
    command = sys.argv[1]
    socket = await connect()
    registry = Registry(socket)
    try:
        entries = await registry.send({"type": "config/entity_registry/list"})

        if command == "list":
            needle = sys.argv[2] if len(sys.argv) > 2 else ""
            for entry in sorted(entries, key=lambda e: e["entity_id"]):
                if needle in entry["entity_id"]:
                    print(f"  {entry['entity_id']:<44} unique_id={entry.get('unique_id')}")
            return 0

        if command == "rename":
            old, new = sys.argv[2], sys.argv[3]
            if not any(e["entity_id"] == old for e in entries):
                print(f"  {old} is not in the registry, skipping")
                return 0
            if any(e["entity_id"] == new for e in entries):
                raise SystemExit(f"  {new} already exists, refusing to clobber it")
            await registry.send({
                "type": "config/entity_registry/update",
                "entity_id": old,
                "new_entity_id": new,
            })
            print(f"  renamed {old} -> {new}")
            return 0

        if command == "remove":
            target = sys.argv[2]
            if not any(e["entity_id"] == target for e in entries):
                print(f"  {target} is not in the registry, nothing to remove")
                return 0
            await registry.send({
                "type": "config/entity_registry/remove",
                "entity_id": target,
            })
            print(f"  removed {target}")
            return 0

        raise SystemExit(f"unknown command {command!r}")
    finally:
        await socket.close()


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
