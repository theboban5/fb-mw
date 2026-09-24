"""A link-preview card per recent match — /og/match/{match_id}-{hash}.png.

WHAT WAS WRONG. A match page (match_page.py) exists so a result can be sent
to someone, and what they were sent was the site's own logo card: WhatsApp
and Facebook draw og:image and nothing else, so "Bullets 2–1 Wanderers"
arrived as a picture that said "Every league. Every level." The <title> named
the match in small print above it. The scoreline — the whole reason the link
was sent — was never in the part people look at.

WHY IT WAS DECLINED, AND WHAT CHANGED. A card for every match was costed at
~40 MB of PNGs a build, drawn again on every deploy. Measured, it is ~26 kB a
card — ~26 MB for ~1,000, beside the 19 MB of match pages — and the drawing is
the only real cost. So CI keeps /og/match/ between runs (actions/cache in
deploy.yml) and a build draws only the cards whose hash is new: the matches
that changed since the last deploy, usually a handful. A first build with no
cache draws them all (~30 s) and nothing else is different.

A first cut drew only matches near today, and a result shared a month later
previewed as the logo again. Every match with a page gets a card now.

THE FILENAME CARRIES A HASH of everything the card shows. Chat clients cache
a preview by image URL, so a fixture card shared on Friday and re-scraped on
Sunday must not come back as "v 15:00" — a changed score is a new URL.

Pillow is optional everywhere in this build and optional here: without it,
or without the font, no cards are written and every page keeps the site card.

The font is Inter (assets/fonts/Inter.ttf, OFL), the same face the portal
ships as woff2 — converted once to TTF because a woff2 needs a FreeType built
with brotli, and whether CI's Pillow wheel has one is not a thing a build
should find out on deploy day. It lives outside static/ so it is never
shipped to readers.
"""

from functools import lru_cache
import hashlib
import json
import os

from . import adapt, matches_page, render

SLUG = "og/match"
W, H = 1200, 630

# Part of every filename's hash. Bump it when the drawing changes: the facts
# alone would hand a redesigned card the old URL, and chat clients would keep
# showing the old picture under it.
DESIGN = 1

# The site's dark palette, as scripts/make_og_image.py uses for the site card:
# a shared result should look like it came from the same place.
BG = (21, 23, 26)
PANEL = (30, 33, 37)
INK = (233, 234, 236)
MUTED = (154, 161, 171)
ACCENT = (63, 179, 122)

FONT_FILE = os.path.join(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__))), "assets", "fonts", "Inter.ttf")


def _is_result(m) -> bool:
    # match_page._is_result, restated rather than imported: match_page is
    # handed this module's output, and the arrow should point one way.
    return m.counts_for_table and m.has_score


def facts(ds, m) -> dict:
    """Every string the card draws. Hashed for the filename, so anything the
    card shows must come from here and nowhere else."""
    home, away = ds.teams.get(m.home_team_id), ds.teams.get(m.away_team_id)
    comp = ds.competitions.get(m.competition_id)
    f = {
        "home": home.display_name if home else m.home_team_id,
        "away": away.display_name if away else m.away_team_id,
        "competition": ds.league_display_name(m.competition_id, m.season_id),
        "round": matches_page._round_label(comp, m),
        "date": matches_page.short_date_label(m.date) if m.date else "",
    }
    if _is_result(m):
        f["middle"] = f"{m.home_goals}–{m.away_goals}"
        bits = []
        if m.home_pens is not None and m.away_pens is not None:
            bits.append(f"{m.home_pens}–{m.away_pens} on pens")
        if m.extra_time:
            bits.append("After extra time")
        if m.status == "awarded":
            bits.insert(0, "Awarded")
        elif not bits:
            bits.append("Full time")
        f["status"] = " · ".join(bits)
        f["kind"] = "result"
    elif m.status in ("postponed", "cancelled", "abandoned"):
        f["middle"] = "vs"
        f["status"] = m.status.capitalize()
        f["kind"] = "off"
    else:
        f["middle"] = adapt.clock(m.kickoff) or "vs"
        f["status"] = ""
        f["kind"] = "fixture"
    return f


def alt_text(f) -> str:
    """"Bullets 2–1 Wanderers, Super League" — read aloud, so no byline."""
    if f["kind"] == "result":
        line = f'{f["home"]} {f["middle"]} {f["away"]}'
    else:
        line = f'{f["home"]} v {f["away"]}'
        if f["kind"] == "off":
            line += f', {f["status"].lower()}'
        elif f["date"]:
            line += f', {f["date"]}'
    return f'{line}, {f["competition"]}'


# ── Drawing ──────────────────────────────────────────────────────────────────

@lru_cache(maxsize=None)
def _font(size, weight="Bold"):
    from PIL import ImageFont
    font = ImageFont.truetype(FONT_FILE, size)
    font.set_variation_by_name(weight)
    return font


def _fit(draw, text, weight, size, min_size, max_w):
    """The largest font from size down to min_size that fits max_w, and the
    text — ellipsised at min_size if even that does not fit."""
    for s in range(size, min_size - 1, -2):
        font = _font(s, weight)
        if draw.textlength(text, font=font) <= max_w:
            return font, text
    font = _font(min_size, weight)
    while text and draw.textlength(text + "…", font=font) > max_w:
        text = text[:-1]
    return font, text.rstrip() + "…"


def _wrap_name(draw, name, max_w):
    """A team name in one line if it fits large, else two, else shrunk.

    "Mighty Mukuru Wanderers" at 46px is wider than a column; split at the
    space nearest the middle it is two readable lines, which beats one line
    at a size nobody can read on a phone's preview.
    """
    big = _font(46, "Bold")
    if draw.textlength(name, font=big) <= max_w:
        return big, [name]
    words = name.split()
    if len(words) > 1:
        best = None
        for i in range(1, len(words)):
            a, b = " ".join(words[:i]), " ".join(words[i:])
            wider = max(draw.textlength(a, font=big), draw.textlength(b, font=big))
            if best is None or wider < best[0]:
                best = (wider, [a, b])
        if best[0] <= max_w:
            return big, best[1]
        lines = best[1]
        wide = max(lines, key=lambda ln: draw.textlength(ln, font=big))
        font, _ = _fit(draw, wide, "Bold", 44, 30, max_w)
        return font, [_fit(draw, ln, "Bold", font.size, font.size, max_w)[1]
                      for ln in lines]
    font, text = _fit(draw, name, "Bold", 44, 30, max_w)
    return font, [text]


def _centred(draw, cx, y, text, font, fill):
    draw.text((cx - draw.textlength(text, font=font) / 2, y), text,
              font=font, fill=fill)


def _crest(Image, path, box):
    """The crest scaled to fit a box×box square, or None if it cannot be read."""
    try:
        with Image.open(path) as im:
            im = im.convert("RGBA")
            im.thumbnail((box, box), Image.LANCZOS)
            return im
    except OSError:
        return None


def draw(f, home_crest=None, away_crest=None):
    """The card as a PIL image. Crests are file paths (PNG) or None."""
    from PIL import Image, ImageDraw

    img = Image.new("RGB", (W, H), BG)
    draw = ImageDraw.Draw(img)

    # A band across the top carries the competition — the first thing a
    # reader needs, because the same two clubs meet in a league and a cup.
    draw.rectangle((0, 0, W, 8), fill=ACCENT)
    top = f["competition"].upper()
    if f["round"]:
        top += f'  ·  {f["round"].upper()}'
    font, top = _fit(draw, top, "SemiBold", 30, 22, W - 120)
    _centred(draw, W / 2, 52, top, font, MUTED)

    col_w, crest_box = 360, 190
    for cx, name, crest in ((210, f["home"], home_crest),
                            (W - 210, f["away"], away_crest)):
        im = _crest(Image, crest, crest_box) if crest else None
        font, lines = _wrap_name(draw, name, col_w)
        line_h = font.size + 8
        if im is not None:
            img.paste(im, (int(cx - im.width / 2),
                           int(150 + (crest_box - im.height) / 2)), im)
            y = 370
        else:
            # No crest: the name takes the crest's place rather than sitting
            # under an empty square — nothing drawn for a fact we lack.
            y = 250 - (line_h * len(lines)) / 2
        for ln in lines:
            _centred(draw, cx, y, ln, font, INK)
            y += line_h

    mid = f["middle"]
    if f["kind"] == "result":
        # Spaced, because Inter sets "1–2" with the 1 touching the dash; and
        # fitted, because "10 – 10" at full size runs into the team columns.
        font, mid = _fit(draw, mid.replace("–", " – "), "Bold", 150, 90, 420)
        _centred(draw, W / 2, 150 + (150 - font.size) / 2, mid, font, INK)
    elif mid == "vs":
        _centred(draw, W / 2, 200, mid, _font(80, "SemiBold"), MUTED)
    else:
        _centred(draw, W / 2, 180, mid, _font(96, "Bold"), INK)

    sub = f["status"] if f["kind"] != "fixture" else f["date"]
    if sub:
        colour = ACCENT if f["kind"] == "result" else INK
        font, sub = _fit(draw, sub, "SemiBold", 34, 24, 380)
        _centred(draw, W / 2, 360, sub, font, colour)
    if f["kind"] == "result" and f["date"]:
        _centred(draw, W / 2, 410, f["date"], _font(28, "Medium"), MUTED)

    draw.rectangle((0, H - 96, W, H), fill=PANEL)
    _centred(draw, W / 2, H - 66, "everyleague.co", _font(32, "SemiBold"), ACCENT)
    return img


# ── Writing ──────────────────────────────────────────────────────────────────

def _crest_path(static_dir):
    """f(team) -> a crest PNG's path on disk, or None (SVG is not drawable)."""
    find = matches_page._crest_finder(static_dir, "")

    def path(team):
        rel = find(team)
        if not rel or not rel.lower().endswith(".png"):
            return None
        return os.path.join(static_dir, rel)

    return path


@lru_cache(maxsize=None)
def _file_digest(path):
    if not path:
        return ""
    with open(path, "rb") as fh:
        return hashlib.sha1(fh.read()).hexdigest()


def filename(match_id, f, home_crest, away_crest) -> str:
    key = json.dumps([DESIGN, f, _file_digest(home_crest),
                      _file_digest(away_crest)],
                     sort_keys=True, ensure_ascii=False)
    return f"{match_id}-{hashlib.sha1(key.encode()).hexdigest()[:8]}.png"


def build_cards(dist, static_dir, ds, page_ids) -> "dict[str, tuple[str, str]]":
    """Write a card for every match with a page; return match_id -> (absolute
    URL, alt text), which is what match_page hands to social_meta.

    An empty dict is a working answer: every page then keeps the site card.
    A card already on disk under its hashed name is not drawn again, which is
    what makes the CI cache worth having; any other PNG in the directory is a
    card for a score that no longer stands, and is deleted — otherwise the
    cache, and the Pages upload with it, would grow by every edit forever.
    """
    try:
        from PIL import Image
        MEDIANCUT = Image.Quantize.MEDIANCUT
        _font(40)
    except ImportError:
        print("WARNING: Pillow not installed; match pages keep the site card.")
        return {}
    except OSError:
        print(f"WARNING: {FONT_FILE} missing; match pages keep the site card.")
        return {}

    out_dir = os.path.join(dist, SLUG)
    os.makedirs(out_dir, exist_ok=True)
    crest_path = _crest_path(static_dir)
    cards = {}
    drawn = 0
    for mid in sorted(page_ids):
        m = ds.matches[mid]
        f = facts(ds, m)
        hc = crest_path(ds.teams.get(m.home_team_id))
        ac = crest_path(ds.teams.get(m.away_team_id))
        name = filename(mid, f, hc, ac)
        dst = os.path.join(out_dir, name)
        fresh = not os.path.exists(dst)
        if fresh:
            # A 256-colour palette: a third of the bytes of full colour, and
            # no visible loss — the card is flat colour, text and two crests.
            img = draw(f, hc, ac).quantize(256, method=MEDIANCUT)
            img.save(dst, "PNG", optimize=True)
        cards[mid] = (f"{render.SITE_URL}/{SLUG}/{name}", alt_text(f))
        drawn += 1 if fresh else 0
    keep = {url.rsplit("/", 1)[1] for url, _ in cards.values()}
    for old in os.listdir(out_dir):
        if old.endswith(".png") and old not in keep:
            os.remove(os.path.join(out_dir, old))
    print(f"og cards: {len(cards)} ({drawn} drawn, {len(cards) - drawn} reused)")
    return cards
