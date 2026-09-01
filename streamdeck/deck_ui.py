#!/usr/bin/env python3
"""Stream Deck front panel for the Home Assistant pager radio.

Layout on a 6-key Stream Deck Mini (3 across, 2 down):

    +----------+----------+----------+
    |  LISTEN  |  VOL -   |  VOL +   |   top row
    +----------+----------+----------+
    | DISTRICT | CALLOUTS |   FEED   |   bottom row
    +----------+----------+----------+

LISTEN toggles the listen switch. VOL - and VOL + move the player's volume by the
configured step. DISTRICT advances to the next stream and wraps. CALLOUTS and FEED are
display only: today's callout count, and whether the pager feed is healthy.

The panel talks to Home Assistant through a backend object with four methods, so this
module knows nothing about WebSockets or entity ids. Anything with the same four methods
can drive it.
"""

import logging
import threading

LOG = logging.getLogger("cfa-deck.ui")

KEY_LISTEN = 0
KEY_VOL_DOWN = 1
KEY_VOL_UP = 2
KEY_DISTRICT = 3
KEY_CALLOUTS = 4
KEY_FEED = 5

# Icons must exist in DejaVu Sans or they render as a tofu box. Geometric Shapes
# (U+25xx) are present; the Miscellaneous Technical media symbols such as U+23F8
# pause are not.
FONT_BOLD = "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"
FONT_PLAIN = "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"

COLOUR_TEXT = (245, 245, 245)
COLOUR_DIM = (150, 150, 145)
COLOUR_EDGE = (86, 86, 82)

# Each key is painted as a vertical gradient from the first colour to the second, which
# reads as a lit physical key rather than a flat swatch. Names match the state they mean.
THEMES = {
    "idle":   ((44, 44, 42), (24, 24, 23)),
    "live":   ((18, 138, 56), (8, 74, 30)),
    "manual": ((34, 104, 190), (16, 52, 100)),
    "alert":  ((170, 40, 36), (96, 20, 18)),
    "action": ((58, 58, 62), (32, 32, 35)),
    "muted":  ((34, 34, 33), (22, 22, 21)),
}


def _paint_gradient(image, top, bottom):
    """Fill an image with a vertical gradient, drawn row by row."""
    from PIL import ImageDraw

    draw = ImageDraw.Draw(image)
    width, height = image.size
    span = max(1, height - 1)
    for y in range(height):
        ratio = y / span
        draw.line(
            [(0, y), (width, y)],
            fill=tuple(int(top[i] + (bottom[i] - top[i]) * ratio) for i in range(3)),
        )


def render_face(size, icon, title, value, theme):
    """Draw one key face: gradient, hairline edge, icon, title, value."""
    from PIL import Image, ImageDraw, ImageFont

    top, bottom = THEMES.get(theme, THEMES["action"])
    image = Image.new("RGB", size)
    width, height = size
    _paint_gradient(image, top, bottom)
    draw = ImageDraw.Draw(image)

    # A hairline inset border gives each key a defined edge on a dark deck.
    draw.rounded_rectangle([1, 1, width - 2, height - 2], radius=9, outline=COLOUR_EDGE, width=1)

    try:
        icon_font = ImageFont.truetype(FONT_PLAIN, 22)
        title_font = ImageFont.truetype(FONT_BOLD, 11)
        value_font = ImageFont.truetype(FONT_BOLD, 17)
    except OSError:
        icon_font = title_font = value_font = ImageFont.load_default()

    dim = theme == "muted"
    draw.text((width / 2, height * 0.26), icon, font=icon_font, anchor="mm",
              fill=COLOUR_DIM if dim else COLOUR_TEXT)
    draw.text((width / 2, height * 0.55), title, font=title_font, anchor="mm", fill=COLOUR_DIM)
    draw.text((width / 2, height * 0.78), value, font=value_font, anchor="mm",
              fill=COLOUR_DIM if dim else COLOUR_TEXT)
    return image


def compute_faces(status):
    """Return {key: (icon, title, value, theme)} for one snapshot of Home Assistant.

    Faces are plain tuples so an unchanged key is skipped without re-rendering. Pulled out
    of the panel class so preview.py can render the same art with no device attached.
    """
    district = status.get("district") or "none"

    if not status.get("online"):
        # Never show state that may have moved on. A dark panel is honest.
        return {key: ("", "", "", "muted") for key in range(6)} | {
            KEY_FEED: ("○", "FEED", "no HA", "muted"),
        }

    if status.get("listening"):
        theme = "manual" if status.get("manual") else "live"
        listen = ("■", "LISTEN", "on", theme)
    else:
        listen = ("▶", "LISTEN", "off", "idle")

    volume = str(status.get("volume", 0))
    callouts = status.get("callouts", 0)

    if not status.get("feed_connected"):
        feed = ("○", "FEED", "down", "alert")
    elif status.get("feed_stale"):
        feed = ("●", "FEED", "stale", "manual")
    else:
        feed = ("●", "FEED", "ok", "live")

    return {
        KEY_LISTEN: listen,
        KEY_VOL_DOWN: ("▼", "VOL", volume, "action"),
        KEY_VOL_UP: ("▲", "VOL", volume, "action"),
        KEY_DISTRICT: ("◆", "DISTRICT", district, "action"),
        KEY_CALLOUTS: ("●", "TODAY", str(callouts), "alert" if callouts else "idle"),
        KEY_FEED: feed,
    }


def available():
    """Report whether the Stream Deck library and Pillow can both be imported."""
    try:
        import PIL  # noqa: F401
        from StreamDeck.DeviceManager import DeviceManager  # noqa: F401
    except ImportError as exc:
        LOG.warning("Stream Deck support unavailable: %s", exc)
        return False
    return True


class StreamDeckPanel:
    """Own the Stream Deck device: render key faces and turn presses into backend calls."""

    def __init__(self, backend, brightness=60, reconnect_seconds=10, volume_step=10):
        self.backend = backend
        self.brightness = brightness
        self.reconnect_seconds = reconnect_seconds
        self.volume_step = volume_step
        self.deck = None
        self.stopping = threading.Event()
        self.last_faces = {}
        self.thread = threading.Thread(target=self._run, name="streamdeck", daemon=True)

    def start(self):
        self.thread.start()

    def shutdown(self):
        self.stopping.set()
        self.thread.join(timeout=5)
        self._close_deck()

    # -- device lifecycle ------------------------------------------------------------

    def _run(self):
        while not self.stopping.is_set():
            if self.deck is None and not self._open_deck():
                self.stopping.wait(self.reconnect_seconds)
                continue
            try:
                self._render()
            except Exception as exc:
                LOG.warning("Stream Deck render failed, will reconnect: %s", exc)
                self._close_deck()
            self.stopping.wait(0.5)

    def _open_deck(self):
        from StreamDeck.DeviceManager import DeviceManager

        try:
            decks = DeviceManager().enumerate()
        except Exception as exc:
            LOG.warning("Could not enumerate Stream Decks: %s", exc)
            return False
        if not decks:
            LOG.info("No Stream Deck found, retrying in %ss", self.reconnect_seconds)
            return False
        deck = decks[0]
        try:
            deck.open()
            deck.reset()
            deck.set_brightness(self.brightness)
            deck.set_key_callback(self._on_key)
        except Exception as exc:
            LOG.warning("Could not open Stream Deck: %s", exc)
            return False
        self.deck = deck
        self.last_faces = {}
        LOG.info("Stream Deck connected: %s with %s keys", deck.deck_type(), deck.key_count())
        return True

    def _close_deck(self):
        if self.deck is None:
            return
        try:
            self.deck.reset()
            self.deck.close()
        except Exception as exc:
            LOG.debug("Error closing Stream Deck: %s", exc)
        self.deck = None
        self.last_faces = {}

    # -- input -----------------------------------------------------------------------

    def _on_key(self, deck, key, pressed):
        # Runs on the Stream Deck library's own thread. Every backend call hands the work
        # to another thread, so nothing here waits on the network.
        if not pressed:
            return
        LOG.debug("Stream Deck key %s pressed", key)
        if key == KEY_LISTEN:
            self.backend.toggle_listen()
        elif key == KEY_VOL_DOWN:
            self.backend.change_volume(-self.volume_step)
        elif key == KEY_VOL_UP:
            self.backend.change_volume(self.volume_step)
        elif key == KEY_DISTRICT:
            self.backend.next_district()

    # -- output ----------------------------------------------------------------------

    def _render(self):
        faces = compute_faces(self.backend.status())
        for key, face in faces.items():
            if key >= self.deck.key_count():
                continue
            if self.last_faces.get(key) == face:
                continue
            image = self._build_image(*face)
            with self.deck:
                self.deck.set_key_image(key, image)
            self.last_faces[key] = face

    def _build_image(self, icon, title, value, theme):
        from StreamDeck.ImageHelpers import PILHelper

        size = PILHelper.create_image(self.deck).size
        return PILHelper.to_native_format(self.deck, render_face(size, icon, title, value, theme))
