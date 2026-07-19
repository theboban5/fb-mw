#!/usr/bin/env python3
"""Build the static site from the normalized 13-tab schema.

Pipeline: fetch all 13 tabs -> validate (any ERROR aborts before a single
page is written, so production is never touched by bad data) -> snapshot to
data/canonical/ -> render every competition that has a competition_seasons
row -> landing page.

Usage:
    python build.py [--dist DIR] [--no-snapshot] [--allow-deletions]

--dist DIR          output directory (default: docs). Use a staging dir for
                    parity checks, e.g. --dist staging.
--no-snapshot       don't update data/canonical/ (staging/parity builds).
--allow-deletions   pass through to the validator's drift check.
"""

from datetime import datetime, timezone, timedelta
from html import escape
import os
import sys

ROOT = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, ROOT)

import validate  # noqa: E402
from src import adapt, dataset, hubs, render, scorers, standings  # noqa: E402

STATIC = os.path.join(ROOT, "static")
TEMPLATES = os.path.join(ROOT, "templates")

BACK_LINK = '<a href="../" class="back-link">&#x2190; All Leagues</a>'

# Timezone for the "last updated" stamp. Malawi is CAT (UTC+2), no DST.
TZ_OFFSET_HOURS = 2
TZ_LABEL = "CAT"

# The short tier caption under each league name on the landing page. These
# are editorial labels, not data; unknown competitions fall back to a label
# derived from competitions.tier / age_group.
TIER_LABELS = {
    "MW_SL": "Top Tier",
    "MW_NDL": "Second Division",
    "MW_SRFA": "Division One",
    "MW_CRFA": "Division One",
    "MW_NRFA": "League One",
    "MW_SRFA2": "Division Two",
    "MW_WP": "Women&#x2019;s First Division",
    "MW_KU19": "Under-19",
    "MW_U16": "Development",
}

_TIER_WORDS = {1: "Top Tier", 2: "Second Division", 3: "Third Division",
               4: "Fourth Division"}

# Landing-page ordering of live competitions inside a group; anything not
# listed sorts after these by (tier, name).
_LANDING_ORDER = list(adapt.COMPETITION_SLUGS)


def _tier_label(comp: "dataset.Competition") -> str:
    if comp.competition_id in TIER_LABELS:
        return TIER_LABELS[comp.competition_id]
    if comp.age_group != "senior":
        return escape(comp.age_group.upper())
    if comp.type == "cup":
        return "Cup"
    if comp.tier in _TIER_WORDS:
        return _TIER_WORDS[comp.tier]
    return "League"


def _build_league(ds, cs, dist_root, updated):
    """Render one competition+season into dist_root/<slug>/."""
    league = adapt.league_data(ds, cs.competition_id, cs.season_id)

    table_kwargs = {
        "points_win": league.points_win,
        "points_draw": league.points_draw,
        "adjustments": league.adjustments,
    }
    rows = standings.compute_standings(league.matches, league.teams, **table_kwargs)
    form = standings.recent_form(league.matches, league.teams)
    changes = standings.position_changes(league.matches, league.teams, **table_kwargs)
    days, history = standings.position_history(league.matches, league.teams, **table_kwargs)
    played_count = sum(1 for m in league.matches if m.played)
    total_goals = sum(m.home_goals + m.away_goals for m in league.matches if m.played)
    goals_per_game = total_goals / played_count if played_count > 0 else 0.0

    if league.goals:
        goals_by_match = scorers.goals_by_match(league.goals)
        top_scorers, _og_from_rows, more_scorers = scorers.top_scorers(league.goals)
        team_scorers = scorers.team_top_scorers(league.goals, league.teams)
    else:
        goals_by_match, top_scorers, more_scorers, team_scorers = {}, [], [], []

    render.build_site(
        os.path.join(dist_root, league.slug), TEMPLATES, STATIC,
        league.league_name, updated, rows, league.matches, league.teams,
        season=league.season, total_goals=total_goals, goals_per_game=goals_per_game,
        form=form, changes=changes, days=days, history=history,
        css_prefix="../", back_link=BACK_LINK, copy_static=False,
        goals_by_match=goals_by_match, top_scorers=top_scorers,
        # own_goal_total from the adapter, not the scorer rows: it includes
        # own goals by unresolved (CAF_MW_UNKNOWN) players.
        own_goal_total=league.own_goal_total,
        more_scorers=more_scorers, team_scorers=team_scorers,
        promotion_spots=league.promotion_places,
        relegation_spots=league.relegation_places,
        withdrawn=league.withdrawn,
        adjustment_reasons=league.adjustment_reasons,
        crest_keys={code: t.club_id for code, t in league.teams.items()},
        competition_id=league.competition_id,
        # Team names on league pages link to the cross-competition club hub;
        # the per-league club pages stay generated so their URLs keep working.
        # The #club-team-{code} fragment tells the hub which row to highlight
        # as "currently viewing" (see hubs.render_club_hub).
        club_hrefs={code: f"../clubs/{t.club_id}.html#club-team-{code}"
                    for code, t in league.teams.items()},
        # Club overview pages link back up to the cross-competition hub, so
        # a visitor can hop to another of the club's squads without going
        # back through a league page (see render.render_club).
        club_names={code: ds.clubs[t.club_id].name
                    for code, t in league.teams.items() if t.club_id in ds.clubs},
    )
    return league, rows, played_count


# ── Landing page ─────────────────────────────────────────────────────────────
# Live rows come from the data (grouped by competitions.gender/age_group/type);
# the "Coming Soon" roadmap rows and the tier pyramid are editorial content.

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


def _soon(tier, name, region=None):
    return {"live": False, "tier": tier, "name": name, "region": region}


def _live_item(ds, league):
    comp = ds.competitions[league.competition_id]
    return {
        "live": True,
        "slug": league.slug,
        "competition_id": league.competition_id,
        "tier": _tier_label(comp),
        "name": escape(league.league_name),
        "season": league.season,
        "sort": (
            _LANDING_ORDER.index(league.competition_id)
            if league.competition_id in _LANDING_ORDER else len(_LANDING_ORDER),
            comp.tier or 99,
            league.league_name,
        ),
    }


def _logo_html(item):
    """League logo <img> when one exists on disk (new naming, then old)."""
    for subdir, key in (("competitions", item.get("competition_id", "")),
                        ("leagues", item["slug"])):
        if not key:
            continue
        for ext in (".svg", ".png"):
            if os.path.exists(os.path.join(STATIC, "logos", subdir, key + ext)):
                return (f'<img class="lc-logo" '
                        f'src="logos/{subdir}/{key}{ext}" alt="">')
    return ""


def _row(item):
    tier = item["tier"]
    name = item["name"]
    if item["live"]:
        meta = f"{tier} &middot; Season {item['season']}"
        return (
            f'<a href="{item["slug"]}/" class="lc-row">'
            f"{_logo_html(item)}"
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
    rows = "\n      ".join(_row(item) for item in group["items"])
    extra = group.get("extra", "")
    return (
        f'<h3 class="lc-group">{group["label"]}</h3>\n'
        f'      {extra}'
        f'<div class="lc-list">\n      {rows}\n      </div>'
    )


def _panel(cat, active=False):
    hidden = "" if active else " hidden"
    inner = "\n    ".join(_group(g) for g in cat["groups"])
    return (
        f'<section class="comp-panel" data-panel="{cat["key"]}"{hidden}>\n    '
        f"{inner}\n  </section>"
    )


def _landing_categories(ds, leagues):
    """Group the live leagues by competitions.gender / age_group / type.

    Men's / Women's / Youth tabs; leagues vs cups within each. Roadmap
    ("Coming Soon") rows stay editorial until those competitions have data.
    """
    buckets = {
        ("men", "league"): [], ("men", "cup"): [],
        ("women", "league"): [], ("women", "cup"): [],
        ("youth-boys", "league"): [], ("youth-girls", "league"): [],
    }
    for league in leagues:
        comp = ds.competitions[league.competition_id]
        if comp.age_group != "senior":
            key = "youth-girls" if comp.gender == "w" else "youth-boys"
        elif comp.gender == "w":
            key = "women"
        else:
            key = "men"
        kind = "cup" if comp.type == "cup" else "league"
        buckets.setdefault((key, kind), []).append(_live_item(ds, league))
    for items in buckets.values():
        items.sort(key=lambda it: it["sort"])

    men_cups = buckets[("men", "cup")] + [
        _soon("Cup", "FAM Charity Shield"),
        _soon("Cup", "Airtel Top 8"),
        _soon("Cup", "Castel Challenge Cup"),
        _soon("Cup", "FDH Bank Cup"),
    ]
    women_leagues = buckets[("women", "league")] + [
        _soon("Premier Division", "Southern Region Women&#x2019;s Premier Division", region="Southern"),
        _soon("Premier Division", "Central Region Women&#x2019;s Premier Division", region="Central"),
        _soon("Premier Division", "Northern Region Women&#x2019;s Premier Division", region="Northern"),
    ]
    boys = buckets[("youth-boys", "league")]
    boys_items = (
        [_soon("Under-23", "National Bank U23 Championship")]
        + boys
        + [_soon("Under-14", "U14 Development League")]
    )
    girls_items = buckets[("youth-girls", "league")] + [
        _soon("Youth", "Girls&#x2019; Youth Competitions"),
    ]

    return [
        {"key": "men", "label": "Men&#x2019;s", "groups": [
            {"label": "Leagues", "extra": _MEN_TIER_PYRAMID,
             "items": buckets[("men", "league")]},
            {"label": "Cups", "items": men_cups},
            {"label": "National Team", "items": [
                _soon("National Team", "Malawi Flames"),
            ]},
        ]},
        {"key": "women", "label": "Women&#x2019;s", "groups": [
            {"label": "Leagues", "items": women_leagues},
            {"label": "Cups", "items": buckets[("women", "cup")] + [
                _soon("Cup", "Women&#x2019;s Cups"),
            ]},
            {"label": "National Team", "items": [
                _soon("National Team", "Malawi Scorchers"),
            ]},
        ]},
        {"key": "youth", "label": "Youth", "groups": [
            {"label": "Boys", "items": boys_items},
            {"label": "Girls", "items": girls_items},
            {"label": "National Team", "items": [
                _soon("National Team", "Boys&#x2019; Youth National Team"),
                _soon("National Team", "Girls&#x2019; Youth National Team"),
            ]},
        ]},
    ]


def _write_landing(dist, ds, leagues):
    css_ver = render.css_version(STATIC)
    categories = _landing_categories(ds, leagues)

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
<!-- Google tag (gtag.js) -->
<script async src="https://www.googletagmanager.com/gtag/js?id=G-RCV8V3DEKV"></script>
<script>
  window.dataLayer = window.dataLayer || [];
  function gtag(){{dataLayer.push(arguments);}}
  gtag('js', new Date());

  gtag('config', 'G-RCV8V3DEKV');
</script>
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
    render._write(os.path.join(dist, "index.html"), html)


def main(argv):
    dist = os.path.join(ROOT, "docs")
    if "--dist" in argv:
        dist = os.path.abspath(argv[argv.index("--dist") + 1])
    snapshot = "--no-snapshot" not in argv
    allow_deletions = "--allow-deletions" in argv

    tz = timezone(timedelta(hours=TZ_OFFSET_HOURS), TZ_LABEL)
    now = datetime.now(tz)
    updated = f"{now.day} {now.strftime('%B %Y, %H:%M')} {TZ_LABEL}"

    # 1. Fetch + validate. Any error aborts before a single page is written,
    # so a broken sheet can never produce a partial or wrong site.
    try:
        texts = dataset.fetch_all()
    except OSError as err:
        print(f"ERROR: could not fetch data: {err}", file=sys.stderr)
        return 1
    ds, errors = validate.validate(texts, allow_deletions=allow_deletions)
    if errors:
        print(f"VALIDATION FAILED — {len(errors)} error(s):", file=sys.stderr)
        for e in errors:
            print(f"  ERROR: {e}", file=sys.stderr)
        return 1

    # 2. Snapshot the validated fetch (git history of data/canonical/ is the
    # audit log; also the drift baseline for the next build).
    if snapshot:
        validate.write_snapshot(texts)

    # 3. Render.
    os.makedirs(dist, exist_ok=True)
    render.copy_static_tree(STATIC, dist)
    render._write(os.path.join(dist, ".nojekyll"), "")
    # Custom domain for GitHub Pages; rewritten every build because the Pages
    # artifact deploy would otherwise drop it (see repo history).
    render._write(os.path.join(dist, "CNAME"), "everyleague.co\n")

    leagues = []
    standings_by_slug = {}
    parts = []
    for cs in adapt.current_competition_seasons(ds):
        league, rows, n_played = _build_league(ds, cs, dist, updated)
        leagues.append(league)
        standings_by_slug[league.slug] = rows
        parts.append(f"{league.slug}: {len(league.teams)} teams, {n_played} results")

    _write_landing(dist, ds, leagues)

    # Cross-competition pages: club hubs and player pages.
    n_clubs = hubs.build_club_hubs(
        dist, TEMPLATES, STATIC, ds, leagues, standings_by_slug, updated)
    n_players = hubs.build_player_pages(dist, TEMPLATES, STATIC, ds, updated)

    print(f"Built {dist}/  " + " | ".join(parts)
          + f" | {n_clubs} club hubs | {n_players} player pages")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
