# Dashboard cards

The callout and page lists live in entity **attributes**, because a Home Assistant state is
capped at 255 characters. Tile and entity cards can only show a state, which is why they
show a count. A Markdown card renders the lists.

Add one with **Edit dashboard → Add card → Manual**, and paste the whole block including
the `type:` line.

Entity IDs below are those of a fresh install. Check yours under **Settings → Devices &
services → CFA Pager → entities**: Home Assistant fixes an entity ID at first registration,
so an install that predates a rename keeps the older IDs.

## Everything in one card

Status line, your callouts, and all recent traffic. The two limits on the first lines
control how many rows each table shows.

```yaml
type: markdown
title: CFA Pager
content: |
  {% set limit_callouts = 5 %}
  {% set limit_pages = 10 %}
  {% set callouts = state_attr('sensor.cfa_pager_last_callout','callouts') or [] %}
  {% set pages = state_attr('sensor.cfa_pager_recent_pages','pages') or [] %}
  {% set connected = is_state('binary_sensor.cfa_pager_feed_connected','on') %}
  {% set stale = is_state('binary_sensor.cfa_pager_feed_stale','on') %}
  **Feed:** {{ 'connected' if connected else 'DISCONNECTED' }}{% if stale %} · **STALE** {% endif %} · {{ states('sensor.cfa_pager_pages_seen') }} pages seen · {{ states('sensor.cfa_pager_callouts_today') }} today

  ### Your callouts
  {% if callouts %}
  | Time | Brigade | Message |
  |---|---|---|
  {% for c in callouts[:limit_callouts] -%}
  | {{ c.ts | timestamp_custom('%d %b %H:%M') }} | {{ c.brigade or c.description }} | {{ c.text }} |
  {% endfor %}
  {% else %}
  _No callouts recorded yet._
  {% endif %}

  ### Recent pager traffic
  {% if pages %}
  | Time | Agency | Code | Message |
  |---|---|---|---|
  {% for p in pages[:limit_pages] -%}
  | {{ p.ts | timestamp_custom('%H:%M:%S') }} | {{ p.agency }} | {{ p.alphacode or p.capcode }} | {{ p.text }} |
  {% endfor %}
  {% else %}
  _Nothing received yet._
  {% endif %}
```

## Just the callouts

```yaml
type: markdown
title: Your callouts
content: |
  {% set limit = 10 %}
  {% set callouts = (state_attr('sensor.cfa_pager_last_callout','callouts') or [])[:limit] %}
  {% if callouts %}
  | Time | Brigade | Message |
  |---|---|---|
  {% for c in callouts -%}
  | {{ c.ts | timestamp_custom('%d %b %H:%M') }} | {{ c.brigade or c.description }} | {{ c.text }} |
  {% endfor %}
  {% else %}
  _No callouts recorded yet._
  {% endif %}
```

## Just the traffic

```yaml
type: markdown
title: Recent pager traffic
content: |
  {% set limit = 10 %}
  {% set pages = (state_attr('sensor.cfa_pager_recent_pages','pages') or [])[:limit] %}
  | Time | Agency | Code | Message |
  |---|---|---|---|
  {% for p in pages -%}
  | {{ p.ts | timestamp_custom('%H:%M:%S') }} | {{ p.agency }} | {{ p.alphacode or p.capcode }} | {{ p.text }} |
  {% endfor %}
```

## Status tiles

Tiles suit the entities whose state is the whole story.

```yaml
type: grid
columns: 2
square: false
cards:
  - type: tile
    entity: sensor.cfa_pager_callouts_today
    name: Callouts today
  - type: tile
    entity: sensor.cfa_pager_last_callout
    name: Last callout
  - type: tile
    entity: binary_sensor.cfa_pager_feed_connected
    name: Feed
  - type: tile
    entity: sensor.cfa_pager_recent_pages
    name: Pages held
```

## Two limits, not one

| Limit | Where | What it does |
| --- | --- | --- |
| Recent pages to keep | Integration options, in the UI | How many pages the integration holds in the attribute |
| `limit` in the card | The card YAML | How many rows the card draws |

Holding more than you display is usually right: keep 50 for history, show 10 on a wall
screen so it stays readable at a distance.

## Gotchas

- **Markdown tables must start at column 0.** Four or more leading spaces make Markdown
  render the table as a code block instead.
- **`{% raw %}{{ }}{% endraw %}` templating works in Markdown cards** but not in tile or
  entity cards, which is the other reason the lists need a Markdown card.
- Timestamps in the attributes are epoch seconds, so `timestamp_custom` formats them.
  `%d %b %H:%M` gives `01 Sep 14:32`.
