"""The daily match overview: /matches/ and /matches/YYYY-MM-DD.html.

Every other page on the site is organised by competition. This one is
organised by date: one page per day, listing every match played or scheduled
that day across the whole pyramid — leagues, cups and the national team —
grouped by competition. It is the only view that answers "what football is on
today", which is the question a visitor arriving cold actually has.

Shape of the thing:

  * **The homepage** is today's page. It is written by build.py in the
    homepage shell (src/home.py), with the day region from `home_day` —
    the same markup as a dated page, its date links pointing into matches/.
  * **/matches/** is today's page too, kept because it has been linked to.
    CI builds daily at 07:07 CAT, so both are right for the whole Malawi day;
    the few lines of JS at the bottom of each cover the midnight-to-build gap
    by hopping to the neighbouring date's page when the visitor's CAT date has
    moved on.
  * **/matches/YYYY-MM-DD.html** is the stable, shareable page for one date,
    in the same shell as the homepage (competition sidebar and all), so
    stepping to yesterday changes the date and nothing else. Written for
    every date that has a match, plus a contiguous window either side of
    today (WINDOW_BACK/WINDOW_FORWARD) so the day-by-day chips never
    dead-end on an empty stretch of calendar.

Unlike the league pages this reads `Dataset` directly (like src/hubs.py), so a
match in a season that is not the one currently built for its competition —
the Women's Premiership 25/26 while 26/27 runs elsewhere — still shows up on
its date. That is also why every link here is checked against a set build.py
knows was written — `match_pages` for the line itself (src/match_page.py),
`club_hub_ids` for the team names on a line that has no match page — because a
link to anything else would be a 404.
"""

from dataclasses import dataclass, field
from datetime import date as date_cls, datetime, timedelta
from html import escape
import json
import os

from . import adapt, flags, home, nt, nt_page, render

SLUG = "matches"

# How far either side of today the calendar is walkable day by day. Dates with
# matches outside this window still get a page; the empty days between them do
# not, which is what keeps the page count proportional to the football rather
# than to the length of the season.
WINDOW_BACK = 7
WINDOW_FORWARD = 30

# Cache-buster for static/calendar.js, set once by build_pages. Same pattern
# (and reason) as render.SEARCH_JS_VERSION: every page embeds the versioned
# URL, and threading it through render_day's signature would be noise.
CAL_JS_VERSION = ""

# Competition ordering inside a day, mirroring the landing page: the pinned
# order first, then anything else by (tier, name).
_COMP_ORDER = list(adapt.COMPETITION_SLUGS)

_WEEKDAYS = ("Monday", "Tuesday", "Wednesday", "Thursday", "Friday",
             "Saturday", "Sunday")
_WEEKDAYS_SHORT = ("Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun")

# Status values that replace the score with a badge rather than a fixture time.
_BADGES = {"postponed": "PPD", "cancelled": "CANC", "abandoned": "ABD"}


# ── Dates ────────────────────────────────────────────────────────────────────

def _parse(iso: str) -> date_cls:
    return datetime.strptime(iso, "%Y-%m-%d").date()


def _shift(iso: str, days: int) -> str:
    return (_parse(iso) + timedelta(days=days)).isoformat()


def full_date_label(iso: str) -> str:
    """"Wednesday 5 August 2026"."""
    d = _parse(iso)
    return f"{_WEEKDAYS[d.weekday()]} {d.day} {d.strftime('%B %Y')}"


def short_date_label(iso: str) -> str:
    """"Wed 5 Aug"."""
    d = _parse(iso)
    return f"{_WEEKDAYS_SHORT[d.weekday()]} {d.day} {d.strftime('%b')}"


def relative_label(iso: str, today: str) -> str:
    """"Today" / "Yesterday" / "Tomorrow", else "" — the day's own name."""
    return {0: "Today", -1: "Yesterday", 1: "Tomorrow"}.get(
        (_parse(iso) - _parse(today)).days, "")


def offset_label(iso: str, today: str) -> str:
    """relative_label, widened to "In 5 days" / "12 days ago".

    The date bar only ever highlights the three presets, so on any other date
    this eyebrow above the heading is what tells a visitor where they have
    walked to.
    """
    near = relative_label(iso, today)
    if near:
        return near
    n = (_parse(iso) - _parse(today)).days
    return f"In {n} days" if n > 0 else f"{-n} days ago"


def _clock(raw: str) -> str:
    """"14:30" from the sheet's "14:30" or Sheets' "14:30:00"; "" when blank.

    One normaliser for the whole site (adapt.clock): the day view reads raw
    dataset.Match rows, the competition pages read adapted MatchViews, and a
    kickoff must not read differently depending on which page you land on.
    """
    return adapt.clock(raw)


# ── Collecting a day's football ──────────────────────────────────────────────

@dataclass
class CompGroup:
    """One competition's matches on one date."""
    competition_id: str
    slug: str
    name: str                 # sponsored display name for that season
    round_label: str          # "Matchday 5" / "Semi-finals", "" when mixed
    matches: list = field(default_factory=list)   # dataset.Match, kickoff order
    sort: tuple = ()


@dataclass
class Day:
    """Everything on one date, national team included."""
    iso: str
    groups: "list[CompGroup]" = field(default_factory=list)
    nt_matches: list = field(default_factory=list)   # (NTMatch, NTTeam)

    @property
    def count(self) -> int:
        return sum(len(g.matches) for g in self.groups) + len(self.nt_matches)

    @property
    def played_count(self) -> int:
        return (sum(1 for g in self.groups for m in g.matches if _is_played(m))
                + sum(1 for m, _team in self.nt_matches if m.played))

    @property
    def competition_names(self) -> "list[str]":
        """Display names of everything on, national team last."""
        return ([g.name for g in self.groups]
                + [m.competition or team.team_name
                   for m, team in self.nt_matches])


def _round_label(comp, m) -> str:
    """"Matchday 5" for a league, "Semi-finals" for a cup, "" when unknown."""
    if comp is not None and comp.type == "cup":
        return adapt.STAGE_LABELS.get(m.stage, "")
    return f"Matchday {m.matchday}" if m.matchday else ""


def collect(ds, nt_data=None) -> "dict[str, Day]":
    """date -> Day, for every date that has at least one match.

    Placeholder rows and undated matches are simply absent — a match with no
    date belongs to no day, which is the honest rendering of "not scheduled
    yet". Pure: writes nothing, so tests can inspect it.
    """
    days: "dict[str, Day]" = {}

    by_date_comp: "dict[tuple[str, str, str], list]" = {}
    for m in ds.matches.values():
        if m.is_placeholder or not m.date:
            continue
        by_date_comp.setdefault((m.date, m.competition_id, m.season_id), []).append(m)

    for (iso, comp_id, season_id), matches in by_date_comp.items():
        comp = ds.competitions.get(comp_id)
        if comp is None:
            continue
        # Played first, then still to come — a reader opening the day should
        # see what already happened before a list of kickoff times, and
        # within each half the earlier match still sorts above the later one.
        matches.sort(key=lambda m: (not _is_played(m), _clock(m.kickoff) == "",
                                    _clock(m.kickoff), m.match_id))
        labels = {_round_label(comp, m) for m in matches}
        group = CompGroup(
            competition_id=comp_id,
            slug=adapt.competition_slug(comp_id, comp.country),
            name=ds.league_display_name(comp_id, season_id),
            round_label=labels.pop() if len(labels) == 1 else "",
            matches=matches,
            sort=(
                _COMP_ORDER.index(comp_id) if comp_id in _COMP_ORDER
                else len(_COMP_ORDER),
                comp.tier or 99,
                comp.name,
            ),
        )
        days.setdefault(iso, Day(iso)).groups.append(group)

    # The national team is a separate schema with no competition_id to group
    # by, so it gets its own block at the top of the day.
    if nt_data is not None:
        for m in nt_data.nt_matches.values():
            team = nt_data.nt_teams.get(m.team_code)
            if team is None or not m.date:
                continue
            days.setdefault(m.date, Day(m.date)).nt_matches.append((m, team))

    for day in days.values():
        day.groups.sort(key=lambda g: g.sort)
        day.nt_matches.sort(key=lambda pair: (not pair[0].played,
                                              _clock(pair[0].kickoff) == "",
                                              _clock(pair[0].kickoff),
                                              pair[0].match_id))
    return days


def page_dates(days, today: str) -> "list[str]":
    """Every date to write a page for, sorted: match dates + the walkable window."""
    dates = set(days)
    start = _parse(today) - timedelta(days=WINDOW_BACK)
    for n in range(WINDOW_BACK + WINDOW_FORWARD + 1):
        dates.add((start + timedelta(days=n)).isoformat())
    return sorted(dates)




# ── Rendering one day ────────────────────────────────────────────────────────
#
# A day is a stack of competition cards, each a header (logo, name, round —
# the whole thing a link to that competition's results) over one line per
# match: home, score-or-kickoff, away. FotMob's shape, and for its reason: a
# match is one line on a phone, so a Saturday of 29 matches is a scroll rather
# than a page of two-row table entries. The venue is the one thing that did
# not survive the move — it was a second row on every match, and it is still
# on the competition's own results page one tap away.
#
# Everything here is theme-aware (the site tokens), unlike the V2 tables it
# replaced, which stayed white in dark mode.

def _crest_finder(static_dir, css_prefix):
    """f(team) -> crest URL, legacy code first then club id (see render.py)."""
    find = render._logo_finder(static_dir, css_prefix, "clubs")

    def crest(team):
        if team is None:
            return ""
        return (team.legacy_code and find(team.legacy_code)) or find(team.club_id) or ""

    return crest


def _is_played(m) -> bool:
    """True once a league/cup match has a result to show.

    Same test `_score_cell` renders on: `status` is `played`/`awarded` (not a
    placeholder) AND both goals are set. Postponed/cancelled/abandoned stay
    "not played" — they are not a result still to come, but they are not one
    either, and a day's played-first ordering only needs the two-way split.
    """
    return m.counts_for_table and m.has_score


def _score(home, away, star=""):
    return (f'<span class="dm-score">{home}<span class="dm-dash">&ndash;</span>'
            f"{away}{star}</span>")


def _time_or_vs(raw) -> str:
    time = _clock(raw)
    if time:
        return f'<span class="dm-time day-res-time">{escape(time)}</span>'
    return '<span class="dm-vs">vs</span>'


def _score_cell(m) -> "tuple[str, list[str], bool]":
    """(middle of the row, notes for the line under it, is it a fixture).

    A fixture shows its kickoff time where a result shows the score — the one
    real departure from the league results table, and the reason to be on this
    page at all: "what time is it on" is the question of the day view.
    """
    if _is_played(m):
        star = ('<span class="dm-unconf" title="Not yet confirmed">*</span>'
                if m.confidence == "unconfirmed" else "")
        notes = []
        if m.home_pens is not None and m.away_pens is not None:
            notes.append(f"{m.home_pens}–{m.away_pens} on pens")
        if m.extra_time:
            notes.append("AET")
        return _score(m.home_goals, m.away_goals, star), notes, False
    badge = _BADGES.get(m.status, "")
    if badge:
        return f'<span class="dm-badge">{badge}</span>', [], True
    return _time_or_vs(m.kickoff), [], True


def _side(name_html, crest_img, href, side):
    """One team: name and crest, the name a link when there is a page for it.

    The crest sits on the inside, next to the score, so both columns of
    crests line up down the card however long the names are.
    """
    name = f'<span class="dm-name">{name_html}</span>'
    if href:
        name = f'<a class="dm-link" href="{escape(href)}">{name}</a>'
    inner = f"{name}{crest_img}" if side == "home" else f"{crest_img}{name}"
    return f'<span class="dm-team dm-{side}">{inner}</span>'


def _crest(url):
    if not url:
        # An empty box keeps the name the same distance from the score as the
        # rows around it that do have a crest.
        return '<span class="dm-crest" aria-hidden="true"></span>'
    return f'<img class="dm-crest" src="{escape(url)}" alt="" loading="lazy">'


def _team_side(ds, team_id, crest, club_hub_ids, side, prefix, link=True):
    team = ds.teams.get(team_id)
    name = escape(team.display_name if team else team_id)
    club_id = team.club_id if team else ""
    href = (f"{prefix}clubs/{club_id}.html"
            if link and club_id and club_id in club_hub_ids else "")
    return _side(name, _crest(crest(team)), href, side)


def _row(home, mid, away, notes, fixture, href="", label=""):
    """One line. With `href`, the whole line is a link to the match's page.

    THE LINE GOES TO THE MATCH, NOT THE CLUBS. FotMob's rule, and for its
    reason: on a phone the two names ARE the line, so names that went to the
    clubs left a 4rem strip in the middle as the only way into the match — and
    a reader tapping "Bullets 2–1 Wanderers" means that result. The clubs are
    one tap further, on the match page's scoreboard. A match with no page
    (the national team's) keeps its club links, since there is nowhere else
    for the line to go.

    It is an anchor stretched over the row rather than an anchor AROUND it,
    because the row is a grid of the <li> and the list's dividers key off
    `.dm:first-child`; the label is what a screen reader announces for it.
    """
    note = (f'<span class="dm-note">{" &middot; ".join(notes)}</span>'
            if notes else "")
    cls = "dm is-fixture" if fixture else "dm"
    go = ""
    if href:
        cls += " has-go"
        go = (f'<a class="dm-go" href="{escape(href)}" '
              f'aria-label="{escape(label)}"></a>')
    return (f'<li class="{cls}">{home}<span class="dm-mid">{mid}</span>'
            f"{away}{note}{go}</li>")


def match_row(ds, m, mid, notes, fixture, crest, club_hub_ids, prefix,
              href=""):
    """A league/cup line: both sides, the middle, and the link when there is one."""
    link = not href
    label = ""
    if href:
        home = ds.teams.get(m.home_team_id)
        away = ds.teams.get(m.away_team_id)
        score = (f" {m.home_goals}–{m.away_goals} " if _is_played(m) else " v ")
        label = ((home.display_name if home else m.home_team_id) + score
                 + (away.display_name if away else m.away_team_id))
    return _row(
        _team_side(ds, m.home_team_id, crest, club_hub_ids, "home", prefix, link),
        mid,
        _team_side(ds, m.away_team_id, crest, club_hub_ids, "away", prefix, link),
        notes, fixture, href, label)


def _match_rows(ds, group, crest, club_hub_ids, prefix, match_href=None):
    """One line per match in a competition group."""
    out = []
    for m in group.matches:
        mid, notes, fixture = _score_cell(m)
        # The round only moves onto the row when the group header could not
        # carry it (a competition playing two rounds on one date).
        if not group.round_label:
            label = _round_label(ds.competitions.get(m.competition_id), m)
            if label:
                notes.insert(0, label)
        notes = [escape(n) for n in notes]
        if m.awarded_note:
            notes.append(f"Awarded: {escape(m.awarded_note)}")
        href = match_href(m.match_id) if match_href else ""
        out.append(match_row(ds, m, mid, notes, fixture, crest, club_hub_ids,
                             prefix, href))
    return out


def _nt_rows(day, fl, prefix):
    """The national-team lines: Malawi always on the left, flags for crests.

    Same convention as /scorchers/ — a national-team line reads from one
    team's perspective, so home/away moves into the note under it.
    """
    out = []
    for m, team in day.nt_matches:
        notes = [escape(m.competition)] if m.competition else []
        if m.ground_label:
            notes.append(escape(m.ground_label))
        if m.played:
            mid, fixture = _score(m.team_score, m.opponent_score), False
            if m.score_note:
                notes.append(escape(m.score_note))
        else:
            mid, fixture = _time_or_vs(m.kickoff), True
        ours_href = (f"{prefix}{nt_page.SLUG}/"
                     if team.team_code == nt.SCORCHERS else "")
        ours = _side(escape(nt_page.SIDE_NAME),
                     fl.img_for(nt_page.OUR_COUNTRY, "dm-crest dm-flag"),
                     ours_href, "home")
        theirs = _side(escape(m.opponent),
                       fl.img_for(m.opponent, "dm-crest dm-flag") or _crest(""),
                       "", "away")
        out.append(_row(ours, mid, theirs, notes, fixture))
    return out


def _card(head_html, rows):
    return ('<section class="day-comp">' + head_html
            + '<ul class="day-list">' + "".join(rows) + "</ul></section>")


def _chip_label(iso, today) -> str:
    """"Today" / "Yesterday" / "Tomorrow" when it is one of those, else the
    day's own name ("Saturday") — so a chip always says what day it is."""
    return relative_label(iso, today) or _WEEKDAYS[_parse(iso).weekday()]


def _chip(iso, today, active, base=""):
    """One chip in the date bar; a dimmed placeholder when there is no date."""
    if not iso:
        return ('<span class="day-chip is-off" aria-hidden="true">'
                '<span class="day-chip-date">&mdash;</span></span>')
    inner = (f'<span class="day-chip-label">{escape(_chip_label(iso, today))}</span>'
             f'<span class="day-chip-date">{escape(short_date_label(iso))}</span>')
    href = f"{base}{iso}.html"
    if active:
        return (f'<a class="day-chip active" href="{href}" '
                f'aria-current="page">{inner}</a>')
    return f'<a class="day-chip" href="{href}">{inner}</a>'


# The picker's data, inlined on every page rather than fetched as one shared
# file (the pattern search.js uses). It is ~1.5KB against pages of 15–60KB,
# and buying that back would cost a network round trip on the tap that opens
# the calendar — the one moment the visitor is waiting on it. `win` is the
# walkable window, `match` the dates that have football: a date is clickable
# if it falls in the first or appears in the second, which is exactly the set
# of pages build_pages writes. `base` is where those pages are from here —
# "" on a /matches/ page, "matches/" on the homepage.
def _cal_data(iso, today, dates_with_matches, base="") -> str:
    return json.dumps({
        "sel": iso,
        "today": today,
        "win": [_shift(today, -WINDOW_BACK), _shift(today, WINDOW_FORWARD)],
        "match": dates_with_matches,
        "base": base,
    }, separators=(",", ":"))


_CAL_ICON = (
    '<svg class="day-cal-icon" viewBox="0 0 24 24" aria-hidden="true" '
    'fill="none" stroke="currentColor" stroke-width="2" '
    'stroke-linecap="round"><rect x="3" y="5" width="18" height="16" rx="2">'
    '</rect><path d="M8 3v4M16 3v4M3 10h18"></path></svg>'
)


def _date_bar(iso, today, dates, dates_with_matches, css_prefix, base=""):
    """The previous day, this day and the next, plus the calendar button.

    The chips are anchored on the date being shown, not on today: stepping
    forward re-centres them, so the row always reads "the day before, this
    day, the day after". Their labels still say Today/Yesterday/Tomorrow when
    that is what they are, and the day's own name otherwise.

    `dates` is the sorted list of dates that have a page, so a chip can never
    point at one that was not written. Inside the walkable window the
    neighbours are literally ±1 day; outside it (browsing an old season) they
    are the nearest dates that have football, which is the useful step there.
    """
    i = dates.index(iso) if iso in dates else None
    prev_d = dates[i - 1] if i is not None and i > 0 else ""
    next_d = dates[i + 1] if i is not None and i + 1 < len(dates) else ""

    chips = (_chip(prev_d, today, False, base)
             + _chip(iso, today, True, base)
             + _chip(next_d, today, False, base))

    js = f"{css_prefix}calendar.js"
    if CAL_JS_VERSION:
        js += f"?v={CAL_JS_VERSION}"

    return (
        '<nav class="day-bar" aria-label="Choose a date">'
        f'<div class="day-chips">{chips}</div>'
        '<div class="day-cal" data-day-cal-root>'
        # Ships hidden and JS reveals it: without JS it would do nothing, and
        # the chips still step a day at a time on their own.
        '<button class="day-cal-btn" type="button" data-day-cal-btn hidden '
        'aria-haspopup="dialog" aria-expanded="false" '
        f'aria-label="Pick a date">{_CAL_ICON}</button>'
        '<script type="application/json" data-day-cal>'
        f"{_cal_data(iso, today, dates_with_matches, base)}</script>"
        "</div>"
        f'<script defer src="{escape(js)}"></script>'
        "</nav>"
    )


def _nearest(dates_with_matches, iso, forward):
    """The closest date that has football, in one direction, or ""."""
    if forward:
        later = [d for d in dates_with_matches if d > iso]
        return later[0] if later else ""
    earlier = [d for d in dates_with_matches if d < iso]
    return earlier[-1] if earlier else ""


def _empty_body(iso, today, dates_with_matches, base=""):
    """"No matches", and the nearest dates either side that do have some."""
    links = []
    prev = _nearest(dates_with_matches, iso, forward=False)
    nxt = _nearest(dates_with_matches, iso, forward=True)
    if prev:
        links.append(f'<a class="day-jump-link" href="{base}{prev}.html">'
                     f"&#x2190; {escape(short_date_label(prev))}</a>")
    if nxt:
        links.append(f'<a class="day-jump-link" href="{base}{nxt}.html">'
                     f"{escape(short_date_label(nxt))} &#x2192;</a>")
    jump = (f'<p class="day-jump">{"".join(links)}</p>' if links else "")
    rel = relative_label(iso, today)
    when = rel.lower() if rel else "on this date"
    return ('<div class="day-empty"><p class="day-empty-msg">'
            f"No matches {when}.</p>" + jump + "</div>")


def _heading(iso, today, day):
    """"TODAY  Thursday 24 September" and, on the right, how much is on.

    One line rather than the old banner: the chips below already say which
    day this is, so the heading only has to say it in full — and, off in last
    season, how far from today you have walked ("41 days ago").
    """
    rel = offset_label(iso, today)
    count = ""
    if day is not None and day.count:
        n = day.count
        count = (f'<span class="day-count">{n} match{"" if n == 1 else "es"}'
                 "</span>")
    # The year only when it is not this one: it is what pushed the heading
    # onto a second line on a phone, and it says nothing in September.
    d = _parse(iso)
    label = f"{_WEEKDAYS[d.weekday()]} {d.day} {d.strftime('%B')}"
    if iso[:4] != today[:4]:
        label += f" {d.year}"
    return ('<div class="day-head"><h2 class="day-title">'
            f'<span class="day-rel">{escape(rel)}</span>'
            f"{escape(label)}</h2>{count}</div>")


def render_day(ds, day, iso, today, dates, dates_with_matches, static_dir,
               css_prefix, club_hub_ids, fl, base="", match_pages=None):
    """The day region of the page: heading, date bar, every match on it.

    `css_prefix` is the page's depth ("" on the homepage, "../" under
    /matches/); `base` is where the date pages are from here ("matches/" on
    the homepage, "" beside them). `match_pages` is match_page.match_page_ids:
    a line links to its match only where a page was written.
    """
    crest = _crest_finder(static_dir, css_prefix)
    match_href = (render.match_href_for(css_prefix, match_pages)
                  if match_pages is not None else None)
    out = [
        _heading(iso, today, day),
        _date_bar(iso, today, dates, dates_with_matches, css_prefix, base),
    ]

    if day is None or day.count == 0:
        out.append(_empty_body(iso, today, dates_with_matches, base))
        return "\n".join(out)

    out.append('<div class="day-comps">')
    if day.nt_matches:
        logo = fl.img_for(nt_page.OUR_COUNTRY, "day-comp-logo")
        head = (f'<a class="day-comp-head" href="{css_prefix}{nt_page.SLUG}/">'
                f"{logo}"
                f'<span class="day-comp-name">{escape(nt_page.DISPLAY_NAME)}</span>'
                '<span class="day-comp-arrow" aria-hidden="true">&#x203A;</span>'
                "</a>")
        out.append(_card(head, _nt_rows(day, fl, css_prefix)))

    for group in day.groups:
        logo = render._league_logo_lookup(
            static_dir, css_prefix, group.slug, group.competition_id)
        img = (f'<img class="day-comp-logo" src="{escape(logo)}" alt="">'
               if logo else '<span class="day-comp-logo" aria-hidden="true"></span>')
        round_txt = (f'<span class="day-comp-round">{escape(group.round_label)}'
                     "</span>" if group.round_label else "")
        head = (f'<a class="day-comp-head" '
                f'href="{css_prefix}{escape(group.slug)}/results.html">'
                f'{img}<span class="day-comp-name">{escape(group.name)}</span>'
                f"{round_txt}"
                '<span class="day-comp-arrow" aria-hidden="true">&#x203A;</span>'
                "</a>")
        out.append(_card(head, _match_rows(ds, group, crest, club_hub_ids,
                                           css_prefix, match_href)))
    out.append("</div>")

    if any(m.confidence == "unconfirmed" and m.counts_for_table
           for g in day.groups for m in g.matches):
        out.append('<p class="day-legend">* result not yet confirmed</p>')
    return "\n".join(out)


# A page is baked with the build's today. CI builds daily at 07:07 CAT, so
# between midnight and the build a visitor's "today" is one day ahead of the
# page. The window is contiguous around today, so today+1 always has a page —
# hop to it rather than showing yesterday's football under a heading that
# says Today. Only ever moves by a day, and only on the two "today" pages (the
# homepage and /matches/), never on a dated one somebody shared.
_TODAY_JS = """
(function(){
  var el=document.querySelector('[data-day-today]');
  if(!el) return;
  var baked=el.getAttribute('data-day-today');
  var now=new Date(Date.now()+%d*3600000).toISOString().slice(0,10);
  if(now===baked) return;
  var d=new Date(baked+'T00:00:00Z');
  d.setUTCDate(d.getUTCDate()+1);
  if(now===d.toISOString().slice(0,10)) location.replace(%s+now+'.html');
})();
"""


def today_script(today, base, tz_offset_hours) -> str:
    """The marker + midnight hop for a page that stands for "today"."""
    return (f'<div data-day-today="{today}" hidden></div>'
            f"<script>{_TODAY_JS % (tz_offset_hours, json.dumps(base))}</script>")


def set_versions(static_dir):
    """Stamp the calendar script's cache-buster before any page embeds it."""
    global CAL_JS_VERSION
    CAL_JS_VERSION = render.file_version(os.path.join(static_dir, "calendar.js"))


def home_day(ds, days, today, static_dir, club_hub_ids, fl, match_pages=None):
    """(day region, is it empty) for the homepage: today, dates under matches/."""
    dates = page_dates(days, today)
    dates_with_matches = sorted(d for d in days if days[d].count)
    day = days.get(today)
    html = render_day(ds, day, today, today, dates, dates_with_matches,
                      static_dir, "", set(club_hub_ids), fl, base=f"{SLUG}/",
                      match_pages=match_pages)
    return html, (day is None or day.count == 0)


def _page(ds, days, iso, today, dates, dates_with_matches, static_dir, updated,
          css_ver, club_hub_ids, fl, leagues_html, title, social, scripts="",
          match_pages=None):
    day = days.get(iso)
    body = render_day(ds, day, iso, today, dates, dates_with_matches,
                      static_dir, "../", club_hub_ids, fl,
                      match_pages=match_pages)
    return home.page(
        title=f"{title} · {render.SITE_NAME}", social=social,
        css_prefix="../", css_ver=css_ver, fl=fl, day_html=body,
        leagues_html=leagues_html, updated=updated,
        empty=(day is None or day.count == 0),
        scripts=scripts)


def build_pages(dist, templates_dir, static_dir, ds, updated, today,
                nt_data=None, club_hub_ids=(), tz_offset_hours=2, days=None,
                leagues_html="", nav_js="", match_pages=None):
    """Write /matches/ and one page per date. Returns (pages, dates with matches).

    `today` is the build's date in Malawi time — passed in rather than read
    here, so the whole build stamps one date and tests can pin it. `days` is
    an already-collected index (build.py needs one for the homepage before
    these pages are written); omit it and one is collected here.

    `leagues_html` is the competition list for the sidebar, already rendered
    for a page one directory down, and `nav_js` the script its tabs need.
    `templates_dir` is unused since these pages took the homepage's shell; it
    stays in the signature so callers need not change.
    """
    out_dir = os.path.join(dist, SLUG)
    os.makedirs(out_dir, exist_ok=True)
    css_ver = render.css_version(static_dir)
    set_versions(static_dir)
    fl = flags.Flags(static_dir, prefix="../")

    days = collect(ds, nt_data) if days is None else days
    dates = page_dates(days, today)
    dates_with_matches = sorted(d for d in days if days[d].count)
    club_hub_ids = set(club_hub_ids)
    scripts = f"<script>{nav_js}</script>" if nav_js else ""

    for iso in dates:
        title = f"Matches · {full_date_label(iso)}"
        html = _page(ds, days, iso, today, dates, dates_with_matches,
                     static_dir, updated, css_ver, club_hub_ids, fl,
                     leagues_html, title, render.social_meta(title), scripts,
                     match_pages)
        render._write(os.path.join(out_dir, f"{iso}.html"), html)

    # /matches/ is today's page again, with the date-drift hop. Same directory,
    # so every relative link in the body already resolves.
    title = "Matches today"
    index = _page(ds, days, today, today, dates, dates_with_matches,
                  static_dir, updated, css_ver, club_hub_ids, fl, leagues_html,
                  title,
                  render.social_meta(title, url=render.SITE_URL + f"/{SLUG}/"),
                  scripts + today_script(today, "", tz_offset_hours),
                  match_pages)
    render._write(os.path.join(out_dir, "index.html"), index)

    return len(dates) + 1, len(dates_with_matches)
