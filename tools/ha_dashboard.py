#!/usr/bin/env python3
"""Create and update Home Assistant dashboards over the WebSocket API.

Storage-mode dashboards are not exposed over REST, and editing
.storage/lovelace.* by hand needs a restart to take effect. The WebSocket API
creates and saves them live, and the result stays editable in the UI.

Usage:
  ha_dashboard.py list
  ha_dashboard.py save <url-path> <config.yaml> [--title T] [--icon mdi:x]
  ha_dashboard.py delete <url-path>
"""

from __future__ import annotations

import asyncio
import json
import os
import sys

import websockets
import yaml

URL = os.environ.get("HA_WS_URL", "ws://172.20.30.10:8123/api/websocket")
TOKEN_FILE = os.environ.get(
    "TOKEN_FILE", os.path.expanduser("~/.config/trailcam/ha_token")
)


class Client:
    def __init__(self, socket) -> None:
        self.socket = socket
        self._id = 0

    async def send(self, payload: dict):
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
    if json.loads(await socket.recv()).get("type") != "auth_ok":
        raise SystemExit("authentication failed")
    return socket


async def main() -> int:
    if len(sys.argv) < 2:
        print(__doc__)
        return 1
    command = sys.argv[1]
    socket = await connect()
    client = Client(socket)
    try:
        boards = await client.send({"type": "lovelace/dashboards/list"})

        if command == "list":
            for board in boards:
                print(f"  {board.get('url_path')}  {board.get('title')!r}"
                      f"  mode={board.get('mode')}")
            return 0

        url_path = sys.argv[2]
        existing = next((b for b in boards if b.get("url_path") == url_path), None)

        if command == "delete":
            if not existing:
                print(f"  {url_path} does not exist")
                return 0
            await client.send({
                "type": "lovelace/dashboards/delete",
                "dashboard_id": existing["id"],
            })
            print(f"  deleted {url_path}")
            return 0

        if command == "save":
            config = yaml.safe_load(open(sys.argv[3]).read())
            title = config.pop("_title", url_path)
            icon = config.pop("_icon", "mdi:view-dashboard")
            if not existing:
                created = await client.send({
                    "type": "lovelace/dashboards/create",
                    "url_path": url_path,
                    "title": title,
                    "icon": icon,
                    "show_in_sidebar": True,
                    "require_admin": False,
                })
                print(f"  created dashboard {url_path} (id {created.get('id')})")
            else:
                print(f"  updating existing dashboard {url_path}")
            await client.send({
                "type": "lovelace/config/save",
                "url_path": url_path,
                "config": config,
            })
            views = len(config.get("views", []))
            cards = sum(len(v.get("cards", [])) + sum(
                len(s.get("cards", [])) for s in v.get("sections", []))
                for v in config.get("views", []))
            print(f"  saved {views} view(s), {cards} card(s)")
            return 0

        raise SystemExit(f"unknown command {command!r}")
    finally:
        await socket.close()


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
