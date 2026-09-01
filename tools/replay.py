#!/usr/bin/env python3
"""Replay the Pi's raw message log through the Home Assistant matching logic.

Why: the live rate is about 1.5 callouts a day, so waiting for real pages is a hopeless
way to validate a rewrite. This runs the same logic over every message the Pi has ever
received and diffs the result against what the Pi actually fired (history.db), giving
thousands of comparisons in a second.

The logic here is deliberately written to mirror the Jinja that will run in Home
Assistant, not to import the daemon's code. Importing the daemon would only prove the
daemon agrees with itself.

Usage: replay.py [--raw /tmp/raw.jsonl] [--history /tmp/history.db] [--window 90]
"""

import argparse
import json
import re
import sqlite3
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "custom_components/cfa_pager"))

# The integration's own code, so this parity result applies to what actually ships.
import lookup
from matcher import Deduper, collapse, dedupe_key, parse_page

import yaml


def main():
    root = Path(__file__).resolve().parent.parent
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--raw", default="/tmp/raw.jsonl")
    parser.add_argument("--history", default="/tmp/history.db")
    parser.add_argument("--window", type=float, default=90.0)
    parser.add_argument("--verbose", action="store_true")
    args = parser.parse_args()

    wanted = yaml.safe_load((root / "data/watched.yaml").read_text())["watched"]
    resolved, unresolved = lookup.resolve_many(wanted)
    if unresolved:
        raise SystemExit(f"could not resolve: {', '.join(unresolved)}")
    allowlist = set(resolved)

    deduper = Deduper(args.window)
    fired, suppressed, seen, malformed = [], 0, 0, 0
    for line in open(args.raw, encoding="utf-8"):
        try:
            row = json.loads(line)
        except ValueError:
            malformed += 1
            continue
        seen += 1
        page = parse_page(row.get("payload", ""))
        if not page or page["capcode"] not in allowlist:
            continue
        if deduper.is_duplicate(dedupe_key(page), row["ts"]):
            suppressed += 1
            continue
        fired.append({"ts": row["ts"], "capcode": page["capcode"],
                      "name": page["description"] or resolved[page["capcode"]],
                      "text": page["text"]})

    print(f"messages replayed : {seen}  (malformed lines {malformed})")
    print(f"watched capcodes  : {len(allowlist)}")
    print(f"would fire        : {len(fired)}")
    print(f"deduped away      : {suppressed}")

    rows = sqlite3.connect(args.history).execute(
        "SELECT ts, capcode, description, text FROM callouts ORDER BY ts").fetchall()
    if not rows:
        print("\nno reference rows in history.db, nothing to diff")
        return 0

    # The Pi only started recording history partway through raw.jsonl, so compare on the
    # overlap only. Anything earlier is not evidence of disagreement.
    start = rows[0][0] - 1
    ref = {(lookup.normalise(r[1]), collapse(r[3])) for r in rows}
    mine = {(f["capcode"], f["text"]) for f in fired if f["ts"] >= start}

    print(f"\noverlap window    : {time.strftime('%Y-%m-%d %H:%M', time.localtime(start))}"
          f" -> {time.strftime('%Y-%m-%d %H:%M', time.localtime(rows[-1][0]))}")
    print(f"  pi fired        : {len(ref)}")
    print(f"  replay fired    : {len(mine)}")
    print(f"  agreed          : {len(ref & mine)}")

    only_pi, only_replay = ref - mine, mine - ref
    if only_pi:
        print(f"\n  MISSED by replay ({len(only_pi)}) - the port would not have paged you:")
        for code, text in sorted(only_pi):
            print(f"    {code}  {text[:88]}")
    if only_replay:
        print(f"\n  EXTRA in replay ({len(only_replay)}) - the port would page you spuriously:")
        for code, text in sorted(only_replay):
            print(f"    {code}  {text[:88]}")
    if not only_pi and not only_replay:
        print("\n  PARITY: exact agreement on the overlap")

    if args.verbose:
        print("\nall replayed callouts:")
        for f in fired:
            stamp = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(f["ts"]))
            print(f"  {stamp}  {f['name']:<18} {f['text'][:70]}")
    return 0 if not (only_pi or only_replay) else 1


if __name__ == "__main__":
    sys.exit(main())
