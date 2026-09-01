#!/usr/bin/env python3
"""Run the Stream Deck panel against Home Assistant.

    python3 deck.py --config config.yaml

Runs in the foreground and logs to stdout, which is what the systemd user unit wants.
Stop it with Ctrl-C or SIGTERM.
"""

from __future__ import annotations

import argparse
import logging
import os
import signal
import sys
import threading

import yaml

import deck_ui
from ha_client import HomeAssistant

LOG = logging.getLogger("cfa-deck")

DEFAULT_CONFIG = os.path.join(os.path.dirname(os.path.abspath(__file__)), "config.yaml")


def load_config(path: str) -> dict:
    with open(path) as handle:
        config = yaml.safe_load(handle) or {}
    for section in ("home_assistant", "entities"):
        if section not in config:
            raise SystemExit(f"{path}: missing the {section} section")
    return config


def read_token(config: dict) -> str:
    """Token from the environment if set, otherwise from the file named in the config."""
    token = os.environ.get("CFA_DECK_TOKEN")
    if token:
        return token.strip()
    path = os.path.expanduser(config["home_assistant"]["token_file"])
    try:
        with open(path) as handle:
            token = handle.read().strip()
    except OSError as exc:
        raise SystemExit(f"cannot read the Home Assistant token: {exc}")
    if not token:
        raise SystemExit(f"{path} is empty")
    return token


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", default=DEFAULT_CONFIG)
    parser.add_argument("--verbose", action="store_true")
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )

    config = load_config(args.config)
    if not deck_ui.available():
        raise SystemExit("the streamdeck library or Pillow is missing from this environment")

    client = HomeAssistant(
        url=config["home_assistant"]["url"],
        token=read_token(config),
        entities=config["entities"],
    )
    deck_config = config.get("deck") or {}
    panel = deck_ui.StreamDeckPanel(
        client,
        brightness=deck_config.get("brightness", 60),
        reconnect_seconds=deck_config.get("reconnect_seconds", 10),
        volume_step=deck_config.get("volume_step", 10),
    )

    client.start()
    panel.start()

    stopping = threading.Event()
    for received in (signal.SIGINT, signal.SIGTERM):
        signal.signal(received, lambda *_: stopping.set())
    LOG.info("Panel running. Press Ctrl-C to stop.")
    stopping.wait()

    LOG.info("Shutting down")
    panel.shutdown()
    client.shutdown()
    return 0


if __name__ == "__main__":
    sys.exit(main())
