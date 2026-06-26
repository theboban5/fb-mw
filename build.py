#!/usr/bin/env python3
"""Build the static site: fetch -> validate -> compute -> render.

Builds two leagues into subdirectories and a landing page at docs/index.html.

Usage:
    python build.py

Exits non-zero (and prints what's wrong) if the source data is invalid.
"""

from datetime import datetime, timezone, timedelta
import os
import shutil
import sys

ROOT = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, ROOT)

import config  # noqa: E402
from src import data, render, standings  # noqa: E402

DIST = os.path.join(ROOT, "docs")
STATIC = os.path.join(ROOT, "static")
TEMPLATES = os.path.join(ROOT, "templates")

BACK_LINK = '<a href="../" class="back-link">&#x2190; All Leagues</a>'


def _build_league(csv_teams, csv_matches, league_name, season, dist, updated):
    try:
        teams = data.parse_teams(data.fetch(csv_teams))
        matches = data.parse_matches(data.fetch(csv_matches))
        data.validate_match_codes(matches, teams)
    except data.DataError as err:
        print(f"\nERROR ({league_name}): {err}\n", file=sys.stderr)
        return None, None
    except OSError as err:
        print(
            f"\nERROR ({league_name}): could not read data: {err}\n",
            file=sys.stderr,
        )
        return None, None

    rows = standings.compute_standings(matches, teams)
    form = standings.recent_form(matches, teams)
    changes = standings.position_changes(matches, teams)
    days, history = standings.position_history(matches, teams)
    played_count = sum(1 for m in matches if m.played)
    total_goals = sum(m.home_goals + m.away_goals for m in matches if m.played)
    goals_per_game = total_goals / played_count if played_count > 0 else 0.0

    render.build_site(
        dist, TEMPLATES, STATIC, league_name, updated, rows, matches, teams,
        season=season, total_goals=total_goals, goals_per_game=goals_per_game,
        form=form, changes=changes, days=days, history=history,
        css_prefix="../", back_link=BACK_LINK, copy_static=False,
    )
    return len(teams), played_count


def _landing_card(slug, tier, name, season):
    """One league card; shows the league logo when static/logos/leagues/<slug> exists."""
    logo = ""
    for ext in (".svg", ".png"):
        if os.path.exists(os.path.join(STATIC, "logos", "leagues", slug + ext)):
            logo = f'<img class="lc-logo" src="logos/leagues/{slug}{ext}" alt="">'
            break
    return (
        f'<a href="{slug}/" class="league-card">'
        f"{logo}"
        f'<span class="lc-body">'
        f'<span class="lc-tier">{tier}</span>'
        f'<span class="lc-name">{name}</span>'
        f'<span class="lc-season">Season {season}</span>'
        f"</span>"
        f'<span class="lc-arrow">&#x2192;</span>'
        f"</a>"
    )


def _write_landing(season):
    cards = "\n    ".join([
        _landing_card("sl", "Top Tier", "Super League of Malawi", season),
        _landing_card("ndl", "Second Division", "National Division League", season),
        _landing_card("wp", "Women&#x2019;s First Division", "Women&#x2019;s Premiership", "25/26"),
        _landing_card("u16", "Development", "Under-16s Development League", season),
    ])
    html = f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<meta name="color-scheme" content="light dark">
<title>Malawi Football</title>
<link rel="stylesheet" href="style.css">
</head>
<body class="landing">
<main class="landing-main">
  <header class="landing-header">
    <h1 class="landing-title">Malawi Football</h1>
    <p class="landing-sub">Live tables &amp; results</p>
  </header>
  <div class="league-cards">
    {cards}
  </div>
</main>
</body>
</html>"""
    render._write(os.path.join(DIST, "index.html"), html)


def main():
    tz = timezone(timedelta(hours=config.TZ_OFFSET_HOURS), config.TZ_LABEL)
    now = datetime.now(tz)
    updated = f"{now.day} {now.strftime('%B %Y, %H:%M')} {config.TZ_LABEL}"

    # Copy static files once to docs root (recursively, so static/logos/ comes too)
    os.makedirs(DIST, exist_ok=True)
    shutil.copytree(
        STATIC, DIST, dirs_exist_ok=True,
        ignore=shutil.ignore_patterns(".DS_Store"),
    )
    render._write(os.path.join(DIST, ".nojekyll"), "")

    # Super League of Malawi
    sl_teams, sl_played = _build_league(
        config.SL_CSV_TEAMS, config.SL_CSV_MATCHES,
        config.SL_LEAGUE_NAME, config.SL_SEASON,
        os.path.join(DIST, "sl"), updated,
    )

    # National Division League
    ndl_teams, ndl_played = _build_league(
        config.NDL_CSV_TEAMS, config.NDL_CSV_MATCHES,
        config.NDL_LEAGUE_NAME, config.NDL_SEASON,
        os.path.join(DIST, "ndl"), updated,
    )

    # Women's Premiership
    wp_teams, wp_played = _build_league(
        config.WP_CSV_TEAMS, config.WP_CSV_MATCHES,
        config.WP_LEAGUE_NAME, config.WP_SEASON,
        os.path.join(DIST, "wp"), updated,
    )

    # Under-16s Development League
    u16_teams, u16_played = _build_league(
        config.CSV_URL_TEAMS, config.CSV_URL_MATCHES,
        config.LEAGUE_NAME, config.SEASON,
        os.path.join(DIST, "u16"), updated,
    )

    # Landing page
    _write_landing(config.SL_SEASON)

    # Remove stale root-level pages from old single-league structure
    for stale in ("results.html",):
        p = os.path.join(DIST, stale)
        if os.path.exists(p):
            os.remove(p)

    errors = 0
    parts = []
    for label, teams, played in [
        ("SL", sl_teams, sl_played),
        ("NDL", ndl_teams, ndl_played),
        ("WP", wp_teams, wp_played),
        ("U16", u16_teams, u16_played),
    ]:
        if teams is not None:
            parts.append(f"{label}: {teams} teams, {played} results")
        else:
            parts.append(f"{label}: SKIPPED (data error)")
            errors += 1
    print(f"Built {DIST}/  " + " | ".join(parts))
    if errors:
        sys.exit(1)


if __name__ == "__main__":
    main()
