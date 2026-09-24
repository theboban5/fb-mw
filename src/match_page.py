"""One page per match — /match/{match_id}.html.

Every other view of a match on this site is a LINE in a list: a row on the
day view, a row on a competition's Matches tab, a row on a club hub. Those
are the right shape for "what happened on Saturday" and the wrong one for the
thing people actually do with a single result, which is send it to someone.
There was no URL that meant "this match" — the nearest was a results page
opened on whichever matchday the pager defaulted to, with the match somewhere
in it. This is that URL, and the page behind it answers the two questions a
link to one match raises: what happened (score, scorers, team sheets) and,
for a fixture, what to expect (form, the table, previous meetings).

WHICH MATCHES GET ONE. `match_page_ids` is the single source, for the reason
`hubs.player_page_ids` is: the day view, the results pages and the club hubs
all link here, every one of those links is rendered before these pages are
written, and deriving the set twice is how a link 404s. It is every
non-placeholder match in a competition+season the build renders — the same
matches that have a row on a Matches tab. National-team matches are a
separate schema with their own page (/scorchers/) and get none.

NOTHING HERE IS NEW DATA. Every section is a fact the build already had, cut
per match: the score and scorers from the league view, the team sheets from
`lineups`, the table from standings.py, and the form and head-to-head read
off `ds.matches` by team_id — the one join that holds across seasons and
competitions, which is what a previous meeting in last season's cup needs.

Graceful degradation is the whole layout. Only 33 of ~950 played matches
have a team sheet, most have no scorers, many no venue or kickoff: each
section renders only when it has something in it, so the typical page is a
scoreboard, the table and the previous meetings, and that is a fine page.

The link preview is the site's own image. A per-match card was costed and
declined (~40 MB of PNGs a build); the <title> names the match, which is what
a chat client shows above the logo anyway.
"""

from functools import lru_cache
from html import escape
import os

from . import adapt, hubs, lineups, matches_page, render, scorers

SLUG = "match"

# How much history a page carries. Five is what every form guide shows,
# including this site's own table; five meetings is enough to say who usually
# wins without the section outgrowing the match it is about.
FORM_N = 5
H2H_SHOWN = 5


def match_page_ids(ds) -> "set[str]":
    """Exactly the set of matches a page is written for.

    The competition+season rows the build renders (adapt's choice, not a
    second copy of it), minus placeholders — which is the set of matches that
    have a row on some competition's Matches tab.
    """
    built = {(cs.competition_id, cs.season_id)
             for cs in adapt.current_competition_seasons(ds)}
    return {m.match_id for m in ds.matches.values()
            if not m.is_placeholder
            and (m.competition_id, m.season_id) in built}


# ── History off the dataset ──────────────────────────────────────────────────

def _is_result(m) -> bool:
    """A match with a score that counts — what form and meetings are made of."""
    return m.counts_for_table and m.has_score


def _results_by_team(ds) -> "dict[str, list]":
    """team_id -> every played match it was in, oldest first."""
    out: "dict[str, list]" = {}
    for m in ds.matches.values():
        if not _is_result(m):
            continue
        out.setdefault(m.home_team_id, []).append(m)
        out.setdefault(m.away_team_id, []).append(m)
    for lst in out.values():
        lst.sort(key=_order)
    return out


def _order(m):
    return (m.date or "", m.matchday or 0, m.match_id)


def _before(other, m) -> bool:
    """Did `other` happen before `m`, as far as the data can say?

    STRICTLY EARLIER DATE, when both have one. A match on the same day is not
    "before" — two sides do not play twice in a day, and a result from the
    same afternoon is not form going into this one. An undated fixture has
    not happened, so everything played is before it; an undated RESULT (a
    matchday-only league) falls back to the matchday within one competition,
    and to nothing at all across two.
    """
    if other.match_id == m.match_id:
        return False
    if m.date and other.date:
        return other.date < m.date
    if not _is_result(m):
        return True
    if (other.competition_id, other.season_id) == (m.competition_id, m.season_id):
        return (other.matchday or 0) < (m.matchday or 0)
    return False


def _outcome(m, team_id) -> str:
    ours = m.home_goals if m.home_team_id == team_id else m.away_goals
    theirs = m.away_goals if m.home_team_id == team_id else m.home_goals
    return "W" if ours > theirs else ("D" if ours == theirs else "L")


def form_before(results_by_team, m, team_id, n=FORM_N) -> list:
    """The last `n` results `team_id` had before `m`, oldest first."""
    return [o for o in results_by_team.get(team_id, []) if _before(o, m)][-n:]


def meetings_before(results_by_team, m) -> list:
    """Every earlier played match between these two teams, newest first.

    Any competition, any season — a cup tie two years ago is a meeting. Read
    by team_id, which is stable across seasons, never by name.
    """
    pair = {m.home_team_id, m.away_team_id}
    return sorted(
        (o for o in results_by_team.get(m.home_team_id, [])
         if {o.home_team_id, o.away_team_id} == pair and _before(o, m)),
        key=_order, reverse=True)


# ── Markup ───────────────────────────────────────────────────────────────────

class _Page:
    """What every section of one build's match pages shares."""

    def __init__(self, ds, static_dir, club_hub_ids, player_pages,
                 official_pages, page_ids):
        self.ds = ds
        self.club_hub_ids = set(club_hub_ids)
        self.page_ids = page_ids
        self.results_by_team = _results_by_team(ds)
        self.player_href = render.player_href_for("../", player_pages)
        self.official_href = render.official_href_for("../", official_pages)
        self.match_href = render.match_href_for("../", page_ids)
        # A thousand pages each asking the filesystem for the same forty
        # crests is forty thousand stat calls; the answer never changes.
        self.crest = lru_cache(maxsize=None)(
            matches_page._crest_finder(static_dir, "../"))
        self.static_dir = static_dir

    def team(self, team_id):
        return self.ds.teams.get(team_id)

    def team_name(self, team_id) -> str:
        t = self.team(team_id)
        return t.display_name if t else team_id

    def club_href(self, team_id) -> str:
        t = self.team(team_id)
        if t and t.club_id and t.club_id in self.club_hub_ids:
            return f"../clubs/{t.club_id}.html"
        return ""

    def crest_img(self, team_id, cls) -> str:
        url = self.crest(self.team(team_id))
        if not url:
            return f'<span class="{cls}" aria-hidden="true"></span>'
        return f'<img class="{cls}" src="{escape(url)}" alt="">'


def _headline(p, dm) -> str:
    """"Bullets 2–1 Wanderers" or "Bullets v Wanderers" — the page's name."""
    home, away = p.team_name(dm.home_team_id), p.team_name(dm.away_team_id)
    if _is_result(dm):
        return f"{home} {dm.home_goals}–{dm.away_goals} {away}"
    return f"{home} v {away}"


def _status(dm) -> "tuple[str, str]":
    """(big middle, the line under it) for the scoreboard."""
    if _is_result(dm):
        mid = (f'<span class="mp-score">{dm.home_goals}'
               f'<span class="mp-dash">&ndash;</span>{dm.away_goals}</span>')
        bits = []
        if dm.home_pens is not None and dm.away_pens is not None:
            bits.append(f"{dm.home_pens}&ndash;{dm.away_pens} on pens")
        if dm.extra_time:
            bits.append("After extra time")
        if dm.status == "awarded":
            bits.insert(0, "Awarded")
        elif not bits:
            bits.append("Full time")
        if dm.confidence == "unconfirmed":
            bits.append("not yet confirmed")
        return mid, " &middot; ".join(bits)
    words = {"postponed": "Postponed", "cancelled": "Cancelled",
             "abandoned": "Abandoned"}
    if dm.status in words:
        return (f'<span class="mp-badge">{words[dm.status]}</span>', "")
    time = adapt.clock(dm.kickoff)
    if time:
        return (f'<span class="mp-time">{escape(time)}</span>',
                escape(matches_page.short_date_label(dm.date)) if dm.date else "")
    return ('<span class="mp-vs">vs</span>',
            escape(matches_page.short_date_label(dm.date)) if dm.date else "")


def _board_side(p, team_id, side) -> str:
    name = f'<span class="mp-name">{escape(p.team_name(team_id))}</span>'
    inner = p.crest_img(team_id, "mp-crest") + name
    href = p.club_href(team_id)
    if href:
        return f'<a class="mp-team mp-{side}" href="{escape(href)}">{inner}</a>'
    return f'<span class="mp-team mp-{side}">{inner}</span>'


def _goal_line(p, g) -> str:
    """One scorer, linked when they have a page; minute and marker after."""
    href = p.player_href(g.player_id)
    name = escape(g.player_name)
    if href:
        name = f'<a class="mp-link" href="{escape(href)}">{name}</a>'
    minute = f" {escape(g.minute)}&#x2019;" if g.minute else ""
    mark = " (P)" if g.is_penalty else (" (OG)" if g.is_own_goal else "")
    return f'<li>{name}<span class="mp-min">{minute}{mark}</span></li>'


def _scorers(p, mv, goals) -> str:
    """Scorers under the score, each under the side the goal counted for.

    Same filing as the results table's scorer block (`GoalView.team_code` is
    the beneficiary, so an own goal sits under the side it helped, marked OG).
    """
    if not goals:
        return ""
    home = [g for g in goals if g.team_code == mv.home_code]
    away = [g for g in goals if g.team_code == mv.away_code]
    if not home and not away:
        return ""

    def col(side, gs):
        return (f'<ul class="mp-goals mp-goals-{side}">'
                + "".join(_goal_line(p, g) for g in gs) + "</ul>")
    return f'<div class="mp-scorers">{col("home", home)}{col("away", away)}</div>'


def _facts(p, dm, group) -> str:
    """Date, kick-off, venue — each only when known."""
    rows = []
    if dm.date:
        rows.append(("Date", escape(matches_page.full_date_label(dm.date))))
    time = adapt.clock(dm.kickoff)
    if time:
        rows.append(("Kick-off", f"{escape(time)} {adapt.KICKOFF_TZ}"))
    venue = p.ds.venues.get(dm.venue_id)
    if venue:
        rows.append(("Venue", escape(venue.name)))
    if group:
        rows.append(("Group", escape(group)))
    if dm.awarded_note:
        rows.append(("Awarded", escape(dm.awarded_note)))
    if not rows:
        return ""
    return ('<dl class="mp-facts">' + "".join(
        f'<div class="mp-fact"><dt>{k}</dt><dd>{v}</dd></div>' for k, v in rows)
        + "</dl>")


_SHARE_JS = """
(function(){
  var b=document.querySelector('[data-share]');
  if(!b) return;
  var url=location.href.split('#')[0], t=b.getAttribute('data-share');
  var l=b.querySelector('.mp-share-l');
  if(navigator.share){
    b.hidden=false;
    b.addEventListener('click',function(){
      navigator.share({title:t,text:t,url:url}).catch(function(){});
    });
  } else if(navigator.clipboard&&window.isSecureContext){
    b.hidden=false; l.textContent='Copy link';
    b.addEventListener('click',function(){
      navigator.clipboard.writeText(url).then(function(){
        l.textContent='Link copied';
      },function(){});
    });
  }
})();
"""


def _share(text) -> str:
    """The one control on the page, and the reason for it.

    Ships `hidden` and JS reveals it, the carousel dots' bargain: the phone's
    own share sheet where there is one (which is where WhatsApp is), a copy
    button where there is only a clipboard, and nothing at all otherwise —
    never a button that does nothing. The address bar still works either way.
    """
    return (f'<button class="mp-share" type="button" data-share="{escape(text)}" '
            'hidden><svg class="mp-share-i" viewBox="0 0 24 24" aria-hidden="true" '
            'fill="none" stroke="currentColor" stroke-width="2" '
            'stroke-linecap="round" stroke-linejoin="round">'
            '<path d="M12 3v12M7 8l5-5 5 5M5 13v6a2 2 0 0 0 2 2h10a2 2 0 0 0 2-2v-6">'
            '</path></svg><span class="mp-share-l">Share</span></button>'
            f"<script>{_SHARE_JS}</script>")


def _board(p, dm, mv, league, goals, group) -> str:
    comp_logo = render._league_logo_lookup(
        p.static_dir, "../", league.slug, league.competition_id)
    logo = (f'<img class="mp-comp-logo" src="{escape(comp_logo)}" alt="">'
            if comp_logo else "")
    label = matches_page._round_label(p.ds.competitions.get(dm.competition_id), dm)
    # Inside the name's span, so a long sponsored name wraps as one sentence
    # rather than leaving the round stranded in a column of its own.
    rnd = (f'<span class="mp-round"> &middot; {escape(label)}</span>'
           if label else "")
    mid, under = _status(dm)
    under_html = f'<span class="mp-under">{under}</span>' if under else ""
    return (
        '<section class="mp-board">'
        f'<a class="mp-comp" href="../{escape(league.slug)}/results.html">'
        f'{logo}<span class="mp-comp-name">{escape(league.league_name)}'
        f"{rnd}</span></a>"
        '<div class="mp-teams">'
        f'{_board_side(p, dm.home_team_id, "home")}'
        f'<div class="mp-mid">{mid}{under_html}</div>'
        f'{_board_side(p, dm.away_team_id, "away")}'
        "</div>"
        f"{_scorers(p, mv, goals)}"
        f"{_facts(p, dm, group)}"
        f'<div class="mp-actions">{_share(_headline(p, dm) + " · " + league.league_name)}</div>'
        "</section>"
    )


def _lineups(p, dm, mv, home_sheet, away_sheet) -> str:
    """Both team sheets, open, side by side where there is room.

    The results table keeps them in a collapsed drawer because they are one
    match among twenty; here they are the match, so nothing is folded away.
    Built from the same pieces `two_sided_row_html` is, minus the table row.
    """
    parts = []
    officials = mv.officials
    for sheet, team_id, is_home in ((home_sheet, dm.home_team_id, True),
                                    (away_sheet, dm.away_team_id, False)):
        name = p.team_name(team_id)
        body = lineups.lineup_body(sheet, p.player_href)
        coach = (lineups.coach_html(*officials.coach_for(is_home),
                                    official_href=p.official_href)
                 if officials else "")
        if body or coach:
            parts.append(f'<div class="el-lineup-side">'
                         f'<p class="el-lineup-side-name">{escape(name)}</p>'
                         f"{body}{coach}</div>")
    crew = lineups.officials_html(officials, p.official_href)
    if not parts and not crew:
        return ""
    title = "Line-ups" if parts else "Match officials"
    sides = f'<div class="mp-sides">{"".join(parts)}</div>' if parts else ""
    return (f'<section class="mp-sec"><h3 class="v2-sec-title">{title}</h3>'
            f'<div class="mp-sheet el-lineup">{sides}{crew}</div></section>')


def _form_badge(p, o, team_id) -> str:
    r = _outcome(o, team_id)
    opp = p.team_name(o.away_team_id if o.home_team_id == team_id
                      else o.home_team_id)
    label = f"{r} {o.home_goals}–{o.away_goals} v {opp}"
    badge = (f'<span class="v2-form-badge v2-form-{r.lower()}">{r}</span>')
    href = p.match_href(o.match_id)
    if href:
        return (f'<a class="mp-form-a" href="{escape(href)}" '
                f'title="{escape(label)}" aria-label="{escape(label)}">{badge}</a>')
    return f'<span title="{escape(label)}">{badge}</span>'


def _form(p, dm) -> str:
    """Each side's last five results going into this match, oldest first.

    Every competition counts — a cup defeat on Wednesday is form going into
    Saturday — and each badge is a link to the match it stands for.
    """
    rows = []
    for team_id in (dm.home_team_id, dm.away_team_id):
        recent = form_before(p.results_by_team, dm, team_id)
        if not recent:
            continue
        badges = "".join(_form_badge(p, o, team_id) for o in recent)
        rows.append(
            '<li class="mp-form-row">'
            f'{p.crest_img(team_id, "mp-form-crest")}'
            f'<span class="mp-form-name">{escape(p.team_name(team_id))}</span>'
            f'<span class="mp-form-badges">{badges}</span></li>')
    if not rows:
        return ""
    heading = "Form going in" if _is_result(dm) else "Form"
    return (f'<section class="mp-sec"><h3 class="v2-sec-title">{heading}</h3>'
            f'<ul class="mp-form">{"".join(rows)}</ul></section>')


def _table(p, dm, mv, league, rows) -> str:
    """The competition's table as it stands, both sides highlighted.

    The table TODAY, not as it was before kick-off: this is the page a reader
    opens to see what the result did, and a snapshot from before it would be
    a second table disagreeing with the Standings tab one tap away. A cup has
    no table and gets none. In a competition played in clusters it is this
    match's cluster only — the other three tables say nothing about it.
    """
    if league.kind == "cup" or not rows:
        return ""
    ours = {mv.home_code, mv.away_code}
    group = next((s.group for s in rows if s.code == mv.home_code), "")
    subset = [s for s in rows if s.group == group]
    if not ours & {s.code for s in subset}:
        return ""
    body = []
    for s in subset:
        gd = f"+{s.gd}" if s.gd > 0 else str(s.gd)
        team = league.teams.get(s.code)
        team_id = team.team_id if team else ""
        name = f"{p.crest_img(team_id, 'club-crest crest-pre')}{escape(s.name)}"
        href = p.club_href(team_id)
        if href:
            name = f'<a class="club-link" href="{escape(href)}">{name}</a>'
        cls = ' class="mp-hl"' if s.code in ours else ""
        body.append(
            f"<tr{cls}><td class=\"v2-pos\">{s.position}</td>"
            f'<td class="v2-team-name">{name}</td>'
            f"<td>{s.played}</td><td class=\"v2-gd\">{gd}</td>"
            f'<td class="v2-pts">{s.points}</td></tr>')
    title = "Table" + (f" &middot; {escape(group)}" if group else "")
    return (
        f'<section class="mp-sec"><h3 class="v2-sec-title">{title}</h3>'
        '<div class="v2-table-outer"><table class="v2-standings mp-table">'
        '<thead><tr><th class="v2-th-pos">#</th><th class="v2-th-team">TEAM</th>'
        '<th>P</th><th>DIFF</th><th class="v2-th-pts">PTS</th></tr></thead>'
        f'<tbody>{"".join(body)}</tbody></table></div>'
        f'<p class="mp-more"><a href="../{escape(league.slug)}/">Full table &#x203A;</a></p>'
        "</section>")


def _meeting_row(p, o) -> str:
    """One previous meeting, in the day view's line — and, like it, a link."""
    mid, notes, fixture = matches_page._score_cell(o)
    comp = p.ds.league_display_name(o.competition_id, o.season_id)
    notes = [escape(n) for n in notes]
    notes.insert(0, escape(comp))
    if o.date:
        notes.insert(0, escape(render._format_date(o.date)))
    href = p.match_href(o.match_id)
    return matches_page.match_row(
        p.ds, o, mid, notes, fixture, p.crest, p.club_hub_ids, "../", href)


def _meetings(p, dm) -> str:
    """Head-to-head: the record, then the most recent meetings."""
    past = meetings_before(p.results_by_team, dm)
    if not past:
        return ""
    home, away = dm.home_team_id, dm.away_team_id
    h = sum(1 for o in past if _outcome(o, home) == "W")
    a = sum(1 for o in past if _outcome(o, away) == "W")
    d = len(past) - h - a

    def tile(n, label):
        return (f'<div class="mp-h2h-t"><span class="mp-h2h-n">{n}</span>'
                f'<span class="mp-h2h-l">{escape(label)}</span></div>')

    record = ('<div class="mp-h2h">'
              + tile(h, p.team_name(home)) + tile(d, "Drawn")
              + tile(a, p.team_name(away)) + "</div>")
    rows = "".join(_meeting_row(p, o) for o in past[:H2H_SHOWN])
    n = len(past)
    note = (f'<p class="mp-more">{n} meeting{"" if n == 1 else "s"} on record'
            "</p>" if n > H2H_SHOWN else "")
    return ('<section class="mp-sec"><h3 class="v2-sec-title">Head to head</h3>'
            f'{record}<div class="day-comp"><ul class="day-list">{rows}</ul></div>'
            f"{note}</section>")


def render_match(p, mv, league, rows, goals) -> str:
    """The body of one match page."""
    dm = p.ds.matches[mv.match_id]
    home_sheet, away_sheet = league.lineups.get(mv.match_id, (None, None))
    group = league.groups.get(mv.home_code) or league.groups.get(mv.away_code, "")
    parts = [
        '<div class="v2-content mp">',
        _board(p, dm, mv, league, goals, group),
        _lineups(p, dm, mv, home_sheet, away_sheet),
        _form(p, dm),
        _table(p, dm, mv, league, rows),
        _meetings(p, dm),
        "</div>",
    ]
    return "\n".join(x for x in parts if x)


def build_pages(dist, templates_dir, static_dir, ds, leagues, standings_by_slug,
                updated, club_hub_ids=(), player_pages=None, official_pages=None,
                page_ids=None):
    """Write /match/{match_id}.html for every match in a built competition.

    Returns the number written. `page_ids` is match_page_ids(ds), passed in so
    the links rendered earlier and the pages written here are one set.
    """
    if page_ids is None:
        page_ids = match_page_ids(ds)
    base = render._read(os.path.join(templates_dir, "base.html"))
    css_ver = render.css_version(static_dir)
    out_dir = os.path.join(dist, SLUG)
    os.makedirs(out_dir, exist_ok=True)
    p = _Page(ds, static_dir, club_hub_ids, player_pages, official_pages,
              page_ids)

    count = 0
    for league in leagues:
        by_match = scorers.goals_by_match(league.goals) if league.goals else {}
        rows = standings_by_slug.get(league.slug, [])
        for mv in league.matches:
            if mv.match_id not in page_ids:
                continue
            content = render_match(p, mv, league, rows,
                                   by_match.get(mv.match_id, []))
            title = f"{_headline(p, ds.matches[mv.match_id])} · {league.league_name}"
            # The player page's back link: this page is reached from a list
            # (a day, a results tab, a club), and back is where the reader
            # wants to go — the home page only when they arrived from outside.
            html = hubs._page(base, title, content, updated, css_ver,
                              back=hubs.PLAYER_BACK)
            render._write(os.path.join(out_dir, f"{mv.match_id}.html"), html)
            count += 1
    return count
