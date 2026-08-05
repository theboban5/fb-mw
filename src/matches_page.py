"""The daily match overview: /matches/ and /matches/YYYY-MM-DD.html.

Every other page on the site is organised by competition. This one is
organised by date: one page per day, listing every match played or scheduled
that day across the whole pyramid — leagues, cups and the national team —
grouped by competition. It is the only view that answers "what football is on
today", which is the question a visitor arriving cold actually has.

Shape of the thing:

  * **/matches/** is today's page, baked at build time. CI builds daily at
    07:07 CAT, so it is right for the whole Malawi day; the eight lines of JS
    at the bottom of the page cover the midnight-to-build gap by hopping to
    the neighbouring date's page when the visitor's CAT date has moved on.
  * **/matches/YYYY-MM-DD.html** is the stable, shareable page for one date.
    Written for every date that has a match, plus a contiguous window either
    side of today (WINDOW_BACK/WINDOW_FORWARD) so the day-by-day chips never
    dead-end on an empty stretch of calendar.

The date bar is three chips anchored on the date being shown — the day before,
that day, the day after — so stepping forward re-centres them. Their labels
read Today/Yesterday/Tomorrow when that is what they are and the weekday
otherwise. Inside the window the neighbours are literally ±1 day; outside it
(browsing last season) they are the nearest dates that have football.

Beside them sits the calendar button, so two weeks back is one tap rather than
fourteen. The picker is the only part that needs JavaScript
(static/calendar.js) and it ships hidden until that file reveals it —
everything else is an <a href>, and the chips alone still walk the calendar a
day at a time with script off.

Unlike the league pages this reads `Dataset` directly (like src/hubs.py), so a
match in a season that is not the one currently built for its competition —
the Women's Premiership 25/26 while 26/27 runs elsewhere — still shows up on
its date. That is also why team names link to the cross-competition club hub
and only when the hub exists: `club_hub_ids` is the set build.py knows was
written, and a link to anything else would be a 404.
"""

from dataclasses import dataclass, field
from datetime import date as date_cls, datetime, timedelta
from html import escape
import json
import os

from . import adapt, flags, nt, nt_page, render

SLUG = "matches"

BACK_LINK = '<a href="../" class="back-link">&#x2190; All Leagues</a>'

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

    The kickoff column has been in the matches tab all along but nothing
    rendered it, so nothing normalised it either — both forms are in the data.
    """
    raw = (raw or "").strip()
    if not raw or raw.lower() in ("tbd", "tba"):
        return ""
    parts = raw.split(":")
    return f"{parts[0]:0>2}:{parts[1]}" if len(parts) >= 2 else raw


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
        matches.sort(key=lambda m: (_clock(m.kickoff) == "", _clock(m.kickoff),
                                    m.match_id))
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
        day.nt_matches.sort(key=lambda pair: (_clock(pair[0].kickoff) == "",
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

def _crest_finder(static_dir, css_prefix):
    """f(team) -> crest URL, legacy code first then club id (see render.py)."""
    find = render._logo_finder(static_dir, css_prefix, "clubs")

    def crest(team):
        if team is None:
            return ""
        return (team.legacy_code and find(team.legacy_code)) or find(team.club_id) or ""

    return crest


def _score_cell(m) -> "tuple[str, str]":
    """(RESULT cell, row modifier class) for one dataset.Match.

    A fixture shows its kickoff time where a result shows the score — the one
    real departure from the league results table, and the reason to be on this
    page at all: "what time is it on" is the question of the day view.
    """
    if m.counts_for_table and m.has_score:
        star = ('<span class="v2-res-unconf">*</span>'
                if m.confidence == "unconfirmed" else "")
        parts = []
        if m.home_pens is not None and m.away_pens is not None:
            parts.append(f"{m.home_pens}–{m.away_pens} pens")
        if m.extra_time:
            parts.append("AET")
        note = (f'<span class="v2-res-note">({escape(", ".join(parts))})</span>'
                if parts else "")
        return (f'<td class="v2-res-score">{m.home_goals}:{m.away_goals}{star}'
                f"{note}</td>"), ""
    badge = _BADGES.get(m.status, "")
    if badge:
        return (f'<td class="v2-res-score v2-res-badge">{badge}</td>',
                " v2-res-row-fixture")
    time = _clock(m.kickoff)
    if time:
        return (f'<td class="v2-res-score day-res-time">{escape(time)}</td>',
                " v2-res-row-fixture")
    return '<td class="v2-res-score v2-res-vs">vs</td>', " v2-res-row-fixture"


def _team_cell(ds, team_id, crest, club_hub_ids, side):
    """One side of a match: name + crest, linked to the club hub when there is one."""
    team = ds.teams.get(team_id)
    name = escape(team.display_name if team else team_id)
    img = render._crest_img(crest(team),
                            "crest-post" if side == "home" else "crest-pre")
    inner = f"{name}{img}" if side == "home" else f"{img}{name}"
    club_id = team.club_id if team else ""
    if club_id and club_id in club_hub_ids:
        return (f'<a class="club-link" href="../clubs/{escape(club_id)}.html">'
                f"{inner}</a>")
    return inner


def _match_rows(ds, group, crest, club_hub_ids, venues):
    """The meta + result row pair for every match in one competition group."""
    out = []
    for j, m in enumerate(group.matches):
        alt = " alt" if j % 2 == 1 else ""
        meta_bits = []
        # The round only moves onto the row when the group header could not
        # carry it (a competition playing two rounds on one date).
        if not group.round_label:
            label = _round_label(ds.competitions.get(m.competition_id), m)
            if label:
                meta_bits.append(escape(label))
        venue = venues.get(m.venue_id)
        if venue is not None and venue.name:
            meta_bits.append(escape(venue.name))
        if m.awarded_note:
            meta_bits.append(f"Awarded: {escape(m.awarded_note)}")
        if meta_bits:
            out.append(
                f'<tr class="v2-res-meta-row{alt}"><td colspan="3">'
                f'<span class="v2-res-meta">{" &middot; ".join(meta_bits)}</span>'
                f"</td></tr>"
            )
        score_cell, fix_cls = _score_cell(m)
        out.append(
            f'<tr class="v2-res-row v2-res-row-compact{fix_cls}{alt}">'
            f'<td class="v2-res-home">'
            f'{_team_cell(ds, m.home_team_id, crest, club_hub_ids, "home")}</td>'
            f"{score_cell}"
            f'<td class="v2-res-away">'
            f'{_team_cell(ds, m.away_team_id, crest, club_hub_ids, "away")}</td>'
            f"</tr>"
        )
    return out


def _nt_rows(day, fl):
    """The national-team block: Malawi always on the left, flags for crests.

    Same convention as /scorchers/ — a national-team line reads from one
    team's perspective, so home/away moves into the caption.
    """
    out = []
    for j, (m, team) in enumerate(day.nt_matches):
        alt = " alt" if j % 2 == 1 else ""
        meta_bits = [escape(m.competition)] if m.competition else []
        meta_bits.append(escape(m.ground_label))
        if m.venue_label:
            meta_bits.append(escape(m.venue_label))
        out.append(
            f'<tr class="v2-res-meta-row{alt}"><td colspan="3">'
            f'<span class="v2-res-meta">{" &middot; ".join(meta_bits)}</span>'
            f"</td></tr>"
        )
        if m.played:
            note = (f'<span class="v2-res-note">{escape(m.score_note)}</span>'
                    if m.score_note else "")
            score = (f'<td class="v2-res-score">{m.team_score}:'
                     f"{m.opponent_score}{note}</td>")
            fix_cls = ""
        else:
            time = _clock(m.kickoff)
            score = (f'<td class="v2-res-score day-res-time">{escape(time)}</td>'
                     if time else
                     '<td class="v2-res-score v2-res-vs">vs</td>')
            fix_cls = " v2-res-row-fixture"
        ours = (f'{escape(nt_page.SIDE_NAME)}'
                f'{fl.img_for(nt_page.OUR_COUNTRY, "nt-flag nt-flag-post")}')
        if team.team_code == nt.SCORCHERS:
            ours = f'<a class="club-link" href="../{nt_page.SLUG}/">{ours}</a>'
        out.append(
            f'<tr class="v2-res-row v2-res-row-compact{fix_cls}{alt}">'
            f'<td class="v2-res-home">{ours}</td>'
            f"{score}"
            f'<td class="v2-res-away">'
            f'{fl.img_for(m.opponent, "nt-flag nt-flag-pre")}'
            f"{escape(m.opponent)}</td></tr>"
        )
    return out


def _chip_label(iso, today) -> str:
    """"Today" / "Yesterday" / "Tomorrow" when it is one of those, else the
    day's own name ("Saturday") — so a chip always says what day it is."""
    return relative_label(iso, today) or _WEEKDAYS[_parse(iso).weekday()]


def _chip(iso, today, active):
    """One chip in the date bar; a dimmed placeholder when there is no date."""
    if not iso:
        return ('<span class="day-chip is-off" aria-hidden="true">'
                '<span class="day-chip-date">&mdash;</span></span>')
    inner = (f'<span class="day-chip-label">{escape(_chip_label(iso, today))}</span>'
             f'<span class="day-chip-date">{escape(short_date_label(iso))}</span>')
    if active:
        return (f'<a class="day-chip active" href="{iso}.html" '
                f'aria-current="page">{inner}</a>')
    return f'<a class="day-chip" href="{iso}.html">{inner}</a>'


# The picker's data, inlined on every page rather than fetched as one shared
# file (the pattern search.js uses). It is ~1.5KB against pages of 15–60KB,
# and buying that back would cost a network round trip on the tap that opens
# the calendar — the one moment the visitor is waiting on it. `win` is the
# walkable window, `match` the dates that have football: a date is clickable
# if it falls in the first or appears in the second, which is exactly the set
# of pages build_pages writes.
def _cal_data(iso, today, dates_with_matches) -> str:
    return json.dumps({
        "sel": iso,
        "today": today,
        "win": [_shift(today, -WINDOW_BACK), _shift(today, WINDOW_FORWARD)],
        "match": dates_with_matches,
    }, separators=(",", ":"))


_CAL_ICON = (
    '<svg class="day-cal-icon" viewBox="0 0 24 24" aria-hidden="true" '
    'fill="none" stroke="currentColor" stroke-width="2" '
    'stroke-linecap="round"><rect x="3" y="5" width="18" height="16" rx="2">'
    '</rect><path d="M8 3v4M16 3v4M3 10h18"></path></svg>'
)


def _date_bar(iso, today, dates, dates_with_matches, css_prefix):
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

    chips = (_chip(prev_d, today, False)
             + _chip(iso, today, True)
             + _chip(next_d, today, False))

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
        f"{_cal_data(iso, today, dates_with_matches)}</script>"
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


def _empty_body(iso, dates_with_matches):
    links = []
    prev = _nearest(dates_with_matches, iso, forward=False)
    nxt = _nearest(dates_with_matches, iso, forward=True)
    if prev:
        links.append(f'<a class="club-link" href="{prev}.html">'
                     f"&#x2190; {escape(short_date_label(prev))}</a>")
    if nxt:
        links.append(f'<a class="club-link" href="{nxt}.html">'
                     f"{escape(short_date_label(nxt))} &#x2192;</a>")
    jump = (f'<p class="day-jump">{" &middot; ".join(links)}</p>'
            if links else "")
    return ('<p class="v2-empty">No matches on this date.</p>' + jump)


def render_day(ds, day, iso, today, dates, dates_with_matches, static_dir,
               css_prefix, club_hub_ids, fl):
    """The page body for one date."""
    eyebrow = escape(offset_label(iso, today).upper())
    crest = _crest_finder(static_dir, css_prefix)
    venues = ds.venues

    out = [
        '<div class="v2-content">',
        '<div class="v2-mini-banner">',
        f'<p class="v2-season">{eyebrow}</p>',
        f'<h2 class="v2-mini-league">{escape(full_date_label(iso).upper())}</h2>',
        "</div>",
        _date_bar(iso, today, dates, dates_with_matches, css_prefix),
    ]

    if day is None or day.count == 0:
        out.append(_empty_body(iso, dates_with_matches))
        out.append("</div>")
        return "\n".join(out)

    n = day.count
    comps = len(day.groups) + (1 if day.nt_matches else 0)
    out.append(
        f'<p class="day-summary">{n} match{"" if n == 1 else "es"} '
        f'&middot; {comps} competition{"" if comps == 1 else "s"}</p>'
    )
    out.append('<div class="v2-results-outer">')
    out.append('<table class="v2-results-table v2-results-compact">')
    out.append('<thead><tr><th class="v2-res-th-home">HOME</th>'
               '<th class="v2-res-th-score">RESULT</th>'
               '<th class="v2-res-th-away">AWAY</th></tr></thead>')

    if day.nt_matches:
        out.append('<tbody class="day-group">')
        out.append('<tr class="v2-md-row day-comp-row"><td colspan="3">'
                   '<span class="day-comp-head">'
                   f'<a class="day-comp-link" href="../{nt_page.SLUG}/">'
                   f"{escape(nt_page.DISPLAY_NAME.upper())}</a>"
                   "</span></td></tr>")
        out += _nt_rows(day, fl)
        out.append("</tbody>")

    for group in day.groups:
        logo = render._league_logo_lookup(
            static_dir, css_prefix, group.slug, group.competition_id)
        img = (f'<img class="day-comp-logo" src="{escape(logo)}" alt="">'
               if logo else "")
        round_txt = (f'<span class="day-comp-round">{escape(group.round_label)}'
                     "</span>" if group.round_label else "")
        out.append('<tbody class="day-group">')
        out.append(
            '<tr class="v2-md-row day-comp-row"><td colspan="3">'
            '<span class="day-comp-head">'
            f'<a class="day-comp-link" href="../{escape(group.slug)}/results.html">'
            f"{img}{escape(group.name.upper())}</a>{round_txt}"
            "</span></td></tr>"
        )
        out += _match_rows(ds, group, crest, club_hub_ids, venues)
        out.append("</tbody>")

    out.append("</table>")
    if any(m.confidence == "unconfirmed" and m.counts_for_table
           for g in day.groups for m in g.matches):
        out.append('<p class="v2-res-legend">* result not yet confirmed</p>')
    out.append("</div>")
    out.append("</div>")
    return "\n".join(out)


# The /matches/ index is baked with the build's today. CI builds once a day at
# 07:07 CAT, so between midnight and the build a visitor's "today" is one day
# ahead of the page. The window is contiguous around today, so today+1 always
# has a page — hop to it rather than showing yesterday's football under a
# heading that says Today. Only ever moves by a day, and only on the index.
_TODAY_JS = """
(function(){
  var el=document.querySelector('[data-day-today]');
  if(!el) return;
  var baked=el.getAttribute('data-day-today');
  var now=new Date(Date.now()+%d*3600000).toISOString().slice(0,10);
  if(now===baked) return;
  var d=new Date(baked+'T00:00:00Z');
  d.setUTCDate(d.getUTCDate()+1);
  if(now===d.toISOString().slice(0,10)) location.replace(now+'.html');
})();
"""


def build_pages(dist, templates_dir, static_dir, ds, updated, today,
                nt_data=None, club_hub_ids=(), tz_offset_hours=2, days=None):
    """Write /matches/ and one page per date. Returns (pages, dates with matches).

    `today` is the build's date in Malawi time — passed in rather than read
    here, so the whole build stamps one date and tests can pin it. `days` is
    an already-collected index (build.py needs one for the landing card before
    these pages are written); omit it and one is collected here.
    """
    global CAL_JS_VERSION
    out_dir = os.path.join(dist, SLUG)
    os.makedirs(out_dir, exist_ok=True)
    base = render._read(os.path.join(templates_dir, "base.html"))
    css_ver = render.css_version(static_dir)
    CAL_JS_VERSION = render.file_version(os.path.join(static_dir, "calendar.js"))
    fl = flags.Flags(static_dir, prefix="../")

    days = collect(ds, nt_data) if days is None else days
    dates = page_dates(days, today)
    dates_with_matches = sorted(d for d in days if days[d].count)
    club_hub_ids = set(club_hub_ids)

    for iso in dates:
        body = render_day(ds, days.get(iso), iso, today, dates,
                          dates_with_matches, static_dir, "../",
                          club_hub_ids, fl)
        title = f"Matches · {full_date_label(iso)}"
        html = (
            base.replace("{{TITLE}}", escape(f"{title} · {render.SITE_NAME}"))
            .replace("{{LEAGUE_NAME}}", escape("Matches"))
            .replace("{{LEAGUE_LOGO}}", "")
            .replace("{{LAST_UPDATED}}", escape(updated))
            .replace("{{NAV}}", render._nav("", items=(("../", "Home"),)))
            .replace("{{SEARCH}}", render.search_widget("../"))
            .replace("{{CONTENT}}", body)
            .replace("{{CSS_PREFIX}}", "../")
            .replace("{{CSS_VER}}", css_ver)
            .replace("{{BACK_LINK}}", BACK_LINK)
            .replace("{{FOOTER}}", render.footer(updated))
            .replace("{{SOCIAL}}", render.social_meta(title))
        )
        render._write(os.path.join(out_dir, f"{iso}.html"), html)

    # /matches/ is today's page again, with the date-drift hop. Same directory,
    # so every relative link in the body already resolves.
    index = html_for_index(base, ds, days, today, dates, dates_with_matches,
                           static_dir, updated, css_ver, club_hub_ids, fl,
                           tz_offset_hours)
    render._write(os.path.join(out_dir, "index.html"), index)

    return len(dates) + 1, len(dates_with_matches)


def html_for_index(base, ds, days, today, dates, dates_with_matches,
                   static_dir, updated, css_ver, club_hub_ids, fl,
                   tz_offset_hours):
    body = render_day(ds, days.get(today), today, today, dates,
                      dates_with_matches, static_dir, "../", club_hub_ids, fl)
    body = (f'<div data-day-today="{today}"></div>' + body
            + f"<script>{_TODAY_JS % tz_offset_hours}</script>")
    title = "Matches today"
    return (
        base.replace("{{TITLE}}", escape(f"{title} · {render.SITE_NAME}"))
        .replace("{{LEAGUE_NAME}}", escape("Matches"))
        .replace("{{LEAGUE_LOGO}}", "")
        .replace("{{LAST_UPDATED}}", escape(updated))
        .replace("{{NAV}}", render._nav("", items=(("../", "Home"),)))
        .replace("{{SEARCH}}", render.search_widget("../"))
        .replace("{{CONTENT}}", body)
        .replace("{{CSS_PREFIX}}", "../")
        .replace("{{CSS_VER}}", css_ver)
        .replace("{{BACK_LINK}}", BACK_LINK)
        .replace("{{FOOTER}}", render.footer(updated))
        .replace("{{SOCIAL}}", render.social_meta(
            title, url=render.SITE_URL + f"/{SLUG}/"))
    )


# ── The landing-page entry point ─────────────────────────────────────────────

def landing_target(days, today: str) -> "tuple[str, Day | None]":
    """(date to link at, its Day) — today when it has football, else the next
    date that does, else the last one that did. "" when there is no data."""
    if days.get(today) is not None and days[today].count:
        return today, days[today]
    with_matches = sorted(d for d in days if days[d].count)
    nxt = _nearest(with_matches, today, forward=True)
    if nxt:
        return nxt, days[nxt]
    prev = _nearest(with_matches, today, forward=False)
    if prev:
        return prev, days[prev]
    return "", None


def landing_card(days, today: str) -> str:
    """The homepage card into the date browser, or "" when there is no football.

    One <a> around the whole card, like the featured Scorchers card above it —
    the whole thing is the tap target, which is the point on a phone.
    """
    iso, day = landing_target(days, today)
    if not day:
        return ""
    d = _parse(iso)
    rel = relative_label(iso, today)
    eyebrow = rel or _WEEKDAYS[d.weekday()]
    href = f"{SLUG}/" if iso == today else f"{SLUG}/{iso}.html"

    n = day.count
    title = f"{n} match{'' if n == 1 else 'es'}"
    if not rel:
        title += f" on {d.day} {d.strftime('%b')}"
    elif rel != "Today":
        title += f" {rel.lower()}"

    names = day.competition_names
    sub = " &middot; ".join(escape(x) for x in names[:2])
    if len(names) > 2:
        sub += f" &middot; +{len(names) - 2} more"

    return (
        f'<a class="el-today" href="{escape(href)}">'
        '<span class="el-today-cal" aria-hidden="true">'
        f'<span class="el-today-dow">{_WEEKDAYS_SHORT[d.weekday()].upper()}</span>'
        f'<span class="el-today-day">{d.day}</span>'
        "</span>"
        '<span class="el-today-main">'
        f'<span class="el-today-eyebrow">{escape(eyebrow)}</span>'
        f'<span class="el-today-title">{escape(title)}</span>'
        f'<span class="el-today-sub">{sub}</span>'
        "</span>"
        '<span class="el-today-arrow" aria-hidden="true">&#x2192;</span>'
        "</a>"
    )
