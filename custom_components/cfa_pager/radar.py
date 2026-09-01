#!/usr/bin/env python3
"""Build an animated radar loop from the Bureau of Meteorology's public anonymous FTP.

BOM serves each radar scan as a transparent PNG plus a set of static background layers.
The website assembles them in the browser; this does the same server side and emits one
animated GIF, so the dashboard needs a single <img> and the Pi makes one set of requests
per poll however many browsers are open.

The anonymous FTP is the Bureau's documented public access path, which is why it is used
here in preference to scraping the website's own image URLs. Attribution is required for
Bureau material and is rendered on the dashboard.

Blocking: ftplib and Pillow both block, so every call belongs in an executor.

Layer order matters: background, topography, then the scan, then labels and range rings
on top so they stay readable through the rain.
"""

import io
import logging
import re
from ftplib import FTP, error_perm

_LOGGER = logging.getLogger(__name__)

FRAME_PATTERN = re.compile(r"^(IDR\w+)\.T\.(\d{12})\.png$")

# Drawn under the scan, then over it.
UNDER_LAYERS = ("background", "topography")
OVER_LAYERS = ("locations", "range")


class RadarBuilder:
    """Fetch frames and layers over FTP and composite them into an animated GIF."""

    def __init__(self, product, host="ftp.bom.gov.au", frames=6, frame_ms=400,
                 last_frame_ms=1400, timeout=30):
        self.product = product
        self.host = host
        self.frames = frames
        self.frame_ms = frame_ms
        self.last_frame_ms = last_frame_ms
        self.timeout = timeout
        # Static layers change rarely, so they are fetched once and kept.
        self.layer_cache = {}

    def _connect(self):
        ftp = FTP(self.host, timeout=self.timeout)
        ftp.login()  # anonymous
        return ftp

    def _download(self, ftp, path):
        buffer = io.BytesIO()
        ftp.retrbinary(f"RETR {path}", buffer.write)
        return buffer.getvalue()

    def _frame_names(self, ftp):
        """Newest frame filenames for this product, oldest first."""
        ftp.cwd("/anon/gen/radar")
        try:
            # Most FTP servers apply the glob server side, which avoids pulling a listing
            # of every radar in the country on each poll.
            names = ftp.nlst(f"{self.product}.T.*.png")
        except error_perm:
            names = ftp.nlst()
        matched = []
        for name in names:
            base = name.rsplit("/", 1)[-1]
            match = FRAME_PATTERN.match(base)
            if match and match.group(1) == self.product:
                matched.append(base)
        matched.sort()
        return matched[-self.frames:]

    def _layers(self, ftp):
        wanted = UNDER_LAYERS + OVER_LAYERS
        missing = [name for name in wanted if name not in self.layer_cache]
        if missing:
            ftp.cwd("/anon/gen/radar_transparencies")
            for name in missing:
                try:
                    self.layer_cache[name] = self._download(
                        ftp, f"{self.product}.{name}.png")
                except error_perm as exc:
                    _LOGGER.debug("Radar layer %s unavailable: %s", name, exc)
                    self.layer_cache[name] = None
        return self.layer_cache

    def build(self):
        """Return (gif_bytes, frame_count, newest_timestamp). Raises on failure."""
        from PIL import Image

        ftp = self._connect()
        try:
            names = self._frame_names(ftp)
            if not names:
                raise ValueError(f"no frames found for {self.product}")
            layers = self._layers(ftp)
            ftp.cwd("/anon/gen/radar")
            scans = [(name, self._download(ftp, name)) for name in names]
        finally:
            try:
                ftp.quit()
            except OSError:
                ftp.close()

        def open_layer(raw):
            return Image.open(io.BytesIO(raw)).convert("RGBA") if raw else None

        under = [open_layer(layers.get(name)) for name in UNDER_LAYERS]
        over = [open_layer(layers.get(name)) for name in OVER_LAYERS]
        base_size = next((layer.size for layer in under + over if layer), None)

        composed = []
        for name, raw in scans:
            scan = Image.open(io.BytesIO(raw)).convert("RGBA")
            size = base_size or scan.size
            canvas = Image.new("RGBA", size, (255, 255, 255, 255))
            for layer in under:
                if layer:
                    canvas = Image.alpha_composite(canvas, layer)
            canvas = Image.alpha_composite(canvas, scan if scan.size == size
                                           else scan.resize(size))
            for layer in over:
                if layer:
                    canvas = Image.alpha_composite(canvas, layer)
            composed.append(canvas.convert("P", palette=Image.ADAPTIVE, colors=128))

        durations = [self.frame_ms] * len(composed)
        durations[-1] = self.last_frame_ms   # hold on the latest scan

        out = io.BytesIO()
        composed[0].save(out, format="GIF", save_all=True, append_images=composed[1:],
                         duration=durations, loop=0, optimize=True, disposal=2)
        newest = FRAME_PATTERN.match(scans[-1][0]).group(2)
        return out.getvalue(), len(composed), newest
