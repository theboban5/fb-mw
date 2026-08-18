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
import json
import os
import subprocess
import sys

ROOT = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, ROOT)

import validate  # noqa: E402
from src import (adapt, dataset, flags, hubs, matches_page, nt, nt_page,  # noqa: E402
                 render, scorers, search, standings)

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
    "MW_CRFA2": "Division Two",
    "MW_WP": "Women&#x2019;s First Division",
    "MW_KU19": "Under-19",
    "MW_U16": "Development",
    "MW_BDU16": "Under-16",
}

_TIER_WORDS = {1: "Top Tier", 2: "Second Division", 3: "Third Division",
               4: "Fourth Division"}

# Competitions whose pages are still built — so their URLs keep working — but
# which get no row on the landing page, and so are unreachable by navigation.
# The escape hatch for a competition that is in the data before it is ready to
# be read: MW_U16 is a placeholder with entries but no results.
HIDDEN_ON_LANDING = {"MW_U16"}

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

    # A knockout has no table: skip every standings-shaped computation and
    # render a bracket front page instead. Penalties never reach standings.py.
    is_cup = league.kind == "cup"
    if is_cup:
        rows, form, changes, days, history = [], {}, {}, [], {}
        cup_kwargs = {
            "kind": "cup",
            "md_labels": {md: adapt.STAGE_LABELS.get(s, s)
                          for md, s in league.stage_of_matchday.items()},
            "md_chips": {md: adapt.STAGE_CHIPS.get(s, s.upper())
                         for md, s in league.stage_of_matchday.items()},
            "bracket_rounds": adapt.cup_rounds(league.matches),
            "stage_labels": adapt.STAGE_LABELS,
        }
    else:
        table_kwargs = {
            "points_win": league.points_win,
            "points_draw": league.points_draw,
            "adjustments": league.adjustments,
        }
        rows = standings.compute_standings(league.matches, league.teams, **table_kwargs)
        form = standings.recent_form(league.matches, league.teams)
        changes = standings.position_changes(league.matches, league.teams, **table_kwargs)
        days, history = standings.position_history(league.matches, league.teams, **table_kwargs)
        cup_kwargs = {}
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
        # as "currently viewing" (see hubs.render_club_hub). Cup pages link
        # without it: the id belongs to the league row (a team can hold the
        # same code in a league and a cup, and ids must stay unique).
        club_hrefs={code: f"../clubs/{t.club_id}.html"
                          + ("" if is_cup else f"#club-team-{code}")
                    for code, t in league.teams.items()},
        # Club overview pages link back up to the cross-competition hub, so
        # a visitor can hop to another of the club's squads without going
        # back through a league page (see render.render_club).
        club_names={code: ds.clubs[t.club_id].name
                    for code, t in league.teams.items() if t.club_id in ds.clubs},
        **cup_kwargs,
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


def _live_page(name, tier, href, meta, logo=""):
    """A live row for a page that is not a competition+season (the national team).

    `href` and `meta` are given outright, where a league row derives them from
    its slug and season.
    """
    return {"live": True, "tier": tier, "name": name, "href": href,
            "meta": meta, "logo": logo, "sort": (0, 0, name)}


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
    if item.get("logo"):
        return f'<img class="lc-logo" src="{item["logo"]}" alt="">'
    for subdir, key in (("competitions", item.get("competition_id", "")),
                        ("leagues", item.get("slug", ""))):
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
        meta = item.get("meta") or f"{tier} &middot; Season {item['season']}"
        href = item.get("href") or f"{item['slug']}/"
        return (
            f'<a href="{href}" class="lc-row">'
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


def _landing_categories(ds, leagues, scorchers_meta=None):
    """Group the live leagues by competitions.gender / age_group / type.

    Men's / Women's / Youth tabs; leagues vs cups within each. Roadmap
    ("Coming Soon") rows stay editorial until those competitions have data.
    `scorchers_meta` (when the national-team page was built) turns the Women's
    National Team row from a roadmap card into a live link. Anything in
    HIDDEN_ON_LANDING gets no row at all — its pages exist but nothing links
    to them.
    """
    buckets = {
        ("men", "league"): [], ("men", "cup"): [],
        ("women", "league"): [], ("women", "cup"): [],
        ("youth-boys", "league"): [], ("youth-girls", "league"): [],
    }
    for league in leagues:
        if league.competition_id in HIDDEN_ON_LANDING:
            continue
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

    # The Airtel Top 8 left this roadmap when it went live from the data;
    # listing it here as well would show it twice.
    men_cups = buckets[("men", "cup")] + [
        _soon("Cup", "FAM Charity Shield"),
        _soon("Cup", "Castel Challenge Cup"),
        _soon("Cup", "FDH Bank Cup"),
    ]
    women_leagues = buckets[("women", "league")] + [
        _soon("Premier Division", "Southern Region Women&#x2019;s Premier Division", region="Southern"),
        _soon("Premier Division", "Central Region Women&#x2019;s Premier Division", region="Central"),
        _soon("Premier Division", "Northern Region Women&#x2019;s Premier Division", region="Northern"),
    ]
    boys = buckets[("youth-boys", "league")]
    boys_items = [_soon("Under-23", "National Bank U23 Championship")] + boys
    girls_items = buckets[("youth-girls", "league")] + [
        _soon("Youth", "Girls&#x2019; Youth Competitions"),
    ]
    # The Scorchers page is built from the nt_* tabs; the men's, youth and cup
    # National Team rows stay on the roadmap until those tabs are filled in.
    scorchers = (
        _live_page("Malawi Scorchers", "National Team",
                   f"{nt_page.SLUG}/", scorchers_meta,
                   logo=nt_page.FLAG_FILE)
        if scorchers_meta else _soon("National Team", "Malawi Scorchers")
    )

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
            {"label": "National Team", "items": [scorchers]},
        ]},
        {"key": "youth", "label": "Youth", "groups": [
            {"label": "Boys", "items": boys_items},
            {"label": "Girls", "items": girls_items},
            # The four youth sides already have nt_teams rows (MW_U20M,
            # MW_U17M, MW_U20W, MW_U17W); they stay on the roadmap until those
            # tabs carry matches, the same way the Scorchers row did.
            {"label": "National Team", "items": [
                _soon("Under-20", "U20 National Team (Men&#x2019;s)"),
                _soon("Under-17", "U17 National Team (Men&#x2019;s)"),
                _soon("Under-20", "U20 National Team (Women&#x2019;s)"),
                _soon("Under-17", "U17 National Team (Women&#x2019;s)"),
            ]},
        ]},
    ]


def _brand_header(fl):
    """The Everyleague hero: wordmark + country label, then the tagline.

    The country label uses the same flag PNGs as the rest of the site rather
    than a flag emoji, which renders as two letters on Windows.
    """
    flag = fl.img_for("Malawi", cls="el-flag")
    return f"""<header class="el-hero">
    <div class="el-brand-row">
      <div class="el-brand">
        <img class="el-brand-logo" src="everyleague_logo.png" alt=""
             width="360" height="242" decoding="async">
        <span class="el-brand-name">Everyleague</span>
      </div>
      <p class="el-locale">{flag}Malawi <span class="el-locale-sep">&middot;</span> Beta</p>
    </div>
    <h1 class="el-title">Every league. Every level.</h1>
    <p class="el-tagline">Fixtures, results and tables across Malawian football.</p>
  </header>"""


def _scorchers_feature(fl, team_data):
    """The featured Scorchers card, or "" when the national-team page is absent.

    One <a> wrapping the whole card — the CTA is a styled span, since a link
    inside a link is invalid — so the hit area is the card and the keyboard
    focus ring lands on it once. With a match scheduled the card runs two
    columns and the next-match panel takes the right one; without, it is a
    single column and the logo watermark fills the space instead.

    THE COPY BELOW IS HAND-WRITTEN AND DATES. It said "Follow the Scorchers as
    they take on Cameroon on Sunday" for a day after Cameroon had won that
    final 3-0, because nothing here reads the result — only `landing_next_match`
    is derived, and it correctly went quiet once there was no fixture left,
    which is what made the stale sentence beside it so obvious. Whoever changes
    the tournament this card points at must rewrite these three lines by hand.
    Last edited after the 2026 WAFCON final, 16 Aug 2026.
    """
    flag = fl.img_for("Malawi", cls="el-flag")
    nxt = nt_page.landing_next_match(team_data, fl)
    split = " has-next" if nxt else ""
    return f"""<a class="el-feature{split}" href="{nt_page.SLUG}/">
    <div class="el-feature-body">
      <span class="el-feature-eyebrow">{flag}Scorchers at WAFCON</span>
      <span class="el-feature-title">The journey to the final</span>
      <span class="el-feature-copy">Malawi beat Nigeria, Ghana and Algeria on the way to the Women&#x2019;s Africa
        Cup of Nations final, where Cameroon won 3&#x2013;0. Every result,
        line-up and scorer.</span>
      <span class="el-feature-cta">Fixtures, results and squad
        <span class="el-feature-arrow">&#x2192;</span></span>
    </div>
    {nxt}
  </a>"""


def _write_landing(dist, ds, leagues, updated, scorchers_meta=None, scorchers=None,
                   today_card=""):
    css_ver = render.css_version(STATIC)
    categories = _landing_categories(ds, leagues, scorchers_meta)
    fl = flags.Flags(STATIC)
    # No national-team page built means the card would link at a 404, so the
    # hero simply runs straight into the tabs.
    feature = _scorchers_feature(fl, scorchers) if scorchers_meta else ""

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
{render.social_meta("Everyleague — Malawi football", url=render.SITE_URL + "/")}
<link rel="stylesheet" href="style.css?v={css_ver}">
<link rel="icon" href="favicon.ico" sizes="any">
<link rel="icon" type="image/png" href="favicon-48.png" sizes="48x48">
<link rel="apple-touch-icon" href="apple-touch-icon.png">
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
  {_brand_header(fl)}
  {render.search_widget("", variant="hero")}
  {today_card}
  {feature}
  <div class="comp-nav">
    <div class="comp-sticky">
      {tabs}
    </div>
    <div class="comp-panels">
    {panels}
    </div>
  </div>
</main>
{render.footer(updated)}
<script>{_NAV_JS}</script>
</body>
</html>"""
    render._write(os.path.join(dist, "index.html"), html)


def _write_report_config(dist):
    """Write /report/config.js from the environment.

    The reporter app is static, so its Supabase endpoint has to be baked in at
    build time. Only the PUBLISHABLE key goes here: it is designed to be public
    and is subject to Row Level Security, under which every policy grants
    SELECT on football data and nothing else. The secret key must never reach
    this file — see the RLS tests, which prove the boundary holds for a real
    signed-in reporter.

    An unset environment writes empty values rather than failing: the public
    site does not depend on /report, and a missing key must never be able to
    stop a results deploy. The app then says it is unconfigured.
    """
    url = os.environ.get("SUPABASE_URL", "").strip()
    key = os.environ.get("SUPABASE_PUBLISHABLE_KEY", "").strip()
    if not (url and key):
        print("WARNING: SUPABASE_URL / SUPABASE_PUBLISHABLE_KEY unset; "
              "/report ships unconfigured (the public site is unaffected).")
    if "secret" in key:
        raise SystemExit(
            "REFUSING TO BUILD: SUPABASE_PUBLISHABLE_KEY looks like a secret "
            "key. It would be published to everyleague.co/report/config.js.")
    out = os.path.join(dist, "report")
    os.makedirs(out, exist_ok=True)
    render._write(os.path.join(out, "config.js"), (
        "// Generated by build.py — do not edit. See static/report/config.js.\n"
        f'export const SUPABASE_URL = "{url}";\n'
        f'export const SUPABASE_PUBLISHABLE_KEY = "{key}";\n'
    ))


def _crest_report(ds, club_ids):
    """Which of `club_ids` would render with no crest on the live site.

    Mirrors hubs.build_club_hubs exactly — logos/clubs/<club_id> first, then the
    legacy_code of any of the club's teams — because a list that disagreed with
    what the site actually shows would be worse than no list. The truth here is
    the FILESYSTEM, not clubs.crest: that column is populated on rows whose file
    does not exist, so it cannot answer this.
    """
    find = render._logo_finder(STATIC, "", "clubs")
    teams_of_club = {}
    for team in ds.teams.values():
        teams_of_club.setdefault(team.club_id, []).append(team)
    missing = []
    for club_id in sorted(club_ids):
        if find(club_id):
            continue
        if any(t.legacy_code and find(t.legacy_code)
               for t in teams_of_club.get(club_id, [])):
            continue
        missing.append(club_id)
    return missing


def _commit_sha():
    """The commit being built, for the ops dashboard. Best effort."""
    sha = os.environ.get("GITHUB_SHA", "")
    if sha:
        return sha[:8]
    try:
        return subprocess.run(
            ["git", "rev-parse", "--short=8", "HEAD"],
            cwd=ROOT, capture_output=True, text=True, timeout=5,
        ).stdout.strip()
    except (OSError, subprocess.SubprocessError):
        return ""


def _write_build_info(dist, ds, club_ids, data_read_at, tz):
    """Publish what the ops dashboard needs to know about this build.

    Two questions live here that Postgres cannot answer. "Is the site up to
    date?" needs the moment this build READ the database — nothing records it,
    and rebuild_state only knows when a rebuild was dispatched. "Which clubs
    have no crest?" needs the contents of static/logos/, which is in git, not
    in the database.

    A file rather than a write back to Supabase, deliberately: /report is served
    from the same origin as this file, so the dashboard can fetch it with no
    CORS, no credential and no new table. Everything in it is already public —
    a build time, a commit, and which clubs are missing a logo you could see by
    looking at the site.

    NEVER FAILS THE BUILD. A deploy that stops because its telemetry could not
    be written would be a far worse outcome than a dashboard with a stale
    timestamp, so every error here is a warning and the build carries on.
    """
    try:
        info = {
            # Taken here rather than reusing main()'s `now`, which is stamped
            # before the fetch: the dashboard reads this as "when the site was
            # last rebuilt", and a value that predates data_read_at reads as a
            # build that finished before it started.
            "built_at": datetime.now(tz).isoformat(),
            "data_read_at": data_read_at.isoformat(),
            "commit": _commit_sha(),
            "season_id": ds.active_season().season_id,
            "counts": {
                "matches": len(ds.matches),
                "goals": len(ds.goals),
                # Clubs that get a hub page — the same set hubs.build_club_hubs
                # writes. Deliberately not "clubs entered this season": a crest
                # is missing from a page a reader can open, and the completed
                # Women's Premiership is still on the site. That makes this 142
                # rather than the 137 entered in the active season.
                "clubs_with_page": len(club_ids),
            },
            "clubs_missing_crest": _crest_report(ds, club_ids),
        }
        render._write(os.path.join(dist, "build-info.json"),
                      json.dumps(info, indent=2) + "\n")
    except Exception as err:                                  # noqa: BLE001
        print(f"WARNING: could not write build-info.json: {err}")


def main(argv):
    dist = os.path.join(ROOT, "docs")
    if "--dist" in argv:
        dist = os.path.abspath(argv[argv.index("--dist") + 1])
    snapshot = "--no-snapshot" not in argv
    allow_deletions = "--allow-deletions" in argv
    # Lets every page state its own og:url; a staging build resolves the same
    # URLs as production, which is what makes a parity diff meaningful.
    render.DIST_ROOT = dist

    tz = timezone(timedelta(hours=TZ_OFFSET_HOURS), TZ_LABEL)
    now = datetime.now(tz)
    updated = f"{now.day} {now.strftime('%B %Y, %H:%M')} {TZ_LABEL}"
    # "Today" for the by-date pages. The Malawi calendar day, not the runner's:
    # the site's whole clock is CAT (see TZ_OFFSET_HOURS).
    today = now.date().isoformat()

    # 1. Fetch + validate. Any error aborts before a single page is written,
    # so a broken sheet can never produce a partial or wrong site.
    try:
        texts = dataset.fetch_all()
        nt_texts = dataset.fetch_nt_all()
        # The moment this build saw the database. The ops dashboard compares it
        # against matches.updated_at to answer "is there a saved result the site
        # has not shown yet?", so it is taken here — after the read, not at the
        # start of the job — or it would claim to have seen changes it missed.
        data_read_at = datetime.now(tz)
    except OSError as err:
        print(f"ERROR: could not fetch data: {err}", file=sys.stderr)
        return 1
    ds, errors = validate.validate(texts, allow_deletions=allow_deletions,
                                   nt_texts=nt_texts)
    if errors:
        print(f"VALIDATION FAILED — {len(errors)} error(s):", file=sys.stderr)
        for e in errors:
            print(f"  ERROR: {e}", file=sys.stderr)
        return 1

    # 2. Snapshot the validated fetch (git history of data/canonical/ is the
    # audit log; also the drift baseline for the next build).
    if snapshot:
        validate.write_snapshot(texts, nt_texts=nt_texts)

    # 3. Search asset versions. Both have to be known before the first page is
    # rendered, because every page embeds the versioned URLs. The index version
    # hashes the fetched CSVs rather than the finished index, which is what
    # makes it available this early (see search.index_version).
    render.SEARCH_INDEX_VERSION = search.index_version(texts, nt_texts)
    render.SEARCH_JS_VERSION = render.file_version(
        os.path.join(STATIC, "search.js"))

    # 4. Render.
    if not render.FEEDBACK_URL:
        print("WARNING: render.FEEDBACK_URL is unset; the footer ships without "
              "its 'Send us a message' line (better than a dead link).")
    os.makedirs(dist, exist_ok=True)
    render.copy_static_tree(STATIC, dist)
    _write_report_config(dist)
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

    # The national team (nt_* tabs): its own schema, its own page. Built before
    # the landing page, which needs its current competition for the card meta.
    # Parsed once here because the daily match pages need every nt team's
    # matches, not just the one team nt_page renders.
    nt_data = nt.parse_all(nt_texts)
    scorchers = nt.team_data(nt_data)
    # Club hubs exist for every club with a team in a built league — the same
    # set hubs.build_club_hubs writes — so squad club links only point at one
    # of those.
    club_hub_ids = {tv.club_id for league in leagues
                    for tv in league.teams.values() if tv.club_id}
    nt_page.build_page(dist, TEMPLATES, STATIC, scorchers, ds, updated,
                       club_hub_ids=club_hub_ids)

    # The by-date view (/matches/). Collected before the landing page, which
    # shows a card for the day it links at, and reused when the pages
    # themselves are written below.
    days = matches_page.collect(ds, nt_data)

    _write_landing(dist, ds, leagues, updated,
                   scorchers_meta=nt_page.landing_meta(scorchers),
                   scorchers=scorchers,
                   today_card=matches_page.landing_card(days, today))

    # Cross-competition pages: club hubs and player pages.
    n_clubs = hubs.build_club_hubs(
        dist, TEMPLATES, STATIC, ds, leagues, standings_by_slug, updated)
    n_players = hubs.build_player_pages(dist, TEMPLATES, STATIC, ds, updated)

    # The by-date pages, written after the club hubs they link into.
    n_day_pages, n_match_dates = matches_page.build_pages(
        dist, TEMPLATES, STATIC, ds, updated, today, nt_data=nt_data,
        club_hub_ids=club_hub_ids, tz_offset_hours=TZ_OFFSET_HOURS, days=days)

    # Site search: the index every page's bar fetches, plus the /search/ page
    # the bar's form submits to. Built last because the index covers the club
    # hubs and player pages written just above.
    n_search = search.write_index(dist, ds, leagues, scorchers=scorchers,
                                  hidden=HIDDEN_ON_LANDING)
    search.build_page(dist, TEMPLATES, STATIC, updated)

    # Written last: club_hub_ids is only known once the leagues are built, and
    # the file should describe a build that actually finished.
    _write_build_info(dist, ds, club_hub_ids, data_read_at, tz)

    print(f"Built {dist}/  " + " | ".join(parts)
          + f" | {n_clubs} club hubs | {n_players} player pages"
          + f" | {nt_page.SLUG}: {len(scorchers.results)} results,"
          + f" {len(scorchers.fixtures)} fixtures"
          + f" | {matches_page.SLUG}: {n_day_pages} pages,"
          + f" {n_match_dates} match dates"
          + f" | search: {n_search} records")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
