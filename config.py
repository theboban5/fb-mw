"""Site + data configuration. This is the only file you edit for content.

To go live, replace the two placeholder strings below with your Google Sheet
"Publish to web" CSV URLs (File -> Share -> Publish to web -> pick the tab ->
CSV). They look like:
  https://docs.google.com/spreadsheets/d/e/XXXX/pub?gid=0&single=true&output=csv

For local testing you can instead point these at local .csv files, or override
them without editing this file via the CSV_URL_TEAMS / CSV_URL_MATCHES env vars.
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

# Shown in the page header and title.
LEAGUE_NAME = "Under-16s Development League"

# Season label shown in the Design B hero banner (e.g. "26/27").
SEASON = os.environ.get("SEASON", "26/27")

# Data sources. Replace the placeholders with your published CSV URLs.
# A URL (http/https) is fetched; anything else is read as a local file path.
CSV_URL_TEAMS = os.environ.get("CSV_URL_TEAMS", "CSV_URL_TEAMS")
CSV_URL_MATCHES = os.environ.get("CSV_URL_MATCHES", "CSV_URL_MATCHES")

# Timezone for the "last updated" stamp. Malawi is CAT (UTC+2), no DST.
TZ_OFFSET_HOURS = 2
TZ_LABEL = "CAT"
