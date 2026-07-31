#!/usr/bin/env python3
"""Render static/og-image.png — the 1200x630 card link previews show.

Run after changing the wordmark or the strapline:

    python scripts/make_og_image.py

Sources static/everyleague_logo.png, so it needs nothing outside the repo. The
mark is only ever scaled down, never up, so it stays crisp.
"""

import os
import sys

from PIL import Image, ImageDraw, ImageFilter, ImageFont

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
STATIC = os.path.join(ROOT, "static")

W, H = 1200, 630

# The site's dark palette (style.css :root, prefers-color-scheme: dark).
BG = (21, 23, 26)
INK = (233, 234, 236)
MUTED = (154, 161, 171)
ACCENT = (63, 179, 122)

FONT_FILE = "/System/Library/Fonts/HelveticaNeue.ttc"
BOLD, MEDIUM = 1, 10


def _font(size, index=BOLD):
    try:
        return ImageFont.truetype(FONT_FILE, size, index=index)
    except OSError:
        print(f"ERROR: {FONT_FILE} not found — this script needs macOS system "
              "fonts. Render the card elsewhere and drop it in static/.",
              file=sys.stderr)
        raise SystemExit(1)


def _width(draw, text, font, tracking=0):
    w = draw.textlength(text, font=font)
    return w + tracking * max(0, len(text) - 1)


def _tracked(draw, xy, text, font, fill, tracking):
    """Letter-spaced text — Pillow has no tracking, so step glyph by glyph."""
    x, y = xy
    for ch in text:
        draw.text((x, y), ch, font=font, fill=fill)
        x += draw.textlength(ch, font=font) + tracking


def build():
    img = Image.new("RGB", (W, H), BG)

    # A soft green wash bleeding in from the bottom-left, echoing the featured
    # card on the landing page. Painted oversized then blurred so no edge shows.
    glow = Image.new("RGB", (W, H), BG)
    ImageDraw.Draw(glow).ellipse((-460, 250, 700, 1000), fill=(24, 74, 47))
    img = Image.blend(img, glow.filter(ImageFilter.GaussianBlur(150)), 0.85)

    draw = ImageDraw.Draw(img)

    mark = Image.open(os.path.join(STATIC, "everyleague_logo.png")).convert("RGBA")
    mark_h = 92
    mark = mark.resize((round(mark_h * mark.width / mark.height), mark_h),
                       Image.LANCZOS)

    wordmark_font = _font(62)
    tracking = 5
    word = "EVERYLEAGUE"
    gap = 26
    row_w = mark.width + gap + _width(draw, word, wordmark_font, tracking)
    row_x = int((W - row_w) // 2)
    row_mid = 208

    img.paste(mark, (row_x, row_mid - mark.height // 2), mark)
    _tracked(draw, (row_x + mark.width + gap, row_mid - 40),
             word, wordmark_font, INK, tracking)

    title_font = _font(78)
    title = "Every league. Every level."
    draw.text(((W - _width(draw, title, title_font)) // 2, 320),
              title, font=title_font, fill=INK)

    sub_font = _font(34, index=MEDIUM)
    sub = "Fixtures, results and tables across Malawian football."
    draw.text(((W - _width(draw, sub, sub_font)) // 2, 428),
              sub, font=sub_font, fill=MUTED)

    url_font = _font(28)
    url = "everyleague.co"
    _tracked(draw, ((W - _width(draw, url, url_font, 2)) // 2, 516),
             url, url_font, ACCENT, 2)

    out = os.path.join(STATIC, "og-image.png")
    img.save(out, "PNG", optimize=True)
    print(f"wrote {out} ({W}x{H}, {os.path.getsize(out) // 1024} KB)")


if __name__ == "__main__":
    build()
