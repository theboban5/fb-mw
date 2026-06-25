#!/usr/bin/env python3
"""Build the static league site: fetch -> validate -> compute -> render.

Usage:
    python build.py

Exits non-zero (and prints what's wrong) if the source data is invalid, so a
bad team code or malformed row can never silently produce a wrong table.
"""

from datetime import datetime, timezone, timedelta
import os
import sys

ROOT = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, ROOT)

import config  # noqa: E402
from src import data, render, standings  # noqa: E402

DIST = os.path.join(ROOT, "dist")
STATIC = os.path.join(ROOT, "static")
TEMPLATES = os.path.join(ROOT, "templates")


def main():
    try:
        teams = data.parse_teams(data.fetch(config.CSV_URL_TEAMS))
        matches = data.parse_matches(data.fetch(config.CSV_URL_MATCHES))
        data.validate_match_codes(matches, teams)
    except data.DataError as err:
        print(f"\n{err}\n", file=sys.stderr)
        sys.exit(1)
    except OSError as err:
        print(
            f"\nABORTING: could not read a data source: {err}\n"
            f"Check CSV_URL_TEAMS / CSV_URL_MATCHES in config.py.\n",
            file=sys.stderr,
        )
        sys.exit(1)

    rows = standings.compute_standings(matches, teams)

    tz = timezone(timedelta(hours=config.TZ_OFFSET_HOURS), config.TZ_LABEL)
    now = datetime.now(tz)
    updated = f"{now.day} {now.strftime('%B %Y, %H:%M')} {config.TZ_LABEL}"

    render.build_site(
        DIST, TEMPLATES, STATIC, config.LEAGUE_NAME, updated, rows, matches, teams
    )

    played = sum(1 for m in matches if m.played)
    print(
        f"Built {DIST}/  "
        f"({len(teams)} teams, {len(matches)} matches, {played} results)"
    )


if __name__ == "__main__":
    main()
