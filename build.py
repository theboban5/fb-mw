#!/usr/bin/env python3
"""Build the static site: fetch -> validate -> compute -> render.

Builds two leagues into subdirectories and a landing page at docs/index.html.

Usage:
    python build.py

Exits non-zero (and prints what's wrong) if the source data is invalid.
"""

from datetime import datetime, timezone, timedelta
import os
import sys

ROOT = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, ROOT)

import config  # noqa: E402
from src import data, render, scorers, standings  # noqa: E402

DIST = os.path.join(ROOT, "docs")
STATIC = os.path.join(ROOT, "static")
TEMPLATES = os.path.join(ROOT, "templates")

BACK_LINK = '<a href="../" class="back-link">&#x2190; All Leagues</a>'


def _build_league(csv_teams, csv_matches, league_name, season, dist, updated,
                  csv_goals=None):
    try:
        teams = data.parse_teams(data.fetch(csv_teams))
        matches = data.parse_matches(data.fetch(csv_matches))
        data.validate_match_codes(matches, teams)
        # Goals are opt-in per league: csv_goals is None for leagues without a
        # goals sheet, leaving these structures empty so their render is unchanged.
        goals = []
        if csv_goals:
            goals = data.parse_goals(data.fetch(csv_goals))
            data.validate_goal_links(goals, matches, teams)
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

    if goals:
        goals_by_match = scorers.goals_by_match(goals)
        top_scorers, own_goal_total, more_scorers = scorers.top_scorers(goals)
        team_scorers = scorers.team_top_scorers(goals, teams)
    else:
        goals_by_match, top_scorers, own_goal_total = {}, [], 0
        more_scorers, team_scorers = [], []

    render.build_site(
        dist, TEMPLATES, STATIC, league_name, updated, rows, matches, teams,
        season=season, total_goals=total_goals, goals_per_game=goals_per_game,
        form=form, changes=changes, days=days, history=history,
        css_prefix="../", back_link=BACK_LINK, copy_static=False,
        goals_by_match=goals_by_match, top_scorers=top_scorers,
        own_goal_total=own_goal_total, more_scorers=more_scorers,
        team_scorers=team_scorers,
    )
    return len(teams), played_count


def _live(slug, tier, name, season):
    """A live, tappable competition: an item dict rendered later by _row()."""
    return {"live": True, "slug": slug, "tier": tier, "name": name, "season": season}


def _soon(tier, name, region=None):
    """A roadmap ("Coming Soon") competition: rendered muted by _row()."""
    return {"live": False, "tier": tier, "name": name, "region": region}


def _logo_html(slug):
    """A small league logo <img> when one exists on disk, else "" (no logo)."""
    for ext in (".svg", ".png"):
        if os.path.exists(os.path.join(STATIC, "logos", "leagues", slug + ext)):
            return f'<img class="lc-logo" src="logos/leagues/{slug}{ext}" alt="">'
    return ""


def _row(item):
    """One competition as a light list row.

    Live items are an <a> that navigates to the league; roadmap items are a
    non-link <div> so they can never become a broken link.
    """
    tier = item["tier"]
    name = item["name"]
    if item["live"]:
        meta = f"{tier} &middot; Season {item['season']}"
        return (
            f'<a href="{item["slug"]}/" class="lc-row">'
            f"{_logo_html(item['slug'])}"
            f'<span class="lc-main">'
            f'<span class="lc-name">{name}</span>'
            f'<span class="lc-meta">{meta}</span>'
            f"</span>"
            f'<span class="lc-arrow">&#x2192;</span>'
            f"</a>"
        )
    region = item.get("region")
    meta = f"{region} Region &middot; {tier}" if region else tier
    return (
        f'<div class="lc-row is-soon" aria-disabled="true">'
        f'<span class="lc-main">'
        f'<span class="lc-name">{name}</span>'
        f'<span class="lc-meta">{meta}</span>'
        f"</span>"
        f'<span class="lc-badge">Coming Soon</span>'
        f"</div>"
    )


def _group(group):
    """A subheading (Leagues / Cups / …) followed by its competition rows.

    A group may carry an optional "extra" blob of HTML (e.g. the tier-pyramid
    disclosure) rendered between the heading and the row list.
    """
    rows = "\n      ".join(_row(item) for item in group["items"])
    extra = group.get("extra", "")
    return (
        f'<h3 class="lc-group">{group["label"]}</h3>\n'
        f'      {extra}'
        f'<div class="lc-list">\n      {rows}\n      </div>'
    )


# How the men's leagues rank relative to one another: a <details> disclosure
# needs no JS and is tap-friendly on mobile, so it's the simplest fit here.
_MEN_TIER_PYRAMID = """<details class="tier-info">
      <summary>Tiers <span class="tier-info-mark" aria-hidden="true">&#x24D8;</span></summary>
      <div class="tier-pyramid">
        <div class="tier-row tier-1">
          <span class="tier-num">Tier 1</span>
          <span class="tier-name">Super League of Malawi</span>
        </div>
        <div class="tier-link" aria-hidden="true">&#x2193;</div>
        <div class="tier-row tier-2">
          <span class="tier-num">Tier 2</span>
          <span class="tier-name">National Division League</span>
        </div>
        <div class="tier-link" aria-hidden="true">&#x2193;</div>
        <div class="tier-row tier-3">
          <span class="tier-num">Tier 3</span>
          <span class="tier-name">SRFA Division League 1 // CRFA Division One League // NRFA League One</span>
        </div>
      </div>
    </details>
    """


def _panel(cat, active=False):
    """One category's list of groups; hidden unless it's the active tab.

    The `hidden` attribute on inactive panels is the graceful-degradation
    guarantee: with JS disabled only the active (Men's) panel shows.
    """
    hidden = "" if active else " hidden"
    inner = "\n    ".join(_group(g) for g in cat["groups"])
    return (
        f'<section class="comp-panel" data-panel="{cat["key"]}"{hidden}>\n    '
        f"{inner}\n  </section>"
    )


# Tiny vanilla toggler: no framework, degrades to the Men's panel if it never runs.
_NAV_JS = """
(function(){
  var nav=document.querySelector('.comp-nav');
  if(!nav) return;
  var tabs=nav.querySelectorAll('.comp-tab');
  var panels=nav.querySelectorAll('.comp-panel');
  function selectTab(tab){
    var name=tab.dataset.tab;
    tabs.forEach(function(t){
      var on=(t===tab);
      t.classList.toggle('active',on);
      t.setAttribute('aria-selected',on?'true':'false');
    });
    panels.forEach(function(p){ p.hidden = (p.dataset.panel!==name); });
  }
  tabs.forEach(function(tab){
    tab.addEventListener('click',function(){ selectTab(tab); });
  });
})();
"""


def _landing_countries(season):
    """The landing content as data: a list of countries, each with categories.

    Only Malawi exists today, so it's rendered directly (no country picker). A
    second country is just another entry here; the renderer already loops, so a
    picker/heading can be layered on without touching this shape.
    """
    malawi = {
        "country": "Malawi",
        "categories": [
            {"key": "men", "label": "Men&#x2019;s", "groups": [
                {"label": "Leagues", "extra": _MEN_TIER_PYRAMID, "items": [
                    _live("sl", "Top Tier", "Super League of Malawi", season),
                    _live("ndl", "Second Division", "National Division League", season),
                    _live("srfa", "Division One", config.SRFA_LEAGUE_NAME, config.SRFA_SEASON),
                    _live("crfa", "Division One", config.CRFA_LEAGUE_NAME, config.CRFA_SEASON),
                    _live("nrfa", "League One", config.NRFA_LEAGUE_NAME, config.NRFA_SEASON),
                    _live("srfa2", "Division Two", config.SRFA2_LEAGUE_NAME, config.SRFA2_SEASON),
                ]},
                {"label": "Cups", "items": [
                    _soon("Cup", "FAM Charity Shield"),
                    _soon("Cup", "Airtel Top 8"),
                    _soon("Cup", "Castel Challenge Cup"),
                    _soon("Cup", "FDH Bank Cup"),
                ]},
                {"label": "National Team", "items": [
                    _soon("National Team", "Malawi Flames"),
                ]},
            ]},
            {"key": "women", "label": "Women&#x2019;s", "groups": [
                {"label": "Leagues", "items": [
                    _live("wp", "Women&#x2019;s First Division", "NBM Women&#x2019;s Premiership", "25/26"),
                    _soon("Premier Division", "Southern Region Women&#x2019;s Premier Division", region="Southern"),
                    _soon("Premier Division", "Central Region Women&#x2019;s Premier Division", region="Central"),
                    _soon("Premier Division", "Northern Region Women&#x2019;s Premier Division", region="Northern"),
                ]},
                {"label": "Cups", "items": [
                    _soon("Cup", "Women&#x2019;s Cups"),
                ]},
                {"label": "National Team", "items": [
                    _soon("National Team", "Malawi Scorchers"),
                ]},
            ]},
            {"key": "youth", "label": "Youth", "groups": [
                {"label": "Boys", "items": [
                    _soon("Under-23", "National Bank U23 Championship"),
                    _live("ku19", "Under-19", config.KU19_LEAGUE_NAME, config.KU19_SEASON),
                    _live("u16", "Development", "U16 Development League", season),
                    _soon("Under-14", "U14 Development League"),
                ]},
                {"label": "Girls", "items": [
                    _soon("Youth", "Girls&#x2019; Youth Competitions"),
                ]},
                {"label": "National Team", "items": [
                    _soon("National Team", "Boys&#x2019; Youth National Team"),
                    _soon("National Team", "Girls&#x2019; Youth National Team"),
                ]},
            ]},
        ],
    }
    return [malawi]


def _write_landing(season):
    css_ver = render.css_version(STATIC)
    # Single country today; the first category (Men's) is the active tab.
    categories = _landing_countries(season)[0]["categories"]

    tabs = "".join(
        f'<button class="comp-tab{" active" if i == 0 else ""}" type="button" '
        f'data-tab="{cat["key"]}" aria-selected="{"true" if i == 0 else "false"}">'
        f'{cat["label"]}</button>'
        for i, cat in enumerate(categories)
    )
    tabs = (
        '<div class="comp-tab-row" role="tablist" '
        f'aria-label="Competition category">{tabs}</div>'
    )
    panels = "\n    ".join(
        _panel(cat, active=(i == 0)) for i, cat in enumerate(categories)
    )

    html = f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<meta name="color-scheme" content="light dark">
<title>Malawi Football</title>
<link rel="stylesheet" href="style.css?v={css_ver}">
</head>
<body class="landing">
<main class="landing-main">
  <header class="landing-header">
    <h1 class="landing-title">Malawi Football</h1>
    <p class="landing-sub">Live tables &amp; results</p>
  </header>
  <div class="comp-nav">
    <div class="comp-sticky">
      {tabs}
    </div>
    <div class="comp-panels">
    {panels}
    </div>
  </div>
</main>
<script>{_NAV_JS}</script>
</body>
</html>"""
    render._write(os.path.join(DIST, "index.html"), html)


def main():
    tz = timezone(timedelta(hours=config.TZ_OFFSET_HOURS), config.TZ_LABEL)
    now = datetime.now(tz)
    updated = f"{now.day} {now.strftime('%B %Y, %H:%M')} {config.TZ_LABEL}"

    # Copy static files once to docs root (logos are downscaled along the way)
    os.makedirs(DIST, exist_ok=True)
    render.copy_static_tree(STATIC, DIST)
    render._write(os.path.join(DIST, ".nojekyll"), "")
    # Custom domain for GitHub Pages. Written on every build because the Pages
    # deploy uploads docs/ as an artifact — a CNAME committed via Settings would
    # be wiped here, detaching the domain. everyleague.football redirects to this
    # apex via Porkbun URL forwarding.
    render._write(os.path.join(DIST, "CNAME"), "everyleague.co\n")

    # Super League of Malawi
    sl_teams, sl_played = _build_league(
        config.SL_CSV_TEAMS, config.SL_CSV_MATCHES,
        config.SL_LEAGUE_NAME, config.SL_SEASON,
        os.path.join(DIST, "sl"), updated,
        csv_goals=config.SL_CSV_GOALS,
    )

    # National Division League
    ndl_teams, ndl_played = _build_league(
        config.NDL_CSV_TEAMS, config.NDL_CSV_MATCHES,
        config.NDL_LEAGUE_NAME, config.NDL_SEASON,
        os.path.join(DIST, "ndl"), updated,
        csv_goals=config.NDL_CSV_GOALS,
    )

    # Women's Premiership
    wp_teams, wp_played = _build_league(
        config.WP_CSV_TEAMS, config.WP_CSV_MATCHES,
        config.WP_LEAGUE_NAME, config.WP_SEASON,
        os.path.join(DIST, "wp"), updated,
        csv_goals=config.WP_CSV_GOALS,
    )

    # SRFA FINCA Division League 1 (Southern Region, third tier)
    srfa_teams, srfa_played = _build_league(
        config.SRFA_CSV_TEAMS, config.SRFA_CSV_MATCHES,
        config.SRFA_LEAGUE_NAME, config.SRFA_SEASON,
        os.path.join(DIST, "srfa"), updated,
        csv_goals=config.SRFA_CSV_GOALS,
    )

    # GoJet Investments CRFA Division One League (Central Region, third tier)
    crfa_teams, crfa_played = _build_league(
        config.CRFA_CSV_TEAMS, config.CRFA_CSV_MATCHES,
        config.CRFA_LEAGUE_NAME, config.CRFA_SEASON,
        os.path.join(DIST, "crfa"), updated,
        csv_goals=config.CRFA_CSV_GOALS,
    )

    # Chiwemi Investment NRFA League One (Northern Region, third tier).
    # Season not yet started: the matches sheet may have no rows, which the
    # whole pipeline handles — the table shows every team on zero.
    nrfa_teams, nrfa_played = _build_league(
        config.NRFA_CSV_TEAMS, config.NRFA_CSV_MATCHES,
        config.NRFA_LEAGUE_NAME, config.NRFA_SEASON,
        os.path.join(DIST, "nrfa"), updated,
        csv_goals=config.NRFA_CSV_GOALS,
    )

    # SRFA Sultan Concrete Division 2 (Southern Region, fourth tier)
    srfa2_teams, srfa2_played = _build_league(
        config.SRFA2_CSV_TEAMS, config.SRFA2_CSV_MATCHES,
        config.SRFA2_LEAGUE_NAME, config.SRFA2_SEASON,
        os.path.join(DIST, "srfa2"), updated,
        csv_goals=config.SRFA2_CSV_GOALS,
    )

    # Katswiri U19 League (Blantyre District Youth FC, youth boys)
    ku19_teams, ku19_played = _build_league(
        config.KU19_CSV_TEAMS, config.KU19_CSV_MATCHES,
        config.KU19_LEAGUE_NAME, config.KU19_SEASON,
        os.path.join(DIST, "ku19"), updated,
        csv_goals=config.KU19_CSV_GOALS,
    )

    # Under-16s Development League
    u16_teams, u16_played = _build_league(
        config.CSV_URL_TEAMS, config.CSV_URL_MATCHES,
        config.LEAGUE_NAME, config.SEASON,
        os.path.join(DIST, "u16"), updated,
        csv_goals=config.CSV_URL_GOALS,
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
        ("SRFA", srfa_teams, srfa_played),
        ("CRFA", crfa_teams, crfa_played),
        ("NRFA", nrfa_teams, nrfa_played),
        ("SRFA2", srfa2_teams, srfa2_played),
        ("KU19", ku19_teams, ku19_played),
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
