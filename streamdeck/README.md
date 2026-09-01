# Stream Deck panel

Six physical keys for the pager radio, driven entirely by Home Assistant. The deck plugs
into a machine on your LAN; this daemon runs there and talks to Home Assistant over the
WebSocket API. It holds no pager logic of its own.

```
+----------+----------+----------+
|  LISTEN  |  VOL -   |  VOL +   |
+----------+----------+----------+
| DISTRICT | CALLOUTS |   FEED   |
+----------+----------+----------+
```

| Key | Shows | Press |
|---|---|---|
| LISTEN | on / off, blue when started by hand | Toggles `switch.cfa_pager_listen` |
| VOL - | current volume | Lowers the player by `volume_step` percent |
| VOL + | current volume | Raises the player by `volume_step` percent |
| DISTRICT | selected stream, e.g. D17 | Advances `select.cfa_pager_stream`, wrapping |
| CALLOUTS | today's count, red when above zero | Nothing, display only |
| FEED | ok / stale / down | Nothing, display only |

If the connection to Home Assistant drops, every key goes dark rather than showing state
that may have moved on.

Tested on a Stream Deck Mini (6 keys, 80x80). The code asks the device for its key count
and image size, so a larger deck lights the first six keys and leaves the rest blank.

## Install

```bash
sudo apt install python3-pil python3-yaml python3-venv libhidapi-libusb0
python3 -m venv --system-site-packages ~/cfa-pager-ha/streamdeck/.venv
~/cfa-pager-ha/streamdeck/.venv/bin/pip install streamdeck websockets
```

`--system-site-packages` reuses the apt Pillow and PyYAML, so only the two libraries
Debian does not package land in the venv.

Create a long lived access token under your Home Assistant profile, then:

```bash
mkdir -p ~/.config/cfa-deck && chmod 700 ~/.config/cfa-deck
install -m 600 /dev/null ~/.config/cfa-deck/token
# paste the token into that file
cp config.example.yaml config.yaml   # then set the WebSocket URL
```

Check the key art before touching the device. It renders with no deck attached:

```bash
python3 preview.py /tmp/deck.png
```

Run it in the foreground first:

```bash
.venv/bin/python3 deck.py --verbose
```

Then install the service:

```bash
cp cfa-deck.service ~/.config/systemd/user/
systemctl --user daemon-reload
systemctl --user enable --now cfa-deck
loginctl enable-linger "$USER"     # so it survives logout and starts at boot
```

## Notes

- **No udev rule is needed.** logind grants the seat user an ACL on the USB HID node. That
  only holds while the daemon runs as that user, which is why this is a `--user` unit and
  why `enable-linger` matters.
- **Only one process may own the deck.** If you still have the older standalone
  `cfa-pager-radio` daemon installed, keep its service disabled, or the two will fight
  over the device and both will misbehave.
- **Icons must exist in DejaVu Sans.** The faces use Geometric Shapes (U+25xx). The
  Miscellaneous Technical media glyphs, U+23F8 pause among them, are absent from DejaVu
  and render as an empty box.
- Volume uses `media_player.volume_set` with the level read back from the player, because
  not every player integration implements the stepped volume services.
