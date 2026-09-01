#!/usr/bin/env python3
"""Render the six key faces to a PNG, with no Stream Deck attached.

    python3 preview.py /tmp/deck.png

Checks the art and the glyphs before touching the device: an icon missing from DejaVu
Sans renders as a tofu box, and that is only obvious by looking. Pass --status to preview
a different state, e.g. --status offline.
"""

from __future__ import annotations

import argparse
import sys

from PIL import Image

import deck_ui

KEY_SIZE = (80, 80)      # a Stream Deck Mini key
COLUMNS, ROWS = 3, 2
GAP = 8

SAMPLES = {
    "listening": {"online": True, "listening": True, "manual": False, "district": "D17",
                  "volume": 62, "callouts": 1, "feed_connected": True, "feed_stale": False},
    "idle": {"online": True, "listening": False, "manual": False, "district": "D13",
             "volume": 40, "callouts": 0, "feed_connected": True, "feed_stale": False},
    "manual": {"online": True, "listening": True, "manual": True, "district": "D13",
               "volume": 100, "callouts": 3, "feed_connected": True, "feed_stale": True},
    "feed-down": {"online": True, "listening": False, "manual": False, "district": "D17",
                  "volume": 0, "callouts": 0, "feed_connected": False, "feed_stale": True},
    "offline": {"online": False},
}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("output", nargs="?", default="/tmp/deck_preview.png")
    parser.add_argument("--status", choices=sorted(SAMPLES), default=None,
                        help="render one state only, instead of every state stacked")
    args = parser.parse_args()

    names = [args.status] if args.status else list(SAMPLES)
    sheet_width = COLUMNS * KEY_SIZE[0] + (COLUMNS + 1) * GAP
    block_height = ROWS * KEY_SIZE[1] + (ROWS + 1) * GAP
    sheet = Image.new("RGB", (sheet_width, block_height * len(names)), (12, 12, 12))

    for block, name in enumerate(names):
        faces = deck_ui.compute_faces(SAMPLES[name])
        for key in range(COLUMNS * ROWS):
            icon, title, value, theme = faces[key]
            face = deck_ui.render_face(KEY_SIZE, icon, title, value, theme)
            x = GAP + (key % COLUMNS) * (KEY_SIZE[0] + GAP)
            y = block * block_height + GAP + (key // COLUMNS) * (KEY_SIZE[1] + GAP)
            sheet.paste(face, (x, y))
        print(f"  {name}: " + " | ".join(f"{f[1]}={f[2]}" for f in faces.values()))

    sheet.save(args.output)
    print(f"  wrote {args.output} ({sheet.size[0]}x{sheet.size[1]})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
