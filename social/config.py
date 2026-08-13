"""Brand tokens, platform limits, paths, hashtags — one source of truth.

The design tokens below are the site's own dark-theme values (static/style.css
`@media (prefers-color-scheme: dark)`), not a parallel palette. They are
written into `:root` of every template by `_base.html`, so changing a colour
here changes every graphic and nothing else has to be touched.

Why the dark theme and not the light one the site shows by default: these
graphics are read as thumbnails in a WhatsApp list and re-shared with no
caption. A dark board holds its edges against a feed background, and the
watermark stays legible after Instagram's compression.
"""

import os

# ── Paths ────────────────────────────────────────────────────────────────────

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SOCIAL = os.path.join(ROOT, "social")
TEMPLATES = os.path.join(SOCIAL, "templates")
FONTS = os.path.join(SOCIAL, "assets", "fonts")
LOGO_DIR = os.path.join(SOCIAL, "assets", "logo")
FLEX_DIR = os.path.join(SOCIAL, "flex")
# Crests are the site's own club logos — not a second copy. Keyed by club_id
# with a legacy_code fallback, exactly as src/render.py resolves them.
CRESTS = os.path.join(ROOT, "static", "logos", "clubs")
COMP_LOGOS = os.path.join(ROOT, "static", "logos", "competitions")
OUT = os.path.join(ROOT, "out", "social")


# ── Project constants ────────────────────────────────────────────────────────

SITE_URL = "https://everyleague.co"
WATERMARK = "everyleague.co"

# All match times in the sheet are already Malawi time; nothing is converted,
# only labelled. Same convention as build.py.
TZ_OFFSET_HOURS = 2
TZ_LABEL = "CAT"

IMAGE_SIZE = 1080
DEVICE_SCALE = 2


# ── Design tokens ────────────────────────────────────────────────────────────

# Written verbatim into :root as CSS custom properties. Keys become
# `--<key with underscores as hyphens>`.
TOKENS = {
    # Surfaces — the site's dark theme.
    "ground": "#15171a",       # board base
    "panel": "#1b1e22",        # row surface
    "row_alt": "#23272d",      # alternating row
    "line": "#2a2e34",         # hairlines
    # Ink.
    "ink": "#e9eaec",          # scorelines, team names
    "muted": "#9aa1ab",        # minutes, venues, metadata
    # Accent — the site green, both themes' values in play.
    "accent": "#3fb37a",       # rules, marks (the dark-theme green)
    "accent_deep": "#0b6b3a",  # eyebrow bar fill (the light-theme green)
    # Crest monogram fallback. The teams tab has no colour columns, so a
    # missing crest gets brand neutrals rather than an invented club colour.
    "monogram_bg": "#23272d",
    "monogram_ink": "#9aa1ab",
}

FONT_STACK = "'Inter', system-ui, -apple-system, 'Segoe UI', Roboto, sans-serif"

# Vendored so rendering never touches the network. Inter ships as one variable
# font per subset, so both weights below come from the same file.
FONT_FILES = (
    ("inter-latin.woff2", "U+0000-00FF, U+0131, U+0152-0153, U+02BB-02BC, "
                          "U+02C6, U+02DA, U+02DC, U+0304, U+0308, U+0329, "
                          "U+2000-206F, U+20AC, U+2122, U+2191, U+2193, "
                          "U+2212, U+2215, U+FEFF, U+FFFD"),
    ("inter-latin-ext.woff2", "U+0100-02BA, U+02BD-02C5, U+02C7-02CC, "
                              "U+02CE-02D7, U+02DD-02FF, U+0304, U+0308, "
                              "U+0329, U+1D00-1DBF, U+1E00-1E9F, U+1EF2-1EFF, "
                              "U+2020, U+20A0-20AB, U+20AD-20C0, U+2113, "
                              "U+2C60-2C7F, U+A720-A7FF"),
)

# The face that must be resolved at screenshot time. render.py asserts this
# is what actually painted — a silent fallback is the classic way one of
# these pipelines ships ugly output.
FONT_FAMILY = "Inter"


# ── Platform limits ──────────────────────────────────────────────────────────

PLATFORMS = ("whatsapp", "facebook", "x", "instagram")

PLATFORM_LABELS = {
    "whatsapp": "WhatsApp",
    "facebook": "Facebook",
    "x": "X",
    "instagram": "Instagram",
}

# X counts every link as 23 characters regardless of its real length (t.co).
X_LIMIT = 280
X_URL_WEIGHT = 23

# Not a hard cap — the point at which Facebook stops rewarding length.
FACEBOOK_TARGET = (400, 600)

INSTAGRAM_LIMIT = 2200
# Instagram truncates behind "more" at roughly this point, so the first line
# has to carry the post on its own.
INSTAGRAM_FOLD = 125

# Instagram allows no clickable link in a caption.
INSTAGRAM_BIO_POINTER = "Full tables → link in bio"


# ── Voice ────────────────────────────────────────────────────────────────────

# The house rules, in one place because they are the product's credibility.
# Where a rule is mechanically checkable, tests/social/test_captions.py
# checks it; the rest are here to be read before writing new copy.
VOICE_RULES = (
    "Always name the scorer and the minute when the data has them.",
    "No exclamation points in scorelines.",
    "Never editorialize a result — report it.",
    "No predictions or hype: fixtures state who, when and where.",
    "No invented names: teams use display_name, competitions their "
    "sponsor/display name.",
    "Never imply the scorer list is complete when it is not.",
    "Absolute times only ('15:00 CAT'), never 'today' or 'tomorrow'.",
)

# Used positionally, never sprinkled. Anything outside this set is a bug.
EMOJI = {
    "ball": "⚽",      # a result line
    "yellow": "\U0001f7e8",
    "red": "\U0001f7e5",
    "chart": "\U0001f4ca",  # a table or standings line
}


# ── Hashtags ─────────────────────────────────────────────────────────────────

# Never inline a hashtag in a post type — add it here.
HASHTAGS_BASE = ("#MalawiFootball", "#EverLeague")

HASHTAGS_BY_COMPETITION = {
    "MW_SL": ("#SuperLeague", "#FDHBankPremiership"),
    "MW_NDL": ("#NationalDivisionLeague",),
    "MW_WP": ("#WomensPremiership",),
    "MW_TOP8": ("#Top8",),
    "MW_KU19": ("#U19",),
    "MW_U16": ("#U16",),
    "MW_SRFA": ("#SRFA",), "MW_SRFA2": ("#SRFA",),
    "MW_CRFA": ("#CRFA",), "MW_CRFA2": ("#CRFA",),
    "MW_NRFA": ("#NRFA",),
}

# Per platform, because the norms differ: Instagram tolerates a block of them,
# X spends characters it does not have, WhatsApp treats them as noise.
HASHTAG_LIMIT = {
    "whatsapp": 0,
    "facebook": 3,
    "x": 2,
    "instagram": 8,
}


def hashtags(platform: str, competition_ids=()) -> "tuple[str, ...]":
    """The hashtag set for one platform, most specific first, deduped."""
    limit = HASHTAG_LIMIT.get(platform, 0)
    if not limit:
        return ()
    tags: "list[str]" = []
    for cid in competition_ids:
        for tag in HASHTAGS_BY_COMPETITION.get(cid, ()):
            if tag not in tags:
                tags.append(tag)
    for tag in HASHTAGS_BASE:
        if tag not in tags:
            tags.append(tag)
    return tuple(tags[:limit])


# ── Links ────────────────────────────────────────────────────────────────────

def tagged_link(platform: str, campaign: str, path: str = "/") -> str:
    """Every outbound link in this module is built here.

    Deep-link where the site has a page for it: a results post points at that
    matchday's /matches/<date>.html, a scorers post at /<slug>/goalscorers.html.
    """
    path = "/" + path.lstrip("/")
    return (f"{SITE_URL}{path}"
            f"?utm_source={platform}&utm_medium=social&utm_campaign={campaign}")
