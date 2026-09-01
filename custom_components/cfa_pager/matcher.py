"""Page parsing, capcode matching and deduplication.

This is the logic validated offline against 13,435 real messages by tools/replay.py in
the repo, which agreed exactly with what the Pi daemon actually fired (14 of 14). Keep the
two in step: a change here without a replay run is a change of unknown effect.

No Home Assistant imports, so it stays testable on its own.
"""

from __future__ import annotations

import json
import re

# Keys that may carry the page body, longest wins. The live feed always uses message.text,
# but other POCSAG decoders vary and the cost of tolerating them is one loop.
TEXT_KEYS = ("message", "text", "body", "msg", "alpha", "content")


def normalise_capcode(value) -> str:
    """Strip non-digits then leading zeros, so 000575488 becomes 575488."""
    digits = re.sub(r"\D", "", str(value))
    return digits.lstrip("0") or ""


def collapse(text: str) -> str:
    """Collapse whitespace, for the dedupe key and for display."""
    return re.sub(r"\s+", " ", text or "").strip()


def parse_page(payload: str) -> dict | None:
    """Return a page dict, or None if the payload is not a usable pager message."""
    try:
        data = json.loads(payload)
    except (ValueError, TypeError):
        return None
    if not isinstance(data, dict):
        return None

    capcode_field = data.get("capcode")
    if isinstance(capcode_field, dict):
        capcode = normalise_capcode(capcode_field.get("id", ""))
        description = (capcode_field.get("description") or "").strip()
        alphacode = (capcode_field.get("alphacode") or "").strip().upper()
        agency = (capcode_field.get("agency") or "").strip().upper()
    else:
        capcode = normalise_capcode(capcode_field or "")
        description = alphacode = agency = ""

    message = data.get("message")
    text = ""
    if isinstance(message, dict):
        for key in TEXT_KEYS:
            value = message.get(key)
            if isinstance(value, str) and len(value) > len(text):
                text = value
        message_type = (message.get("type") or "").strip()
        stamp = (message.get("timestamp") or "").strip()
    elif isinstance(message, str):
        text, message_type, stamp = message, "", ""
    else:
        text = message_type = stamp = ""

    if not capcode:
        return None
    return {
        "capcode": capcode,
        "description": description,
        "alphacode": alphacode,
        "agency": agency,
        "text": collapse(text),
        "type": message_type,
        "feed_timestamp": stamp,
    }


class Deduper:
    """Suppress a repeated capcode plus text within a window.

    The window runs from first sight and a hit does not extend it, matching the Pi daemon.
    A window of 0 disables suppression.
    """

    def __init__(self, window: float) -> None:
        self.window = window
        self._seen: dict[str, float] = {}

    def is_duplicate(self, key: str, now: float) -> bool:
        if not self.window:
            return False
        self._seen = {k: t for k, t in self._seen.items() if now - t < self.window}
        if key in self._seen:
            return True
        self._seen[key] = now
        return False


def dedupe_key(page: dict) -> str:
    return f"{page['capcode']}|{page['text']}"
