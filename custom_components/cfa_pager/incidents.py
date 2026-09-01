"""Nearby emergency incidents from a public GeoJSON feed.

Defaults to the VicEmergency feed, which publishes every going incident and warning in
Victoria. Each entry is reduced to what a dashboard needs, with the distance and compass
bearing from Home Assistant's own home coordinates, nearest first.

Filtering is a radius from home rather than a bounding box, so it needs no configuration
beyond one number and works wherever Home Assistant thinks it is.

Blocking: urllib blocks, so fetch() belongs in an executor.
"""

from __future__ import annotations

import gzip
import json
import logging
import math
import urllib.request

_LOGGER = logging.getLogger(__name__)

COMPASS = ["N", "NNE", "NE", "ENE", "E", "ESE", "SE", "SSE",
           "S", "SSW", "SW", "WSW", "W", "WNW", "NW", "NNW"]


def haversine_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    radius = 6371.0
    dlat = math.radians(lat2 - lat1)
    dlon = math.radians(lon2 - lon1)
    a = (math.sin(dlat / 2) ** 2
         + math.cos(math.radians(lat1)) * math.cos(math.radians(lat2))
         * math.sin(dlon / 2) ** 2)
    return 2 * radius * math.asin(math.sqrt(a))


def bearing_compass(lat1: float, lon1: float, lat2: float, lon2: float) -> str:
    dlon = math.radians(lon2 - lon1)
    y = math.sin(dlon) * math.cos(math.radians(lat2))
    x = (math.cos(math.radians(lat1)) * math.sin(math.radians(lat2))
         - math.sin(math.radians(lat1)) * math.cos(math.radians(lat2)) * math.cos(dlon))
    degrees = (math.degrees(math.atan2(y, x)) + 360) % 360
    return COMPASS[int((degrees + 11.25) % 360 / 22.5)]


def first_point(geometry) -> tuple[float, float] | None:
    """Return (lat, lon) for any GeoJSON geometry, or None.

    The feed mixes Point and GeometryCollection, and polygons for warning areas, so this
    walks into nested coordinate lists rather than assuming a shape.
    """
    if not isinstance(geometry, dict):
        return None
    kind = geometry.get("type")
    coords = geometry.get("coordinates")
    if kind == "Point" and isinstance(coords, list) and len(coords) >= 2:
        return coords[1], coords[0]
    if kind == "GeometryCollection":
        for inner in geometry.get("geometries", []):
            found = first_point(inner)
            if found:
                return found
        return None
    node = coords
    while isinstance(node, list) and node and isinstance(node[0], list):
        node = node[0]
    if isinstance(node, list) and len(node) >= 2 and isinstance(node[0], (int, float)):
        return node[1], node[0]
    return None


def fetch(url: str, user_agent: str, timeout: int = 25) -> list[dict]:
    """GET the feed and return its features. Handles gzip, which the feed sends
    whether or not it was requested."""
    request = urllib.request.Request(
        url, headers={"User-Agent": user_agent, "Accept": "*/*"}
    )
    with urllib.request.urlopen(request, timeout=timeout) as response:
        raw = response.read()
    if raw[:2] == b"\x1f\x8b":
        raw = gzip.decompress(raw)
    data = json.loads(raw.decode("utf-8", errors="replace"))
    return data.get("features") or []


def nearby(
    url: str,
    user_agent: str,
    home_lat: float,
    home_lon: float,
    radius_km: float,
    max_items: int = 40,
) -> list[dict]:
    """Incidents within radius_km of home, nearest first."""
    out: list[dict] = []
    for feature in fetch(url, user_agent):
        props = feature.get("properties") or {}
        point = first_point(feature.get("geometry"))
        if not point:
            continue
        lat, lon = point
        distance = haversine_km(home_lat, home_lon, lat, lon)
        if radius_km and distance > radius_km:
            continue
        out.append({
            # The feed puts a usable title in sourceTitle far more often than in name.
            "name": props.get("name") or props.get("sourceTitle") or "unnamed",
            "feed_type": props.get("feedType", ""),
            "category": props.get("category2") or props.get("category1") or "",
            "status": props.get("status", ""),
            "location": props.get("location", ""),
            "size": props.get("sizeFmt", ""),
            "updated": props.get("updated") or props.get("created") or "",
            "lat": round(lat, 4),
            "lon": round(lon, 4),
            "km": round(distance, 1),
            "dir": bearing_compass(home_lat, home_lon, lat, lon),
        })
    out.sort(key=lambda row: row["km"])
    return out[:max_items]
