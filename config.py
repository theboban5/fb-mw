"""Site + data configuration. This is the only file you edit for content.

To go live, replace the placeholder strings below with your Google Sheet
"Publish to web" CSV URLs (File -> Share -> Publish to web -> pick the tab ->
CSV). They look like:
  https://docs.google.com/spreadsheets/d/e/XXXX/pub?gid=0&single=true&output=csv

For local testing you can instead point these at local .csv files, or override
them without editing this file via the env vars listed below.
"""

import os


def _load_dotenv():
    """Load KEY=VALUE lines from a local .env (gitignored) into the env.

    Lets `python build.py` pick up the CSV URLs locally without committing them.
    Existing environment variables (e.g. CI secrets) always take precedence.
    """
    path = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".env")
    if not os.path.exists(path):
        return
    with open(path, encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, value = line.split("=", 1)
            os.environ.setdefault(key.strip(), value.strip())


_load_dotenv()

# ── Super League of Malawi (top tier) ───────────────────────────────────────
SL_LEAGUE_NAME = "Super League of Malawi"
SL_SEASON = os.environ.get("SL_SEASON", "26/27")
SL_CSV_TEAMS = os.environ.get("SL_CSV_TEAMS", "SL_CSV_TEAMS")
SL_CSV_MATCHES = os.environ.get("SL_CSV_MATCHES", "SL_CSV_MATCHES")

# ── National Division League (second tier) ──────────────────────────────────
NDL_LEAGUE_NAME = "National Division League"
NDL_SEASON = os.environ.get("NDL_SEASON", "26/27")
NDL_CSV_TEAMS = os.environ.get("NDL_CSV_TEAMS", "NDL_CSV_TEAMS")
NDL_CSV_MATCHES = os.environ.get("NDL_CSV_MATCHES", "NDL_CSV_MATCHES")

# ── Under-16s Development League ────────────────────────────────────────────
LEAGUE_NAME = "Under-16s Development League"
SEASON = os.environ.get("SEASON", "26/27")
CSV_URL_TEAMS = os.environ.get("CSV_URL_TEAMS", "CSV_URL_TEAMS")
CSV_URL_MATCHES = os.environ.get("CSV_URL_MATCHES", "CSV_URL_MATCHES")

# ── Shared ───────────────────────────────────────────────────────────────────
# Timezone for the "last updated" stamp. Malawi is CAT (UTC+2), no DST.
TZ_OFFSET_HOURS = 2
TZ_LABEL = "CAT"
