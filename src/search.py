"""Site search: the JSON index and the /search/ results page.

Everything on the site that has a page of its own — competitions, the national
team, club hubs, distinct squads and players with a goal credit — becomes one
row in dist/search-index.json. static/search.js fetches that file the first
time someone touches a search box and does all the matching client-side; there
is no server and no search API.

The index is an array of arrays rather than an array of objects on purpose: at
~680 records, repeating six field names per row would cost more bytes than
every club and player name in the file put together.

Row shape, mirrored by COLUMNS below and by search.js:

    [type, name, url, meta, weight, extra]

`url` is relative to the site root with no leading slash ("clubs/MW_BULL.html").
Pages sit at three different depths, so search.js prepends the same prefix the
page uses for its CSS — see render.search_widget.

`extra` is a space-joined bag of alternate search terms (aliases from the
sheet, a club's short_name, a player's full name when they go by another) that
should match but should not be displayed.
"""

from html import escape
import hashlib
import json
import os

from . import dataset, hubs, nt_page, officials, render

# Index text is data, not markup: search.js renders every field through
# textContent, so a "&middot;" here would show up literally on screen. Use the
# character. Only the /search/ page's own chrome below is escaped HTML.
SEP = "·"

# Bumped by hand when the shape or content rules of the index change, so that a
# rebuild on unchanged data still busts the cached URL. It is folded into
# index_version() alongside the data itself.
SCHEMA_VERSION = "1"

COLUMNS = ("type", "name", "url", "meta", "weight", "extra")

# Index into TYPES, stored as an integer per row. Order is also the grouping
# order used on the /search/ page.
# Appended to, never reordered: the index stores the integer, not the word.
TYPES = ("comp", "nt", "club", "team", "player", "official")
T_COMP, T_NT, T_CLUB, T_TEAM, T_PLAYER, T_OFFICIAL = range(len(TYPES))

# Human labels for the type groups on the /search/ results page.
TYPE_LABELS = ("Competitions", "National Team", "Clubs", "Teams", "Players",
               "Referees & Coaches")

# Static popularity priors, resolved before any query is typed. They only break
# ties inside a match tier — a worse textual match never outranks a better one.
_COMP_BASE = 90
_NT_WEIGHT = 92
_CLUB_BASE = 55
_CLUB_TIER_BONUS = {1: 20, 2: 12, 3: 6}
_CLUB_TIER_DEFAULT = 3
_TEAM_PENALTY = 5        # a squad page ranks just under its club's hub
_PLAYER_BASE = 18
_PLAYER_GOAL_CAP = 25    # a prolific scorer surfaces first, but never above a club
# Under a player of the same match quality: someone searching a name in Malawi
# means the player far more often than the referee.
_OFFICIAL_BASE = 14


def index_version(texts, nt_texts=None) -> str:
    """Cache-busting token for search-index.json, from the data it is built of.

    Hashing the fetched CSVs rather than the finished index means the version
    is known straight after validation — before any page is rendered — which is
    what lets every page embed the versioned URL in one pass. SCHEMA_VERSION
    covers the case where this module changes but the sheet did not.
    """
    h = hashlib.md5(SCHEMA_VERSION.encode("utf-8"))
    for tab in sorted(texts):
        h.update(texts[tab].encode("utf-8"))
    for tab in sorted(nt_texts or {}):
        h.update((nt_texts or {})[tab].encode("utf-8"))
    return h.hexdigest()[:8]


# ── Index building ───────────────────────────────────────────────────────────

def _alias_map(ds):
    """entity_id -> [alias_text]. The sheet's aliases tab, used by the admin
    entry tool for fast lookup, doubles as hand-written search synonyms here
    ("Nomads", "BULL") that no amount of prefix matching would find."""
    out = {}
    for a in ds.aliases:
        if a.entity_id and a.alias_text:
            out.setdefault(a.entity_id, []).append(a.alias_text)
    return out


def _extra(*terms) -> str:
    """Join alternate search terms, dropping blanks and case-insensitive dupes."""
    seen, kept = set(), []
    for t in terms:
        for part in (t if isinstance(t, (list, tuple)) else [t]):
            part = (part or "").strip()
            key = part.casefold()
            if part and key not in seen:
                seen.add(key)
                kept.append(part)
    return " ".join(kept)


def _player_stats(ds, credits):
    """player_id -> (goals, top_club_name), for weights and result captions.

    `credits` comes from hubs.player_goal_credits, so the totals a search
    result shows match the player's own page.
    """
    goals, by_team = {}, {}
    for g in ds.goals.values():
        if g.player_id == dataset.UNKNOWN_PLAYER_ID or g.is_own_goal:
            continue
        m = ds.matches.get(g.match_id)
        if m is None or m.is_placeholder:
            continue
        goals[g.player_id] = goals.get(g.player_id, 0) + 1
        key = (g.player_id, g.team_id)
        by_team[key] = by_team.get(key, 0) + 1

    top_club = {}
    for (player_id, team_id), n in by_team.items():
        best = top_club.get(player_id)
        if best is None or n > best[0]:
            team = ds.teams.get(team_id)
            club = ds.clubs.get(team.club_id) if team else None
            top_club[player_id] = (n, club.name if club else "")

    return {
        pid: (goals.get(pid, 0), top_club.get(pid, (0, ""))[1])
        for pid in set(credits) | set(goals)
    }


def _seniority(comp):
    """Sort key for "which of a club's competitions is its main one".

    competitions.tier is scoped to an age group, not to the whole pyramid:
    Katswiri U19 League and the Super League are *both* tier 1. Sorting on tier
    alone therefore captions Nyasa Big Bullets with its U19 side. Senior
    football wins first, and tier only breaks ties within an age group.
    """
    senior = 0 if (comp.age_group or "senior") == "senior" else 1
    return (senior, comp.tier if comp.tier is not None else 9, comp.name)


def _tier_bonus(comp):
    """Weight bonus for a club/squad, from the competition it plays in.

    Only senior competitions earn the tier bonus, for the same reason: a
    U19 tier-1 side is not a top-flight club.
    """
    if comp is None or (comp.age_group or "senior") != "senior":
        return _CLUB_TIER_DEFAULT
    return _CLUB_TIER_BONUS.get(comp.tier, _CLUB_TIER_DEFAULT)


def _club_competitions(ds, leagues):
    """club_id -> [(sort key, Competition)], the club's main competition first.

    Drives both the club's weight (a Super League side outranks a regional club
    of the same name) and the caption shown under it in results.
    """
    out = {}
    for league in leagues:
        comp = ds.competitions.get(league.competition_id)
        if comp is None:
            continue
        for tv in league.teams.values():
            if tv.club_id:
                out.setdefault(tv.club_id, []).append((_seniority(comp), comp))
    for club_id in out:
        out[club_id].sort(key=lambda pair: pair[0])
    return out


def build_index(ds, leagues, scorchers=None, hidden=(), ntd=None) -> list:
    """The list of index rows. Pure — writes nothing, so tests can inspect it.

    `hidden` is build.HIDDEN_ON_LANDING: competitions whose pages exist but
    which nothing links to. Search follows that editorial call — the
    competition and its squad pages stay out of the index — but the clubs
    entered in them keep their hubs, which are written unconditionally.

    `ntd` is the parsed nt_* tabs. It is here for one reason: a player whose
    only football in this database is for Malawi has a page, so they must have
    an index row, and hubs.player_page_ids cannot know about them without it.
    """
    aliases = _alias_map(ds)
    hidden = set(hidden)
    rows = []
    seen_urls = set()

    def add(type_idx, name, url, meta, weight, extra=""):
        # A squad can be entered in a league and a cup at once; whichever row
        # is built first wins, and the URL stays unique.
        if not name or url in seen_urls:
            return
        seen_urls.add(url)
        rows.append([type_idx, name, url, meta, min(int(weight), 99), extra])

    # ── Competitions ────────────────────────────────────────────────────────
    for league in leagues:
        if league.competition_id in hidden:
            continue
        comp = ds.competitions.get(league.competition_id)
        # Same age-group caveat as _tier_bonus: Katswiri U19 is tier 1 of U19
        # football, not tier 1 of the pyramid, so only senior competitions earn
        # the tier lift that would put them level with the Super League.
        senior = comp is not None and (comp.age_group or "senior") == "senior"
        tier = comp.tier if comp and comp.tier is not None else 4
        # league_name is the sponsored name ("FDH Bank Premiership"); the
        # official name ("Super League of Malawi") is what most people type,
        # so it goes in as a search term even when it is not displayed.
        official = comp.name if comp else ""
        add(
            T_COMP, league.league_name, f"{league.slug}/",
            f"Competition {SEP} Season {league.season}",
            _COMP_BASE + (max(0, 4 - tier) if senior else 0),
            _extra(official, aliases.get(league.competition_id, [])),
        )

    # ── National team ───────────────────────────────────────────────────────
    if scorchers is not None:
        add(T_NT, nt_page.DISPLAY_NAME, f"{nt_page.SLUG}/", "National Team",
            _NT_WEIGHT, _extra("Malawi", "Scorchers"))

    # ── Club hubs ───────────────────────────────────────────────────────────
    # The same set hubs.build_club_hubs writes: every club with a team in a
    # built competition. Anything else would point at a 404.
    club_comps = _club_competitions(ds, leagues)
    for club_id, comps in sorted(club_comps.items()):
        club = ds.clubs.get(club_id)
        if club is None:
            continue
        best = comps[0][1]
        meta = f" {SEP} ".join(p for p in ("Club", best.name, club.city) if p)
        add(
            T_CLUB, club.name, f"clubs/{club_id}.html", meta,
            _CLUB_BASE + _tier_bonus(best),
            _extra(club.short_name, aliases.get(club_id, [])),
        )

    # ── Squads ──────────────────────────────────────────────────────────────
    # Only leagues: cups emit no per-competition club pages (render.build_site
    # returns before writing them), so a cup squad URL would 404.
    for league in leagues:
        if league.kind == "cup" or league.competition_id in hidden:
            continue
        comp = ds.competitions.get(league.competition_id)
        for code, tv in sorted(league.teams.items()):
            club = ds.clubs.get(tv.club_id) if tv.club_id else None
            # A squad whose name is just the club's name would duplicate the
            # hub row, and the hub is the better destination — it already
            # lists every squad. Distinct squads ("Silver Strikers Women",
            # "Bullets Reserves") still get their own row.
            if club is not None and tv.name.casefold() == club.name.casefold():
                continue
            add(
                T_TEAM, tv.name, f"{league.slug}/clubs/{code}.html",
                f"{league.league_name} {SEP} {league.season}",
                _CLUB_BASE + _tier_bonus(comp) - _TEAM_PENALTY,
                _extra(tv.location, aliases.get(tv.team_id, [])),
            )

    # ── Players ─────────────────────────────────────────────────────────────
    credits, own_goals = hubs.player_goal_credits(ds)
    careers = hubs.player_careers(ds, ntd)
    stats = _player_stats(ds, credits)
    # The same set hubs.build_player_pages writes, from the same function:
    # indexing a player with no page is how a search result 404s.
    for player_id in sorted(hubs.player_page_ids(ds, credits, own_goals, careers)):
        player = ds.players[player_id]
        goals, club_name = stats.get(player_id, (0, ""))
        apps = len(careers[player_id].appearances) if player_id in careers else 0
        # A goal is the more interesting fact, so it leads; a player with none
        # gets their appearance count rather than the bare word "Player".
        if goals:
            bits = [f"{goals} goal{'' if goals == 1 else 's'}"]
        elif apps:
            bits = [f"{apps} appearance{'' if apps == 1 else 's'}"]
        else:
            bits = ["Player"]
        if club_name:
            bits.append(club_name)
        add(
            T_PLAYER, player.display_name, f"players/{player_id}.html",
            f" {SEP} ".join(bits),
            _PLAYER_BASE + min(goals, _PLAYER_GOAL_CAP),
            # Every players.csv row currently has a blank known_as, so
            # display_name is the full name and this adds nothing. It costs
            # nothing either, and starts earning the moment a player is given
            # a known_as — that is exactly when the full name stops showing.
            _extra(
                player.full_name if player.full_name != player.display_name else "",
                aliases.get(player_id, []),
            ),
        )

    # ── Referees and coaches ────────────────────────────────────────────────
    # The same set officials.build_official_pages writes, from the same
    # function, for the same reason the player block says: indexing someone
    # with no page is how a search result 404s.
    by_official = officials.duties(ds)
    for official_id in sorted(officials.official_page_ids(ds, by_official)):
        official = ds.officials[official_id]
        career = by_official[official_id]
        kind = "Head coach" if official.kind == "coach" else "Match official"
        n = len(career)
        add(
            T_OFFICIAL, official.display_name, f"officials/{official_id}.html",
            f"{kind} {SEP} {n} match{'' if n == 1 else 'es'}",
            _OFFICIAL_BASE,
            _extra(
                official.full_name
                if official.full_name != official.display_name else "",
                aliases.get(official_id, []),
            ),
        )

    return rows


def write_index(dist, ds, leagues, scorchers=None, hidden=(), ntd=None) -> int:
    """Write dist/search-index.json. Returns the number of records."""
    rows = build_index(ds, leagues, scorchers=scorchers, hidden=hidden, ntd=ntd)
    payload = {"v": 1, "types": list(TYPES), "docs": rows}
    render._write(
        os.path.join(dist, "search-index.json"),
        json.dumps(payload, ensure_ascii=False, separators=(",", ":")),
    )
    return len(rows)


# ── The /search/ results page ────────────────────────────────────────────────

SLUG = "search"

BACK_LINK = '<a href="../" class="back-link">&#x2190; All Leagues</a>'

_NOSCRIPT = (
    '<noscript><p class="v2-empty">Search needs JavaScript. '
    '<a class="club-link" href="../">Browse all competitions</a> instead.</p>'
    "</noscript>"
)


def build_page(dist, templates_dir, static_dir, updated) -> int:
    """Write dist/search/index.html — the full-results page the bar submits to.

    The page ships empty: search.js reads ?q= from the URL, fetches the index
    and renders the groups. That is also why it is noindex — there is nothing
    for a crawler here, and result URLs should not compete with the real pages.
    """
    out_dir = os.path.join(dist, SLUG)
    os.makedirs(out_dir, exist_ok=True)
    base = render._read(os.path.join(templates_dir, "base.html"))

    content = "\n".join([
        '<div class="v2-content">',
        '<div class="v2-mini-banner">',
        '<p class="v2-season">EVERYLEAGUE</p>',
        '<h2 class="v2-mini-league">SEARCH</h2>',
        "</div>",
        # search.js fills this; the heading it writes doubles as the live region.
        '<div class="sr-results" data-search-results></div>',
        _NOSCRIPT,
        "</div>",
    ])

    title = "Search"
    html = (
        base.replace("{{TITLE}}", escape(f"{title} · {render.SITE_NAME}"))
        .replace("{{LEAGUE_NAME}}", escape(title))
        .replace("{{LEAGUE_LOGO}}", "")
        .replace("{{LAST_UPDATED}}", escape(updated))
        .replace("{{NAV}}", render._nav("", items=(("../", "Home"),)))
        .replace("{{SEARCH}}", render.search_widget("../", variant="hero"))
        .replace("{{CONTENT}}", content)
        .replace("{{CSS_PREFIX}}", "../")
        .replace("{{CSS_VER}}", render.css_version(static_dir))
        .replace("{{BACK_LINK}}", BACK_LINK)
        .replace("{{FOOTER}}", render.footer(updated))
        # Results pages should never be crawled: they are query permutations of
        # pages that are already indexed on their own.
        .replace("{{SOCIAL}}",
                 '<meta name="robots" content="noindex,follow">\n'
                 + render.social_meta(title))
    )
    render._write(os.path.join(out_dir, "index.html"), html)
    return 1
