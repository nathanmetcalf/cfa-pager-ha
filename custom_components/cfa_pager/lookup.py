"""Brigade lookup, so the UI can accept names rather than bare capcodes.

The bundled capcodes.csv is a build-time convenience only: every page on the wire already
carries its own description and alphacode, so nothing here is needed to match or to label
a live callout. It exists purely so "LAHARUM" can be typed into the config flow instead of
575488, and so a typo is caught in the UI rather than silently never paging.

Tab separated, no header: capcode, agency, description, alphacode, flag.
A literal \\N means NULL.
"""

from __future__ import annotations

import csv
import functools
import logging
import os
import re

_LOGGER = logging.getLogger(__name__)

CSV_PATH = os.path.join(os.path.dirname(__file__), "capcodes.csv")


def normalise(value) -> str:
    """Strip non-digits then leading zeros, so 000575488 becomes 575488."""
    digits = re.sub(r"\D", "", str(value))
    return digits.lstrip("0") or ""


@functools.lru_cache(maxsize=1)
def _tables() -> tuple[dict, dict, dict]:
    """Load the CSV once per process into capcode, alphacode and name indexes."""
    by_capcode: dict[str, dict] = {}
    by_alphacode: dict[str, dict] = {}
    by_name: dict[str, dict] = {}
    try:
        with open(CSV_PATH, newline="", encoding="utf-8") as handle:
            for record in csv.reader(handle, delimiter="\t"):
                if len(record) < 4:
                    continue
                capcode = normalise(record[0])
                if not capcode:
                    continue
                entry = {
                    "capcode": capcode,
                    "agency": record[1].strip(),
                    "description": record[2].strip(),
                    "alphacode": record[3].strip().upper(),
                }
                by_capcode[capcode] = entry
                if entry["alphacode"] and entry["alphacode"] != "\\N":
                    by_alphacode.setdefault(entry["alphacode"], entry)
                if entry["description"]:
                    by_name.setdefault(entry["description"].upper(), entry)
    except OSError as err:
        _LOGGER.warning("Could not read %s: %s", CSV_PATH, err)
    return by_capcode, by_alphacode, by_name


def resolve_one(text: str) -> dict | None:
    """Resolve one entry to {capcode, label}, or None if it means nothing.

    A bare number always resolves, known to the lookup or not, so a capcode missing from
    the CSV can still be watched.
    """
    by_capcode, by_alphacode, by_name = _tables()
    text = str(text).strip()
    if not text:
        return None
    if text.replace(" ", "").isdigit():
        capcode = normalise(text)
        if not capcode:
            return None
        entry = by_capcode.get(capcode)
        return {
            "capcode": capcode,
            "label": entry["description"] if entry else f"capcode {capcode}",
            "known": entry is not None,
        }
    entry = by_name.get(text.upper()) or by_alphacode.get(text.upper())
    if not entry:
        return None
    return {"capcode": entry["capcode"], "label": entry["description"], "known": True}


def resolve_many(entries) -> tuple[dict[str, str], list[str]]:
    """Return ({capcode: label}, unresolved) for a list of names, alphacodes or numbers."""
    resolved: dict[str, str] = {}
    unresolved: list[str] = []
    for raw in entries or []:
        found = resolve_one(raw)
        if found:
            resolved[found["capcode"]] = found["label"]
        elif str(raw).strip():
            unresolved.append(str(raw).strip())
    return resolved, unresolved


def suggestions(prefix: str = "", limit: int = 20) -> list[str]:
    """Station names matching a prefix, for showing the user what is available."""
    _, _, by_name = _tables()
    prefix = prefix.upper()
    names = sorted(n for n in by_name if n.startswith(prefix))
    return names[:limit]
