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
        # Goals are Super-League-only; csv_goals is None for every other league,
        # leaving these structures empty so render output is unchanged for them.
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


def _live_card(slug, tier, name, season):
    """A live, tappable league card; shows the league logo when one exists."""
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


def _soon_card(tier, name, region=None):
    """A muted, non-tappable roadmap card with a "Coming Soon" badge.

    Renders as a <div> (not a link) so it can never become a broken link; an
    optional region chip distinguishes the regional divisions at a glance.
    """
    region_html = f'<span class="lc-region">{region} Region</span>' if region else ""
    return (
        f'<div class="league-card is-soon" aria-disabled="true">'
        f'<span class="lc-body">'
        f'<span class="lc-tier">{tier}</span>'
        f'<span class="lc-name">{name}</span>'
        f"{region_html}"
        f"</span>"
        f'<span class="lc-badge">Coming Soon</span>'
        f"</div>"
    )


def _panel(key, cards, active=False):
    """A competition list for one sub-filter; hidden unless it's the active one.

    The `hidden` attribute on inactive panels is the graceful-degradation
    guarantee: with JS disabled only the active (Men's Leagues) panel shows.
    """
    hidden = "" if active else " hidden"
    single = " single" if len(cards) == 1 else ""
    inner = "\n      ".join(cards)
    return (
        f'<section class="comp-panel" data-panel="{key}"{hidden}>'
        f'<div class="league-cards{single}">\n      {inner}\n    </div>'
        f"</section>"
    )


def _subbar(name, items, active=False):
    """A second-level filter row (Leagues/Cups/… or Boys/Girls) for one tab."""
    hidden = "" if active else " hidden"
    btns = "".join(
        f'<button class="comp-sub{" active" if i == 0 else ""}" '
        f'type="button" data-sub="{key}">{label}</button>'
        for i, (key, label) in enumerate(items)
    )
    return f'<div class="comp-subbar" data-subbar="{name}"{hidden}>{btns}</div>'


# Tiny vanilla toggler: no framework, degrades to Men's Leagues if it never runs.
_NAV_JS = """
(function(){
  var nav=document.querySelector('.comp-nav');
  if(!nav) return;
  var tabs=nav.querySelectorAll('.comp-tab');
  var subbars=nav.querySelectorAll('.comp-subbar');
  var panels=nav.querySelectorAll('.comp-panel');
  var lastSub={};
  function showPanel(key){
    panels.forEach(function(p){ p.hidden = (p.dataset.panel!==key); });
  }
  function setSubActive(bar,key){
    bar.querySelectorAll('.comp-sub').forEach(function(s){
      s.classList.toggle('active', s.dataset.sub===key);
    });
  }
  function selectTab(tab){
    var name=tab.dataset.tab, active=null;
    tabs.forEach(function(t){
      var on=(t===tab);
      t.classList.toggle('active',on);
      t.setAttribute('aria-selected',on?'true':'false');
    });
    subbars.forEach(function(b){
      var on=(b.dataset.subbar===name);
      b.hidden=!on;
      if(on) active=b;
    });
    var key=lastSub[name]||active.querySelector('.comp-sub').dataset.sub;
    setSubActive(active,key);
    showPanel(key);
  }
  tabs.forEach(function(tab){
    tab.addEventListener('click',function(){ selectTab(tab); });
  });
  subbars.forEach(function(bar){
    bar.querySelectorAll('.comp-sub').forEach(function(s){
      s.addEventListener('click',function(){
        lastSub[bar.dataset.subbar]=s.dataset.sub;
        setSubActive(bar,s.dataset.sub);
        showPanel(s.dataset.sub);
      });
    });
  });
})();
"""


def _write_landing(season):
    css_ver = render.css_version(STATIC)

    # ── MEN'S ────────────────────────────────────────────────
    men_leagues = [
        _live_card("sl", "Top Tier", "Super League of Malawi", season),
        _live_card("ndl", "Second Division", "National Division League", season),
        _live_card("srfa", "Division One", config.SRFA_LEAGUE_NAME, config.SRFA_SEASON),
        _live_card("crfa", "Division One", config.CRFA_LEAGUE_NAME, config.CRFA_SEASON),
        _live_card("nrfa", "League One", config.NRFA_LEAGUE_NAME, config.NRFA_SEASON),
    ]
    men_cups = [
        _soon_card("Cup", "FAM Charity Shield"),
        _soon_card("Cup", "Airtel Top 8"),
        _soon_card("Cup", "Castel Challenge Cup"),
        _soon_card("Cup", "FDH Bank Cup"),
    ]
    men_nt = [_soon_card("National Team", "Malawi Flames")]

    # ── WOMEN'S ──────────────────────────────────────────────
    women_leagues = [
        _live_card("wp", "Women&#x2019;s First Division", "NBM Women&#x2019;s Premiership", "25/26"),
        _soon_card("Premier Division", "Southern Region Women&#x2019;s Premier Division", region="Southern"),
        _soon_card("Premier Division", "Central Region Women&#x2019;s Premier Division", region="Central"),
        _soon_card("Premier Division", "Northern Region Women&#x2019;s Premier Division", region="Northern"),
    ]
    women_cups = [_soon_card("Cup", "Women&#x2019;s Cups")]
    women_nt = [_soon_card("National Team", "Malawi Scorchers")]

    # ── YOUTH ────────────────────────────────────────────────
    youth_boys = [
        _soon_card("Under-23", "National Bank U23 Championship"),
        _soon_card("Under-19", "U19 Development League"),
        _live_card("u16", "Development", "U16 Development League", season),
        _soon_card("Under-14", "U14 Development League"),
    ]
    youth_girls = [_soon_card("Youth", "Girls&#x2019; Youth Competitions")]
    youth_nt = [
        _soon_card("National Team", "Boys&#x2019; Youth National Team"),
        _soon_card("National Team", "Girls&#x2019; Youth National Team"),
    ]

    tabs = (
        '<div class="comp-tab-row" role="tablist" aria-label="Competition category">'
        '<button class="comp-tab active" type="button" data-tab="men" aria-selected="true">Men&#x2019;s</button>'
        '<button class="comp-tab" type="button" data-tab="women" aria-selected="false">Women&#x2019;s</button>'
        '<button class="comp-tab" type="button" data-tab="youth" aria-selected="false">Youth</button>'
        "</div>"
    )
    subbars = "".join([
        _subbar("men", [("men-leagues", "Leagues"), ("men-cups", "Cups"), ("men-nt", "National Team")], active=True),
        _subbar("women", [("women-leagues", "Leagues"), ("women-cups", "Cups"), ("women-nt", "National Team")]),
        _subbar("youth", [("youth-boys", "Boys"), ("youth-girls", "Girls"), ("youth-nt", "National Team")]),
    ])
    panels = "\n    ".join([
        _panel("men-leagues", men_leagues, active=True),
        _panel("men-cups", men_cups),
        _panel("men-nt", men_nt),
        _panel("women-leagues", women_leagues),
        _panel("women-cups", women_cups),
        _panel("women-nt", women_nt),
        _panel("youth-boys", youth_boys),
        _panel("youth-girls", youth_girls),
        _panel("youth-nt", youth_nt),
    ])

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
      <div class="comp-subfilters">{subbars}</div>
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
    )

    # Women's Premiership
    wp_teams, wp_played = _build_league(
        config.WP_CSV_TEAMS, config.WP_CSV_MATCHES,
        config.WP_LEAGUE_NAME, config.WP_SEASON,
        os.path.join(DIST, "wp"), updated,
    )

    # SRFA FINCA Division League 1 (Southern Region, third tier)
    srfa_teams, srfa_played = _build_league(
        config.SRFA_CSV_TEAMS, config.SRFA_CSV_MATCHES,
        config.SRFA_LEAGUE_NAME, config.SRFA_SEASON,
        os.path.join(DIST, "srfa"), updated,
    )

    # GoJet Investments CRFA Division One League (Central Region, third tier)
    crfa_teams, crfa_played = _build_league(
        config.CRFA_CSV_TEAMS, config.CRFA_CSV_MATCHES,
        config.CRFA_LEAGUE_NAME, config.CRFA_SEASON,
        os.path.join(DIST, "crfa"), updated,
    )

    # Chiwemi Investment NRFA League One (Northern Region, third tier).
    # Season not yet started: the matches sheet may have no rows, which the
    # whole pipeline handles — the table shows every team on zero.
    nrfa_teams, nrfa_played = _build_league(
        config.NRFA_CSV_TEAMS, config.NRFA_CSV_MATCHES,
        config.NRFA_LEAGUE_NAME, config.NRFA_SEASON,
        os.path.join(DIST, "nrfa"), updated,
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
        ("SRFA", srfa_teams, srfa_played),
        ("CRFA", crfa_teams, crfa_played),
        ("NRFA", nrfa_teams, nrfa_played),
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
