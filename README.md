# CFA Pager for Home Assistant

Watch a POCSAG pager feed over MQTT and raise Home Assistant events when *your* brigades
are paged. Built for the Victorian CFA feed on [pocsag.info](https://pocsag.info), but the
broker, topics and capcodes are all configurable, so any similar feed works.

Everything is set up in the UI. No YAML.

## Why an integration and not the MQTT integration

Home Assistant's MQTT integration is limited to **one broker** (its manifest sets
`single_config_entry`), and that broker is almost certainly your own. Watching a second,
public broker therefore needs either a Mosquitto bridge — which means editing add-on
config, a `/share` file and a broker restart that briefly drops every other MQTT device in
the house — or an integration with its own client. This is the latter.

`paho-mqtt` already ships with Home Assistant, so this adds no dependencies.

## Install

**HACS:** add this repository as a custom repository (category: Integration), install, then
restart Home Assistant.

**Manual:** copy `custom_components/cfa_pager` into your `config/custom_components/`, then
restart.

Then **Settings → Devices & Services → Add Integration → CFA Pager**.

## Configuration

All of it is in the UI, and every field can be changed later from **Configure**. Saving
tests the connection first, then reloads the integration, so changes apply without a
restart.

| Field | Notes |
| --- | --- |
| Broker host / port | Defaults to `pocsag.info` on 1883 |
| Client ID | Must be unique on that broker. Two clients sharing an ID keep kicking each other off, and both go silent |
| Username / password | Leave blank for an anonymous broker |
| Use TLS | For a broker on 8883. Optionally skip certificate verification for a self-signed cert |
| Topics | `agency/#` gives each page once. A bare `#` delivers every page three times on this feed, because it republishes under `message_type` and `incident_type` as well |
| Brigades | Station names (`LAHARUM`), alphacodes (`LAHA`) or raw capcodes (`575488`). Anything that cannot be resolved is reported in the form rather than silently ignored |
| Duplicate window | Seconds. The same brigade and message text arriving twice inside the window counts once. `0` disables |
| Callouts to keep | Rolling history, restored across restarts |
| Recent pages to keep | All traffic, not just your brigades. `0` turns the entity off |

Brigade **names** resolve against a bundled lookup of 1558 Victorian capcodes. Raw numbers
always work, whether the lookup knows them or not, so other regions are fine.

## Entities

On a fresh install, under a device named **CFA Pager**:

| Entity | What it is |
| --- | --- |
| `sensor.cfa_pager_last_callout` | Timestamp of the last callout. Attributes carry the brigade, capcode, agency, message text, and the rolling `callouts` list |
| `sensor.cfa_pager_callouts_today` | Count since local midnight, with 7 and 30 day figures in attributes |
| `sensor.cfa_pager_pages_seen` | Every page on the feed, matched or not. Proves ingestion is alive |
| `sensor.cfa_pager_recent_pages` | The last N pages in a `pages` attribute, for a traffic view |
| `binary_sensor.cfa_pager_feed_connected` | The MQTT socket state |
| `binary_sensor.cfa_pager_feed_stale` | Problem when nothing at all has arrived for 30 minutes. The failure that otherwise looks like a quiet night |

Message text lives in attributes because a state is capped at 255 characters.

## Services

| Service | What it does |
| --- | --- |
| `cfa_pager.clear_history` | Forgets stored callouts and recent pages. `callouts` and `pages` are separate flags, because after testing you usually want the synthetic callouts gone and the live traffic kept |

## Events

| Event | When |
| --- | --- |
| `cfa_pager_callout` | A page matched one of your brigades and survived deduplication |
| `cfa_pager_page` | Every page received, matched or not |

Both carry `capcode`, `alphacode`, `description`, `agency`, `text`, `topic`, `ts`, and
`brigade` on a callout. Hook `cfa_pager_callout` for notifications, lights, sirens, or
anything else:

```yaml
automation:
  - alias: Pager callout to my phone
    triggers:
      - trigger: event
        event_type: cfa_pager_callout
    actions:
      - action: notify.mobile_app_your_phone
        data:
          title: "Callout: {{ trigger.event.data.brigade }}"
          message: "{{ trigger.event.data.text }}"
```

## Showing recent pages on a dashboard

More card examples, including a single card with everything on it, are in
[docs/dashboard.md](docs/dashboard.md).

`sensor.cfa_pager_recent_pages` holds the list in an attribute. A markdown card renders it:

There are two separate limits, and it is worth keeping them apart:

- **How many are kept** is the *Recent pages to keep* option, set in the UI.
- **How many are shown** is the `limit` at the top of the card below. Showing fewer than
  are kept is usually what you want: the attribute can hold 50 while the wall display shows
  the last 10.

```yaml
type: markdown
title: Recent pager traffic
content: |
  {% set limit = 10 %}
  {% set pages = (state_attr('sensor.cfa_pager_recent_pages', 'pages') or [])[:limit] %}
  | Time | Agency | Code | Message |
  |---|---|---|---|
  {% for p in pages -%}
  | {{ p.ts | timestamp_custom('%H:%M:%S') }} | {{ p.agency }} | {{ p.alphacode or p.capcode }} | {{ p.text }} |
  {% endfor %}
```

The same shape gives your own callout history, from the other entity and attribute:

```yaml
type: markdown
title: Your callouts
content: |
  {% set limit = 10 %}
  {% set callouts = (state_attr('sensor.cfa_pager_last_callout', 'callouts') or [])[:limit] %}
  {% if callouts %}
  | Time | Brigade | Message |
  |---|---|---|
  {% for c in callouts -%}
  | {{ c.ts | timestamp_custom('%d %b %H:%M') }} | {{ c.brigade or c.description }} | {{ c.text }} |
  {% endfor %}
  {% else %}
  No callouts yet.
  {% endif %}
```

Markdown tables must start at column 0: four or more leading spaces make Markdown render
the whole thing as a code block instead.

## One thing to add yourself

`sensor.cfa_pager_recent_pages` changes on **every** page — roughly 670 times a day on the
CFA feed — and carries the whole list in its attributes. Nothing downstream wants that
history, so exclude it from the recorder, and from InfluxDB if you use it:

```yaml
recorder:
  exclude:
    entities:
      - sensor.cfa_pager_recent_pages
```

An integration cannot do this for you; recorder filtering is user config. Set **Recent
pages to keep** to `0` if you would rather not have the entity at all.

## Notes on the feed

- Volume on the CFA feed is about **0.5 pages a minute**, and a single brigade pages
  roughly **1.5 times a day**.
- Every page already carries its own station description and alphacode, so callouts are
  labelled correctly even for capcodes the bundled lookup does not know.
- Never publish to `pocsag.info`. It carries live emergency traffic. To test, point the
  integration at your own broker and inject a page there.

## Development

`tools/replay.py` replays a captured log of raw feed messages through the integration's own
matcher and diffs the result against what a reference implementation actually fired. It
imports `matcher.py` and `lookup.py` directly, so a passing run is evidence about the code
that ships, not a lookalike copy.

```bash
python3 tools/replay.py --raw /path/to/raw.jsonl --history /path/to/history.db
```

`tools/deploy.sh` copies the integration to a Home Assistant config directory, validates
the configuration over the REST API, and optionally restarts.

## If you fork or publish this

The HACS validation job in CI only runs when the repository is public: the HACS action
reads `hacs.json` and the manifest over unauthenticated HTTP, so on a private repository
both content checks fail with "expected a dictionary. Got None" regardless of what those
files contain. Going public is also the only state in which anyone can install from it.

The `brands` check is ignored deliberately. It requires the integration to be listed in the
[home-assistant/brands](https://github.com/home-assistant/brands) repository, which is a
pull request against a repo you do not own and only matters for inclusion in the default
HACS store, not for a custom repository.

## Licence

MIT.
