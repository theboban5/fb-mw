"""Club hub and player pages — the cross-competition views the schema unlocks.

Club hubs live at /clubs/{club_id}.html: the club's header, each of its
squads with its current competition + table position, and recent results
across every competition. Player pages live at /players/{player_id}.html:
goals by season and competition. Both reuse the site's existing CSS classes
so they inherit the league pages' styling.
"""

from dataclasses import dataclass, field
from html import escape
import os

from . import adapt, dataset, lineups, render, scorers

RECENT_RESULTS = 10

HOME_BACK = '<a href="../" class="back-link">&#x2190; All Leagues</a>'

# The back link on a player page, which is the one page on this site nobody
# arrives at from the top.
#
# WHAT WAS WRONG. A profile is reached by tapping a name on a team sheet, and
# the only way out of it was "All Leagues" — the home page. Someone comparing
# three players in one line-up had to walk back in through the competition, the
# results tab, the right matchday and the right match, three times. The browser
# already knew the answer; the page just refused to offer it.
#
# So the link goes back when there is somewhere to go back TO, and to the home
# page otherwise. The href is the home page either way, which is what a reader
# with no JavaScript gets, what a crawler follows, and what someone who landed
# here from a shared link or a search result gets — for them there is no
# previous page on this site and "Back" would be a lie.
#
# The referrer test is deliberately same-origin only: arriving from Facebook or
# Google, history.back() would leave the site entirely.
PLAYER_BACK = (
    '<a href="../" class="back-link" data-player-back>&#x2190; All Leagues</a>'
    "<script>(function(){"
    "var a=document.querySelector('[data-player-back]');"
    "if(!a)return;"
    "var r=document.referrer;"
    "if(!r||r.indexOf(location.origin+'/')!==0)return;"
    "if(r.split('#')[0]===location.href.split('#')[0])return;"
    "a.innerHTML='\\u2190 Back';"
    "a.addEventListener('click',function(e){e.preventDefault();history.back();});"
    "})();</script>"
)


def _page(base, title, content, updated, css_ver, header_logo="",
          back=HOME_BACK):
    return (
        # A hub belongs to no single competition, so there is no second half to
        # the <title> — just the club or player name.
        base.replace("{{TITLE}}", escape(title))
        .replace("{{LEAGUE_NAME}}", escape(title))
        .replace("{{LEAGUE_LOGO}}", header_logo)
        .replace("{{LAST_UPDATED}}", escape(updated))
        .replace("{{NAV}}", render._nav("", items=(("../", "Home"),)))
        .replace("{{SEARCH}}", render.search_widget("../"))
        .replace("{{CONTENT}}", content)
        .replace("{{CSS_PREFIX}}", "../")
        .replace("{{CSS_VER}}", css_ver)
        .replace("{{BACK_LINK}}", back)
        .replace("{{FOOTER}}", render.footer(updated))
        .replace("{{SOCIAL}}", render.social_meta(title))
    )


# ── Club hubs ────────────────────────────────────────────────────────────────

def _club_result_row(m, league, goals_by_match=None, official_pages=None):
    """One compact result row from the club's perspective, with league tag.

    Carries the scorers and the line-up toggle too. This hub is where someone
    following one club actually lands — it is the page a club's own supporters
    share — so anything that renders on the competition's Matches tab and not
    here reads as the feature being broken rather than as being somewhere else.

    WHAT WAS WRONG. The line-up block was added here and the scorer block was
    not, which produced the one arrangement that makes no sense: a match on
    this page listing all twenty-two names and not saying who scored, while the
    same match one page over said both.
    """
    home_name = league.teams[m.home_code].name if m.home_code in league.teams else m.home_code
    away_name = league.teams[m.away_code].name if m.away_code in league.teams else m.away_code
    home = escape(home_name)
    away = escape(away_name)
    score_cell, fix_cls = render._score_cell(m)
    date = escape(render._format_date(m.date))
    # Same order as every other match caption on the site: date, kickoff, then
    # the competition in place of the results table's venue.
    bits = [b for b in (date, escape(m.kickoff_label), escape(league.league_name)) if b]
    meta = " &middot; ".join(bits)
    home_sheet, away_sheet = league.lineups.get(m.match_id, (None, None))
    scorers_html = render._scorers_block(m, goals_by_match)
    return (
        f'<tr class="v2-res-meta-row"><td colspan="3">'
        f'<span class="v2-res-meta">{meta}</span></td></tr>'
        f'<tr class="v2-res-row v2-res-row-compact{fix_cls}">'
        f'<td class="v2-res-home">{home}</td>'
        f'{score_cell}'
        f'<td class="v2-res-away">{away}</td></tr>'
        + (f'<tr class="v2-scorers-row"><td colspan="3">{scorers_html}</td></tr>'
           if scorers_html else "")
        + lineups.two_sided_row_html(
            home_sheet, away_sheet, home_name, away_name,
            player_href=render.player_href_for("../"),
            officials=m.officials,
            official_href=render.official_href_for("../", official_pages))
    )


def render_club_hub(club, club_teams, crest_url, goals_by_slug=None,
                    official_pages=None):
    """The hub page body for one club.

    `club_teams` is a list of (team, league, standing, position, played,
    recent_matches, code) tuples — one per squad that is entered in a built
    league. `code` is that squad's team code within its league, used to
    match the `#club-team-{code}` fragment a league page links in with
    (see build.py) so the row for the team the user came from can be
    highlighted via the CSS :target selector.
    """
    crest_img = (
        f'<img class="v2-mini-logo" src="{escape(crest_url)}" alt="">' if crest_url else ""
    )
    place = ", ".join(x for x in (club.city, club.region) if x)

    v2 = [
        '<div class="v2-content">',
        '<div class="v2-mini-banner">',
        crest_img,
        f'<p class="v2-season">{escape(place.upper())}</p>' if place else "",
        f'<h2 class="v2-mini-league">{escape(club.name.upper())}</h2>',
        "</div>",  # /v2-mini-banner
        f'<h3 class="v2-sec-title">All {escape(club.name)} Teams</h3>',
    ]

    if club_teams:
        rows = []
        for team, league, standing, position, _played, _recent, code in club_teams:
            pos_txt = (
                f"{render._ordinal(position)} &middot; {standing.points} pts"
                if standing is not None and position is not None else "&ndash;"
            )
            # Cups emit no per-competition club pages, so their row links to
            # the competition itself instead of a clubs/ URL that never exists.
            # `code` is this squad's key in league.teams — the same key
            # render.py names its per-league club pages after, and the only
            # one guaranteed to resolve. legacy_code is blank for teams added
            # under the new schema (all of srfa2/crfa2), so keying off it
            # silently missed and fell back to the raw team id.
            team_href = (f"../{league.slug}/" if league.kind == "cup"
                         else f"../{league.slug}/clubs/{code}.html")
            league_href = f"../{league.slug}/"
            # Only league rows carry the highlight id: a squad can hold the
            # same code in a league and a cup, and only league pages link in
            # with the #club-team-{code} fragment (see build.py).
            row_id = (f' id="club-team-{escape(code)}"'
                      if league.kind != "cup" else "")
            rows.append(
                f'<tr class="v2-res-row"{row_id}>'
                f'<td class="v2-res-home"><a class="club-link" href="{escape(team_href)}">'
                f'{escape(league.teams[code].name)}</a></td>'
                f'<td class="v2-res-away"><a class="club-link" href="{escape(league_href)}">'
                f'{escape(league.league_name)}</a></td>'
                f'<td class="v2-res-venue">{pos_txt}</td></tr>'
            )
        v2 += [
            '<div class="v2-results-outer">',
            '<table class="v2-results-table">',
            '<thead><tr><th class="v2-res-th-home">TEAM</th>'
            '<th class="v2-res-th-away">COMPETITION</th>'
            '<th class="v2-res-th-venue">POSITION</th></tr></thead>',
            "<tbody>", *rows, "</tbody></table></div>",
            # Highlights the row for window.__clubTeamTarget (set in
            # templates/base.html before the hash could scroll the page to
            # it). Runs inline, after the rows above, so the target already
            # exists in the DOM.
            "<script>(function(){"
            "var id=window.__clubTeamTarget;if(!id)return;"
            "var el=document.getElementById(id);"
            "if(el)el.classList.add('v2-current-team');"
            "})();</script>",
        ]
    else:
        v2.append('<p class="v2-empty">No teams in a current competition.</p>')

    # Recent results across all of the club's competitions, newest first.
    all_recent = []
    for _team, league, _standing, _position, _played, recent, _code in club_teams:
        all_recent += [(m.date or "", m, league) for m in recent]
    all_recent.sort(key=lambda x: x[0], reverse=True)
    all_recent = all_recent[:RECENT_RESULTS]

    v2.append('<h3 class="v2-sec-title">Recent Results</h3>')
    if all_recent:
        body = "".join(
            _club_result_row(m, league, (goals_by_slug or {}).get(league.slug),
                             official_pages)
            for _d, m, league in all_recent)
        v2 += [
            '<div class="v2-results-outer">',
            '<table class="v2-results-table v2-results-compact">',
            '<thead><tr><th class="v2-res-th-home">HOME</th>'
            '<th class="v2-res-th-score">RESULT</th>'
            '<th class="v2-res-th-away">AWAY</th></tr></thead>',
            f"<tbody>{body}</tbody></table></div>",
        ]
    else:
        v2.append('<p class="v2-empty">No results yet.</p>')

    v2.append("</div>")  # /v2-content
    return "\n".join(v2)


def build_club_hubs(dist, templates_dir, static_dir, ds, leagues, standings_by_slug,
                    updated, official_pages=None):
    """Write /clubs/{club_id}.html for every club with a team in a built league.

    `leagues` is the list of LeagueData that were built; `standings_by_slug`
    maps slug -> the computed standings rows for that league.
    """
    base = render._read(os.path.join(templates_dir, "base.html"))
    css_ver = render.css_version(static_dir)
    out_dir = os.path.join(dist, "clubs")
    os.makedirs(out_dir, exist_ok=True)

    # A team can be in several built competitions at once (a league and a
    # cup), so this maps team_id -> every (league, code) it appears in; the
    # hub then lists one row per competition, not one per squad.
    leagues_of_team = {}
    for league in leagues:
        for code, tv in league.teams.items():
            leagues_of_team.setdefault(tv.team_id, []).append((league, code))

    crest = render._crest_lookup(static_dir, "../")

    # Who scored, per competition. render._scorers_block takes the same
    # {match_id: [GoalView]} the league pages are built with, so this hub shows
    # the identical line rather than a second implementation of it.
    goals_by_slug = {league.slug: scorers.goals_by_match(league.goals)
                     for league in leagues if league.goals}

    count = 0
    for club in ds.clubs.values():
        club_teams = []
        for team in ds.teams.values():
            if team.club_id != club.club_id:
                continue
            for league, code in leagues_of_team.get(team.team_id, []):
                rows = standings_by_slug.get(league.slug, [])
                standing = next((s for s in rows if s.code == code), None)
                position = next(
                    (i for i, s in enumerate(rows, start=1) if s.code == code), None)
                played = [m for m in league.matches
                          if code in (m.home_code, m.away_code) and m.played]
                played.sort(key=lambda m: (m.date, m.matchday), reverse=True)
                club_teams.append(
                    (team, league, standing, position, len(played),
                     played[:RECENT_RESULTS], code))
        if not club_teams:
            continue

        crest_url = crest(club.club_id) or next(
            (crest(t.legacy_code) for t, *_ in club_teams if t.legacy_code and crest(t.legacy_code)),
            None)
        content = render_club_hub(club, club_teams, crest_url or "",
                                  goals_by_slug, official_pages)
        html = _page(base, club.name, content, updated, css_ver)
        render._write(os.path.join(out_dir, f"{club.club_id}.html"), html)
        count += 1
    return count


# ── Player pages ─────────────────────────────────────────────────────────────

def player_goal_credits(ds):
    """(credits, own_goals) — who gets a player page, and what is on it.

    `credits` is player_id -> (season_id, competition_id) -> goals; `own_goals`
    is player_id -> count. CAF_MW_UNKNOWN and goals from placeholder matches
    are skipped. Split out of build_player_pages because src/search.py has to
    index exactly the set of players that gets a page — deriving that twice is
    how a search result ends up pointing at a 404.
    """
    credits = {}
    own_goals = {}
    for g in ds.goals.values():
        if g.player_id == dataset.UNKNOWN_PLAYER_ID:
            continue
        m = ds.matches.get(g.match_id)
        if m is None or m.is_placeholder:
            continue
        key = (m.season_id, m.competition_id)
        if g.is_own_goal:
            own_goals[g.player_id] = own_goals.get(g.player_id, 0) + 1
        else:
            credits.setdefault(g.player_id, {})
            credits[g.player_id][key] = credits[g.player_id].get(key, 0) + 1
    return credits, own_goals


@dataclass(frozen=True)
class Appearance:
    """One player in one match: what the match-stats table shows as a row.

    Display-ready on purpose. A profile mixes club football (from `Dataset`)
    with national-team football (from `NTData`), and those two schemas share no
    ids at all — a league match has a competition_id and two team_ids, an
    nt_matches row has a competition NAME and an opponent NAME. Resolving both
    to labels at the point they are read is what lets one table render them
    side by side without knowing which it is looking at.
    """
    date: str
    competition: str      # label, not an id
    opponent: str         # label, not an id
    team_label: str       # the side this player turned out for
    club_id: str          # for the club-hub link; "" for a national team
    # The side, as an id. `club_id` cannot stand in for it: a club's men's and
    # women's teams share one club_id, and "the other players in this squad"
    # must not mix them. A national team uses its own team_code — a different
    # namespace, which is exactly why grouping by this string is safe.
    team_id: str
    national: bool
    home: bool
    started: bool
    minute_on: str
    minute_off: str
    captain: bool
    shirt_number: str
    position: str
    goals: int
    assists: int
    # Named exactly as the lineups tab names them: lineups.cards_html reads
    # these attributes straight off the row, and a shorter name here would mean
    # a second copy of the card markup.
    yellow_card: bool
    yellow_red_card: bool
    red_card: bool
    scoreline: str        # "2-1", the player's own side first
    outcome: str          # W | D | L, or "" when the match has no score
    # False for an unused substitute. They are held in Career.bench rather than
    # in appearances — a bench call is not a game played — but the match still
    # belongs on their page, so the row has to know which it is. Defaulted
    # true: an Appearance is an appearance unless it says otherwise.
    played: bool = True
    # 0028. Man of the match, straight off the team sheet row. Shown on the
    # match table and deliberately NOT counted in the summary tiles yet: a
    # total means something only once enough matches carry one, and on the day
    # this shipped none of them did.
    motm: bool = False

    @property
    def sort_key(self):
        # Newest first, and a match with no date sorts last rather than first:
        # a blank date is an unscheduled fixture, not the dawn of time.
        return (self.date != "", self.date)


@dataclass
class Career:
    """Everything a profile page shows, computed once per player.

    `appearances` is matches PLAYED. `bench` is matches they were named in and
    did not play — kept apart rather than merged, because "games played" must
    not quietly become "games named in a squad", and kept at all rather than
    dropped, because a bench name is still a real player with a real id and
    their name is clickable on the team sheet.
    """
    appearances: "list[Appearance]"     # newest first
    goals: int
    assists: int
    bench: "list[Appearance]" = field(default_factory=list)

    @property
    def starts(self) -> int:
        return sum(1 for a in self.appearances if a.started)

    @property
    def sub_apps(self) -> int:
        return sum(1 for a in self.appearances if not a.started)

    @property
    def yellows(self) -> int:
        return sum(1 for a in self.appearances if a.yellow_card or a.yellow_red_card)

    @property
    def reds(self) -> int:
        return sum(1 for a in self.appearances if a.red_card or a.yellow_red_card)

    @property
    def latest(self) -> "Appearance | None":
        """The most recent appearance FOR A CLUB, else the most recent of any.

        The header names a team, and "Civil Service United" is a more useful
        answer to "who does she play for" than "Malawi" — a national team is
        something you are picked for, not somewhere you play. A player who has
        only ever been an unused substitute falls through to `bench`, so their
        page still says which club named them.
        """
        club = [a for a in self.appearances if not a.national]
        return (club or self.appearances or self.bench or [None])[0]


def _outcome(ours, theirs) -> "tuple[str, str]":
    """("2-1", "W") from one side's perspective, or ("", "") with no score."""
    if ours is None or theirs is None:
        return "", ""
    return (f"{ours}-{theirs}",
            "W" if ours > theirs else ("D" if ours == theirs else "L"))


def _playable(row) -> bool:
    """Did they actually play? An unused substitute did not.

    They still get a page — their name is clickable on the team sheet and a
    real player should have somewhere to click TO — but they are counted
    separately, because letting them into "games played" would make it mean
    "games named in a squad", a different and much less interesting number.
    """
    return row.role != "unused_sub"


def _identified(player_id: str) -> bool:
    return bool(player_id) and player_id != dataset.UNKNOWN_PLAYER_ID


def _club_appearances(ds):
    """player_id -> [Appearance] from `lineups`, `goals` and their assists."""
    goals_by, assists_by = {}, {}
    for g in ds.goals.values():
        m = ds.matches.get(g.match_id)
        if m is None or m.is_placeholder:
            continue
        if _identified(g.player_id) and not g.is_own_goal:
            key = (g.player_id, g.match_id)
            goals_by[key] = goals_by.get(key, 0) + 1
        if g.assist_player_id:
            key = (g.assist_player_id, g.match_id)
            assists_by[key] = assists_by.get(key, 0) + 1

    out, bench = {}, {}
    for r in ds.lineups:
        if not _identified(r.player_id):
            continue
        m = ds.matches.get(r.match_id)
        if m is None or m.is_placeholder:
            continue
        home = r.team_id == m.home_team_id
        team = ds.teams.get(r.team_id)
        club = ds.clubs.get(team.club_id) if team else None
        scoreline, outcome = _outcome(
            m.home_goals if home else m.away_goals,
            m.away_goals if home else m.home_goals)
        opponent = ds.teams.get(m.away_team_id if home else m.home_team_id)
        into = out if _playable(r) else bench
        into.setdefault(r.player_id, []).append(Appearance(
            date=m.date,
            competition=ds.league_display_name(m.competition_id, m.season_id),
            opponent=opponent.display_name if opponent else "",
            team_label=(team.display_name if team else r.team_id),
            club_id=club.club_id if club else "", team_id=r.team_id,
            national=False, home=home, started=r.role == "starting",
            minute_on=r.minute_on, minute_off=r.minute_off,
            captain=r.captain, shirt_number=r.shirt_number, position=r.position,
            goals=goals_by.get((r.player_id, r.match_id), 0),
            assists=assists_by.get((r.player_id, r.match_id), 0),
            played=_playable(r), motm=getattr(r, "motm", False),
            yellow_card=r.yellow_card, yellow_red_card=r.yellow_red_card,
            red_card=r.red_card, scoreline=scoreline, outcome=outcome,
        ))
    return out, bench, goals_by, assists_by


def _national_appearances(ntd):
    """The same, from the nt_* tabs — our own sides only.

    An opponent's rows carry ids from no registry (INT_LIB_KOSIAH), so they
    resolve to nobody and contribute nothing. That is not a gap: this site
    holds one match of a Liberian international's career and has no business
    publishing a profile of them.
    """
    if ntd is None:
        return {}, {}, {}, {}
    ours = set(ntd.nt_teams)
    goals_by, assists_by = {}, {}
    for g in ntd.nt_goals.values():
        if g.team_id not in ours:
            continue
        if _identified(g.player_id) and not g.is_own_goal:
            key = (g.player_id, g.match_id)
            goals_by[key] = goals_by.get(key, 0) + 1
        if g.assist_player_id:
            key = (g.assist_player_id, g.match_id)
            assists_by[key] = assists_by.get(key, 0) + 1

    out, bench = {}, {}
    for r in ntd.nt_lineups:
        if r.team_id not in ours or not _identified(r.player_id):
            continue
        m = ntd.nt_matches.get(r.match_id)
        if m is None:
            continue
        scoreline, outcome = _outcome(m.team_score, m.opponent_score)
        team = ntd.nt_teams.get(r.team_id)
        into = out if _playable(r) else bench
        into.setdefault(r.player_id, []).append(Appearance(
            date=m.date, competition=m.competition, opponent=m.opponent,
            team_label=team.team_name if team else r.team_id,
            club_id="", team_id=r.team_id, national=True, home=m.home_away == "home",
            started=r.role == "starting",
            minute_on=r.minute_on, minute_off=r.minute_off,
            captain=r.captain, shirt_number=r.shirt_number, position=r.position,
            goals=goals_by.get((r.player_id, r.match_id), 0),
            assists=assists_by.get((r.player_id, r.match_id), 0),
            played=_playable(r), motm=getattr(r, "motm", False),
            yellow_card=r.yellow_card, yellow_red_card=r.yellow_red_card,
            red_card=r.red_card, scoreline=scoreline, outcome=outcome,
        ))
    return out, bench, goals_by, assists_by


def player_careers(ds, ntd=None):
    """player_id -> Career, club football and international football together.

    That union is the whole point of giving the national-team tabs canonical
    ids in 0020: before it, the same person was two unrelated strings and no
    page could show both halves of their season.

    Everything is derived from `lineups`/`goals` and their nt_ counterparts; no
    tab is hand-maintained for it, so a profile cannot fall out of step with
    the match pages it summarises. A goal by a player with no line-up row still
    counts — most of this dataset predates team sheets entirely, so a scorer
    with no sheet is the normal case rather than an error.
    """
    club_apps, club_bench, club_goals, club_assists = _club_appearances(ds)
    nt_apps, nt_bench, nt_goals, nt_assists = _national_appearances(ntd)

    goals_by = {**club_goals, **nt_goals}
    assists_by = {**club_assists, **nt_assists}

    player_ids = set(club_apps) | set(nt_apps) | set(club_bench) | set(nt_bench)
    player_ids |= {pid for pid, _mid in goals_by}
    player_ids |= {pid for pid, _mid in assists_by}

    goal_totals, assist_totals = {}, {}
    for (pid, _mid), n in goals_by.items():
        goal_totals[pid] = goal_totals.get(pid, 0) + n
    for (pid, _mid), n in assists_by.items():
        assist_totals[pid] = assist_totals.get(pid, 0) + n

    careers = {}
    for player_id in player_ids:
        appearances = club_apps.get(player_id, []) + nt_apps.get(player_id, [])
        appearances.sort(key=lambda a: a.sort_key, reverse=True)
        bench = club_bench.get(player_id, []) + nt_bench.get(player_id, [])
        bench.sort(key=lambda a: a.sort_key, reverse=True)
        careers[player_id] = Career(
            appearances=appearances,
            goals=goal_totals.get(player_id, 0),
            assists=assist_totals.get(player_id, 0),
            bench=bench,
        )
    return careers


def player_page_ids(ds, credits=None, own_goals=None, careers=None, ntd=None):
    """Exactly the set of players a page is written for.

    src/search.py indexes this same set — deriving it twice is how a search
    result ends up pointing at a 404, which is why both callers come here.
    A player earns a page by having done something: a goal, an own goal, an
    assist, or an appearance on a team sheet. Everyone else is a `players` row
    with nothing to put on a page.
    """
    if credits is None or own_goals is None:
        credits, own_goals = player_goal_credits(ds)
    if careers is None:
        careers = player_careers(ds, ntd)
    ids = set(credits) | set(own_goals) | set(careers)
    return {pid for pid in ids if pid in ds.players}


# How many match-stat rows show before the rest are folded away. A <details>
# rather than a "show more" link, so the whole career is in the page for search
# and for a reader who taps once, without twenty seasons of table on first
# paint. Same idea as the scorer tables' cutoff.
MATCH_ROWS_SHOWN = 15


def _stat_tile(value, label) -> str:
    return (f'<div class="pl-tile"><span class="pl-tile-n">{value}</span>'
            f'<span class="pl-tile-l">{escape(label)}</span></div>')


def _summary_tiles(career) -> str:
    """Apps / Starts / Goals / Assists / Cards, and only what is non-zero.

    A player with no team sheet anywhere would otherwise get a row of four
    zeroes under their name, which reads as "this player did nothing" rather
    than "nobody has entered a line-up for these matches yet".
    """
    tiles = []
    if career.appearances:
        tiles.append(_stat_tile(len(career.appearances), "Apps"))
        tiles.append(_stat_tile(career.starts, "Starts"))
    elif career.bench:
        # Named and not used. Worth a tile of its own rather than a zero under
        # "Apps", which would read as a player who turned out and did nothing.
        tiles.append(_stat_tile(len(career.bench), "On the bench"))
    if career.goals or career.appearances:
        tiles.append(_stat_tile(career.goals, "Goals"))
    if career.assists:
        tiles.append(_stat_tile(career.assists, "Assists"))
    if career.yellows:
        tiles.append(_stat_tile(career.yellows, "Yellow"))
    if career.reds:
        tiles.append(_stat_tile(career.reds, "Red"))
    if not tiles:
        return ""
    return f'<div class="pl-tiles">{"".join(tiles)}</div>'


def _profile_header(player, career, ds, club_hub_ids) -> str:
    """Name, and whatever else is actually known — never a placeholder.

    players.position, .dob and .nationality are empty for every row in this
    dataset today, so each line here has to disappear rather than render an
    em-dash. The shirt, the position and the club come from the most recent
    team sheet instead, which is where that information actually lives.
    """
    latest = career.latest
    bits = []
    if latest and latest.shirt_number:
        bits.append(f'<span class="pl-shirt">#{escape(latest.shirt_number)}</span>')
    position = (latest.position if latest and latest.position else player.position)
    if position:
        bits.append(f'<span class="pl-pos">{escape(position.upper())}</span>')

    club_line = ""
    if latest:
        name = escape(latest.team_label)
        club_line = (
            f'<p class="pl-club"><a class="club-link" '
            f'href="../clubs/{escape(latest.club_id)}.html">{name}</a></p>'
            if latest.club_id and latest.club_id in club_hub_ids
            else f'<p class="pl-club">{name}</p>')

    meta = f'<p class="pl-meta">{" ".join(bits)}</p>' if bits else ""
    return (
        '<div class="v2-mini-banner">'
        '<p class="v2-season">PLAYER</p>'
        f'<h2 class="v2-mini-league">{escape(player.display_name.upper())}</h2>'
        f"{meta}{club_line}"
        "</div>"
    )


def _match_stat_row(a, show_side=False) -> str:
    """One match on the profile: what happened, in the order it is asked about.

    Deliberately not the site's results row: the question on a profile is "how
    did THIS player do", so the opponent is one column and the player's own
    goals, assists and cards are the rest. The competition and date share a
    caption line above, exactly as the compact results table does, which is
    what keeps six columns inside a phone's width.
    """
    date = render._format_date(a.date) if a.date else ""
    # The side is named only when the career actually moves between sides —
    # a club and the national team, or two clubs. On a player who has only
    # ever turned out for one, repeating it on every row is pure noise.
    comp = f"{escape(a.team_label)} &middot; {escape(a.competition)}" \
        if show_side else escape(a.competition)
    caption = " &middot; ".join(b for b in (escape(date), comp) if b)

    # An unused substitute was there and did not play, which is a different
    # statement from "started" or "came on" and has to read as one. Their goal
    # and assist columns are empty by construction.
    if not a.played:
        role = '<span class="pl-dnp" title="Named as a substitute, did not play">DNP</span>'
    else:
        role = "XI" if a.started else "SUB"
        if a.started and a.minute_off:
            role += f' <span class="pl-min">&darr;{escape(a.minute_off)}\'</span>'
        elif not a.started and a.minute_on:
            role += f' <span class="pl-min">&uarr;{escape(a.minute_on)}\'</span>'
        if a.captain:
            role += lineups.captain_badge(is_captain=True)
    # Outside the branch above on purpose: it is a fact about the match, not
    # about how much of it they played, and the same badge in the same place
    # as on the team sheet is what makes it recognisable in a column of rows.
    role += lineups.motm_badge(a.motm)

    cards = lineups.cards_html(a)
    score = (f'<span class="pl-res pl-res-{a.outcome.lower()}">'
             f'{escape(a.outcome)}</span> {escape(a.scoreline)}'
             if a.outcome else escape(a.scoreline))

    return (
        f'<tr class="pl-cap-row"><td colspan="5">'
        f'<span class="v2-res-meta">{caption}</span></td></tr>'
        f'<tr class="pl-match-row">'
        f'<td class="pl-opp">{"" if a.home else "@ "}{escape(a.opponent)}</td>'
        f'<td class="pl-score">{score}</td>'
        f'<td class="pl-role">{role}{cards}</td>'
        f'<td class="pl-ga">{a.goals or ""}</td>'
        f'<td class="pl-ga">{a.assists or ""}</td>'
        "</tr>"
    )


SWITCH_PLAYER_MAX = 30


def squads_by_team(careers, page_ids):
    """team_id -> [(name, player_id, shirt)] for everyone who has a page.

    Derived from the careers already computed rather than from `lineups`
    directly, for the reason the whole player-page set is derived from one
    function: a name listed here that has no page written for it is a 404, and
    the only defence against that is asking the same question the page writer
    asked. Bench rows count — an unused substitute is not an appearance, but
    they are in the squad, they have a page, and a reader flicking through a
    team sheet expects to find them.

    The shirt is the one from their most recent match for THAT side, which is
    also the one their own profile header shows.
    """
    best: "dict[tuple[str, str], tuple]" = {}
    for player_id, career in careers.items():
        if player_id not in page_ids:
            continue
        for a in career.appearances + career.bench:
            if not a.team_id:
                continue
            key = (a.team_id, player_id)
            if key not in best or a.sort_key > best[key][0]:
                best[key] = (a.sort_key, a.shirt_number)
    out: "dict[str, list]" = {}
    for (team_id, player_id), (_key, shirt) in best.items():
        out.setdefault(team_id, []).append((player_id, shirt))
    return out


def _switch_player(player_id, career, squads, ds) -> str:
    """The other players in this player's squad, one tap away.

    WHAT WAS WRONG. Every route to a profile goes through a team sheet, and the
    thing a reader wants next is almost always another name on that same sheet
    — who else played, who scored, who came on. Getting there meant leaving the
    profile and finding the match again.

    One squad, not all of them: the side from `career.latest`, which is the
    same side the header names. A player who has moved club still reaches the
    old squad through the match-stats table below, and listing every squad
    they have ever been in would bury the current one.
    """
    latest = career.latest
    if latest is None:
        return ""
    mates = [(pid, shirt) for pid, shirt in squads.get(latest.team_id, [])
             if pid != player_id]
    if not mates:
        return ""

    def _name(pid):
        player = ds.players.get(pid)
        return player.display_name if player else pid

    mates.sort(key=lambda pair: _name(pair[0]).lower())
    links = "".join(
        f'<a class="pl-switch-link" href="{escape(pid)}.html">'
        + (f'<span class="pl-switch-shirt">{escape(shirt)}</span>' if shirt else "")
        + f"{escape(_name(pid))}</a>"
        for pid, shirt in mates[:SWITCH_PLAYER_MAX])
    return (
        '<h3 class="v2-sec-title">Switch Player</h3>'
        f'<p class="pl-switch-team">{escape(latest.team_label)}</p>'
        f'<div class="pl-switch">{links}</div>'
    )


def _bench_note(career) -> str:
    """The one line a bench-only page has to say.

    Their name is clickable on the team sheet, so they need a page; a page with
    a name and nothing else reads as broken. This says what is actually true.
    """
    if career.appearances or not career.bench:
        return ""
    n = len(career.bench)
    return (f'<p class="v2-res-legend">Named as a substitute in {n} match'
            f'{"es" if n != 1 else ""} without coming on. No appearances yet.</p>')


def _match_stats(career) -> str:
    """Every match on this player's record, played or not, newest first.

    WHAT WAS WRONG. The table listed `appearances` only, so a player who came
    on in one match and sat unused in another had a page showing one match and
    no trace of the other — and the sheet they were named on links here, so the
    reader arrives from the very match the page has decided not to mention.
    Being an unused substitute is a fact about a career; it is just not an
    appearance, which is what the DNP row and the untouched tiles say.
    """
    rows_for = sorted(career.appearances + career.bench,
                      key=lambda a: a.sort_key, reverse=True)
    if not rows_for:
        return ""
    head = (
        '<thead><tr>'
        '<th class="pl-th-opp">OPPONENT</th>'
        '<th class="pl-th-score">RES</th>'
        '<th class="pl-th-role">ROLE</th>'
        '<th class="pl-th-ga">G</th>'
        '<th class="pl-th-ga">A</th>'
        "</tr></thead>"
    )
    show_side = len({a.team_label for a in rows_for}) > 1
    rows = [_match_stat_row(a, show_side) for a in rows_for]
    shown, hidden = rows[:MATCH_ROWS_SHOWN], rows[MATCH_ROWS_SHOWN:]
    out = [
        '<h3 class="v2-sec-title">Match Stats</h3>',
        '<div class="v2-table-outer">'
        f'<table class="v2-standings pl-matches">{head}'
        f'<tbody>{"".join(shown)}</tbody></table></div>',
    ]
    if hidden:
        out.append(
            '<details class="pl-more">'
            f'<summary>{len(hidden)} earlier match'
            f'{"es" if len(hidden) != 1 else ""}</summary>'
            '<div class="v2-table-outer">'
            f'<table class="v2-standings pl-matches">{head}'
            f'<tbody>{"".join(hidden)}</tbody></table></div></details>'
        )
    return "".join(out)


def build_player_pages(dist, templates_dir, static_dir, ds, updated,
                       club_hub_ids=frozenset(), ntd=None):
    """Write /players/{player_id}.html for everyone who has done something.

    Until team sheets existed a page meant a goal, so a player with 30
    appearances and no goals had no page at all and no way to be found. The set
    is now goals + own goals + assists + appearances — see player_page_ids,
    which src/search.py uses for exactly the same set.

    Three sections: what is known about the player, a summary of their career,
    and the per-match table. Goals by competition stays underneath, unchanged.
    """
    base = render._read(os.path.join(templates_dir, "base.html"))
    css_ver = render.css_version(static_dir)
    out_dir = os.path.join(dist, "players")
    os.makedirs(out_dir, exist_ok=True)

    credits, own_goals = player_goal_credits(ds)
    careers = player_careers(ds, ntd)
    page_ids = player_page_ids(ds, credits, own_goals, careers)
    squads = squads_by_team(careers, page_ids)
    empty_career = Career(appearances=[], goals=0, assists=0)

    count = 0
    for player_id in sorted(page_ids):
        player = ds.players[player_id]
        name = player.display_name
        career = careers.get(player_id, empty_career)
        by_comp = credits.get(player_id, {})

        rows = []
        total = 0
        for (season_id, competition_id), n in sorted(
                by_comp.items(),
                key=lambda kv: (ds.seasons[kv[0][0]].start_date, kv[0][1]),
                reverse=True):
            season = ds.seasons[season_id]
            comp_name = ds.league_display_name(competition_id, season_id)
            slug = adapt.competition_slug(
                competition_id, ds.competitions[competition_id].country)
            total += n
            rows.append(
                f'<tr><td class="scr-player">{escape(season.label)}</td>'
                f'<td class="scr-team"><a class="club-link" href="../{escape(slug)}/">'
                f'{escape(comp_name)}</a></td>'
                f'<td class="scr-goals">{n}</td></tr>'
            )
        if total:
            rows.append(
                '<tr class="scr-og-row"><td class="scr-player">Total</td>'
                f'<td class="scr-team"></td><td class="scr-goals">{total}</td></tr>'
            )

        og_note = ""
        if own_goals.get(player_id):
            n = own_goals[player_id]
            og_note = (f'<p class="v2-res-legend">{n} own goal'
                       f'{"s" if n != 1 else ""} (not counted above).</p>')

        # This table counts LEAGUE goals only. On a player whose goals are all
        # internationals it would otherwise read "No goals recorded" directly
        # under a tile saying 5 — so with nothing to say, it says nothing.
        goals_section = ""
        if rows or own_goals.get(player_id):
            table = (
                '<div class="v2-table-outer">'
                '<table class="v2-standings scorers-table">'
                '<thead><tr><th class="scr-th-player">SEASON</th>'
                '<th class="scr-th-team">COMPETITION</th>'
                '<th class="scr-th-goals">GOALS</th></tr></thead>'
                f'<tbody>{"".join(rows)}</tbody></table></div>'
                if rows else '<p class="v2-empty">No goals recorded.</p>'
            )
            goals_section = ('<h3 class="v2-sec-title">Goals by Competition</h3>'
                             + table + og_note)

        content = "\n".join(part for part in [
            '<div class="v2-content">',
            _profile_header(player, career, ds, club_hub_ids),
            _summary_tiles(career),
            _bench_note(career),
            _match_stats(career),
            goals_section,
            _switch_player(player_id, career, squads, ds),
            "</div>",
        ] if part)
        html = _page(base, name, content, updated, css_ver, back=PLAYER_BACK)
        render._write(os.path.join(out_dir, f"{player_id}.html"), html)
        count += 1
    return count
