"""Country flags for the national-team pages.

`nt_matches.opponent` is a display NAME, not a code (see `nt.py`), so the only
way to a flag is a name lookup. That lookup lives here, alongside the files it
resolves against: `static/flags/<code>.png`, small public-domain flag images
(80px wide, a few hundred bytes each) committed to the repo so a page never
depends on an outside host.

Two rules keep this from ever breaking a build:

  * **An unknown name renders no flag**, exactly as a missing club crest does.
    A new opponent shows its name alone until someone adds a row below.
  * **A known name whose file is missing renders no flag either** — `Flags`
    checks the static tree once at build time, so deleting a PNG degrades
    instead of shipping a broken `<img>`.

Codes are ISO 3166-1 alpha-2, lowercased, plus the `gb-eng`-style subdivisions
the flag set uses for the Home Nations.
"""

import os
import re
import unicodedata

# Where the PNGs live inside static/, and their intrinsic width.
DIR = "flags"
WIDTH = 80

# name -> code. Keys are matched after _normalize(), so accents, punctuation
# and case do not matter here; they are written in their natural form for
# readability. Alternative names (Ivory Coast / Côte d'Ivoire, Swaziland /
# Eswatini) each get their own entry — the sheet may use either.
_NAMES = {
    # ── CAF ──────────────────────────────────────────────────────────────
    "Algeria": "dz",
    "Angola": "ao",
    "Benin": "bj",
    "Botswana": "bw",
    "Burkina Faso": "bf",
    "Burundi": "bi",
    "Cabo Verde": "cv",
    "Cape Verde": "cv",
    "Cameroon": "cm",
    "Central African Republic": "cf",
    "Chad": "td",
    "Comoros": "km",
    "Congo": "cg",
    "Congo-Brazzaville": "cg",
    "Republic of the Congo": "cg",
    "DR Congo": "cd",
    "DR Congo (Kinshasa)": "cd",
    "Democratic Republic of the Congo": "cd",
    "Congo DR": "cd",
    "Cote d'Ivoire": "ci",
    "Ivory Coast": "ci",
    "Djibouti": "dj",
    "Egypt": "eg",
    "Equatorial Guinea": "gq",
    "Eritrea": "er",
    "Eswatini": "sz",
    "Swaziland": "sz",
    "Ethiopia": "et",
    "Gabon": "ga",
    "Gambia": "gm",
    "The Gambia": "gm",
    "Ghana": "gh",
    "Guinea": "gn",
    "Guinea-Bissau": "gw",
    "Kenya": "ke",
    "Lesotho": "ls",
    "Liberia": "lr",
    "Libya": "ly",
    "Madagascar": "mg",
    "Malawi": "mw",
    "Mali": "ml",
    "Mauritania": "mr",
    "Mauritius": "mu",
    "Morocco": "ma",
    "Mozambique": "mz",
    "Namibia": "na",
    "Niger": "ne",
    "Nigeria": "ng",
    "Rwanda": "rw",
    "Sao Tome and Principe": "st",
    "Senegal": "sn",
    "Seychelles": "sc",
    "Sierra Leone": "sl",
    "Somalia": "so",
    "South Africa": "za",
    "South Sudan": "ss",
    "Sudan": "sd",
    "Tanzania": "tz",
    "Togo": "tg",
    "Tunisia": "tn",
    "Uganda": "ug",
    "Zambia": "zm",
    "Zanzibar": "tz",
    "Zimbabwe": "zw",
    # ── Everyone else the Scorchers have met or plausibly will ───────────
    "Argentina": "ar",
    "Australia": "au",
    "Austria": "at",
    "Bangladesh": "bd",
    "Belgium": "be",
    "Bolivia": "bo",
    "Brazil": "br",
    "Bulgaria": "bg",
    "Canada": "ca",
    "Chile": "cl",
    "China": "cn",
    "China PR": "cn",
    "Chinese Taipei": "tw",
    "Colombia": "co",
    "Costa Rica": "cr",
    "Croatia": "hr",
    "Cuba": "cu",
    "Czechia": "cz",
    "Czech Republic": "cz",
    "Denmark": "dk",
    "Ecuador": "ec",
    "England": "gb-eng",
    "Fiji": "fj",
    "Finland": "fi",
    "France": "fr",
    "Germany": "de",
    "Greece": "gr",
    "Haiti": "ht",
    "Hungary": "hu",
    "Iceland": "is",
    "India": "in",
    "Indonesia": "id",
    "Iran": "ir",
    "Iraq": "iq",
    "Ireland": "ie",
    "Republic of Ireland": "ie",
    "Israel": "il",
    "Italy": "it",
    "Jamaica": "jm",
    "Japan": "jp",
    "Jordan": "jo",
    "Kazakhstan": "kz",
    "Kuwait": "kw",
    "Lebanon": "lb",
    "Malaysia": "my",
    "Mexico": "mx",
    "Nepal": "np",
    "Netherlands": "nl",
    "New Zealand": "nz",
    "North Korea": "kp",
    "Korea DPR": "kp",
    "Northern Ireland": "gb-nir",
    "Norway": "no",
    "Oman": "om",
    "Pakistan": "pk",
    "Panama": "pa",
    "Papua New Guinea": "pg",
    "Paraguay": "py",
    "Peru": "pe",
    "Philippines": "ph",
    "Poland": "pl",
    "Portugal": "pt",
    "Qatar": "qa",
    "Romania": "ro",
    "Russia": "ru",
    "Saudi Arabia": "sa",
    "Scotland": "gb-sct",
    "Serbia": "rs",
    "Singapore": "sg",
    "Slovakia": "sk",
    "Slovenia": "si",
    "South Korea": "kr",
    "Korea Republic": "kr",
    "Spain": "es",
    "Sri Lanka": "lk",
    "Sweden": "se",
    "Switzerland": "ch",
    "Syria": "sy",
    "Thailand": "th",
    "Trinidad and Tobago": "tt",
    "Turkey": "tr",
    "Ukraine": "ua",
    "United Arab Emirates": "ae",
    "UAE": "ae",
    "United States": "us",
    "USA": "us",
    "Uruguay": "uy",
    "Uzbekistan": "uz",
    "Venezuela": "ve",
    "Vietnam": "vn",
    "Wales": "gb-wls",
}

_STRIP = re.compile(r"[^a-z0-9 ]+")


def _normalize(name: str) -> str:
    """Fold a country name to its lookup key: no accents, no punctuation.

    "Côte d'Ivoire", "Cote d Ivoire" and "COTE D'IVOIRE" all land on the same
    key, so the sheet can spell a name however it likes.
    """
    folded = unicodedata.normalize("NFKD", name or "")
    folded = "".join(c for c in folded if not unicodedata.combining(c))
    folded = _STRIP.sub(" ", folded.lower())
    return " ".join(folded.split())


_BY_KEY = {_normalize(name): code for name, code in _NAMES.items()}

# Every code the map can produce — what the download script fetches.
CODES = sorted(set(_NAMES.values()))


def code_for(name: str) -> str:
    """The flag code for a country name, or "" when the name is unknown.

    A trailing qualifier is dropped before the second lookup, so team-style
    names ("Nigeria U20", "South Africa (Women)") still find their country.
    """
    key = _normalize(name)
    if not key:
        return ""
    if key in _BY_KEY:
        return _BY_KEY[key]
    words = key.split()
    while len(words) > 1:
        words.pop()
        candidate = " ".join(words)
        if candidate in _BY_KEY:
            return _BY_KEY[candidate]
    return ""


class Flags:
    """Name -> flag URL, resolved against one static tree and one page depth.

    Built once per build: listing the directory on every team name in every
    results table would be hundreds of stat calls for an answer that cannot
    change mid-build.
    """

    def __init__(self, static_dir: str, prefix: str = ""):
        self.prefix = prefix
        root = os.path.join(static_dir, DIR)
        try:
            self.available = frozenset(
                f[:-4] for f in os.listdir(root) if f.endswith(".png")
            )
        except OSError:
            # No flags directory (a stripped checkout, a test fixture): every
            # lookup misses and the pages render names only.
            self.available = frozenset()

    def url_for(self, name: str) -> str:
        """"../flags/ng.png", or "" when there is no flag to show."""
        code = code_for(name)
        if not code or code not in self.available:
            return ""
        return f"{self.prefix}{DIR}/{code}.png"

    def img_for(self, name: str, cls: str = "nt-flag") -> str:
        """The <img> to sit beside a team name, or "" — never a broken image.

        `alt` is empty on purpose: the country name is right next to it in the
        same cell, so a screen reader announcing the flag would just stutter.
        """
        url = self.url_for(name)
        if not url:
            return ""
        return (f'<img class="{cls}" src="{url}" alt="" '
                f'loading="lazy" decoding="async">')
