"""The national-team pages: /flames/ and /scorchers/.

One set of pages per entry in `PAGES`, each built from that team's slice of
the nt_* tabs (`nt.team_data`). Four pages sharing one banner and one tab strip, the same flat-file shape the
league pages use (index.html + siblings, never subdirectories):

  * **index.html** — the tournament the team is in right now: next match, the
    group table, that tournament's results and its remaining fixtures. Nothing
    else; a visitor arriving mid-WAFCON should see WAFCON.
  * **results.html** — the latest year's results, with scorers and (where
    the data exists) line-ups; **results-YYYY.html** for every earlier year,
    behind a "Past years" picker. One page held them all until the RSSSF
    archive (0066) made it 715 results and 471 kB.
  * **fixtures.html** — every scheduled match.
  * **squad.html** — the current squad.
  * **history.html** — the record: by competition, against every opponent,
    the biggest wins and defeats. Only `NTMatch.in_record` matches count;
    unofficial and abandoned ones are shown wherever matches are listed and
    counted nowhere, and each table says how many it left out.
  * **vs-<country>.html** — every meeting with one opponent, linked from the
    head-to-head table. Keyed by `opponent_country`, so Swaziland and
    Eswatini are one page.

Like `hubs.py` this reuses `render.py`'s helpers and the site's existing `v2-*`
CSS classes, so it inherits the league pages' look without touching their build
path.

Three deliberate departures from the club pages:

  * **The home side reads first**, as on the club pages, and the scorer
    columns follow the teams above them (`NTMatch.ours_first`). It used to be
    Malawi on the left whatever `home_away` said, on the argument that the
    list reads from one team's perspective; but "Malawi 1:3 Angola" for a
    match in Talatona read as a home defeat to anyone who did not read the
    caption. A neutral ground keeps Malawi first. Home/Away/Neutral stays in
    the caption line.
  * **Country flags stand in for crests** (`flags.py`), with the club pages'
    placement: the left side's flag sits after its name, the right side's
    before, both nearest the score. An unmapped country renders no flag.
  * **A line-up section renders only when `nt_lineups` has rows for that
    match** — the same graceful degradation as scorers on club pages. Most
    matches have no line-up data and simply omit the block.

No JavaScript: the line-up is a `<details>` element, the tabs are links, and a
single team needs no matchday pager.
"""

from dataclasses import dataclass
from html import escape
import os
import re
import unicodedata

from . import adapt, flags, lineups, nt, render


@dataclass(frozen=True)
class TeamPage:
    """One national team that gets its own section of the site.

    The page was written for the Scorchers alone, with their name and URL as
    module constants, so the men's rows sat in nt_matches for a year with no
    page to render them on — and on /matches/ they rendered under a "Malawi
    Scorchers" heading. Everything that differs between two Malawi sides is
    here; everything else (the side label, the flag) is the same country.

    `display_name` is editorial, matching the landing-page card: the data's own
    `nt_teams.team_name` ("Malawi (Men's)") shows above it as the eyebrow.
    """
    team_code: str
    slug: str
    display_name: str
    nickname: str


FLAMES = TeamPage("MW_M", "flames", "Malawi Flames", "Flames")
SCORCHERS = TeamPage("MW_W", "scorchers", "Malawi Scorchers", "Scorchers")

# Every national team with a page, in the order they are listed wherever more
# than one shows (a day with both sides playing). The four youth sides have
# nt_teams rows and no entry here; they join when their tabs carry matches.
PAGES = (FLAMES, SCORCHERS)
_BY_CODE = {p.team_code: p for p in PAGES}


def page_for(team_code: str) -> "TeamPage | None":
    """The page a team_code is built into, or None when it has none."""
    return _BY_CODE.get(team_code)


# The short side label used in the results/fixtures tables, where the column is
# 42% of a phone screen.
SIDE_NAME = "Malawi"

# The country whose flag stands beside SIDE_NAME. Split from it because the
# table label is editorial and the flag lookup is a country name.
OUR_COUNTRY = "Malawi"

FLAG_FILE = "malawi_flag.svg"

BACK_LINK = '<a href="../" class="back-link">&#x2190; All Leagues</a>'

NAV_ITEMS = (
    ("index.html", "Home"),
    ("results.html", "Results"),
    ("fixtures.html", "Fixtures"),
    ("squad.html", "Squad"),
    ("history.html", "History"),
)

# How many matches the home page shows when there is no tournament on — enough
# to be worth reading, short enough to still be a summary.
HOME_FALLBACK_LIMIT = 5

_DASH = "&ndash;"
# The bracket's "no team here yet", distinct from the en dash a group table
# uses for a blank cell.
_MDASH = "&mdash;"


# ── Small shared pieces ──────────────────────────────────────────────────────

def _date_label(match) -> str:
    """"28 Jul 2026"; "1957" for a match known only to the year (0066); or
    "Date TBC" for a fixture the sheet dates as tbd."""
    if match.date:
        return render._format_date(match.date)
    return str(match.date_year) if match.date_year else "Date TBC"


# One match's round, singular — adapt.STAGE_LABELS names a column of ties
# ("Semi-finals"), this names the one being captioned.
_STAGE_NAMES = {"r1": "First round", "r2": "Second round",
                "r64": "Round of 64", "r32": "Round of 32", "r16": "Round of 16",
                "qf": "Quarter-final", "sf": "Semi-final",
                "3p": "Third-place play-off", "final": "Final"}


def _meta_line(match, with_competition=True) -> str:
    """The caption above a result/fixture: date · competition · round ·
    ground · venue, and "Unofficial" last for a match that is not a full
    international — shown, never counted."""
    parts = [_date_label(match)]
    if with_competition and match.competition:
        parts.append(match.competition)
        if match.stage in _STAGE_NAMES:
            parts.append(_STAGE_NAMES[match.stage])
    parts.append(match.ground_label)
    if match.venue_label:
        parts.append(match.venue_label)
    if match.unofficial:
        parts.append("Unofficial")
    return " &middot; ".join(escape(p) for p in parts)


def _score_cell(match) -> str:
    """The home side's score first, like the names either side of it."""
    if not match.played:
        return '<td class="v2-res-score v2-res-vs">vs</td>'
    note = match.score_note
    note_html = f'<span class="v2-res-note">{escape(note)}</span>' if note else ""
    left, right = match.sides(match.team_score, match.opponent_score)
    return f'<td class="v2-res-score">{left}:{right}{note_html}</td>'


def _match_rows(match, fl, extra_rows=()):
    """The caption row + result row for one match, plus any extra full-width rows."""
    home, away = match.sides((SIDE_NAME, OUR_COUNTRY),
                             (match.opponent, match.opponent))
    out = [
        f'<tr class="v2-res-meta-row"><td colspan="3">'
        f'<span class="v2-res-meta">{_meta_line(match)}</span></td></tr>',
        f'<tr class="v2-res-row v2-res-row-compact'
        f'{"" if match.played else " v2-res-row-fixture"}">'
        f'<td class="v2-res-home">{escape(home[0])}'
        f'{fl.img_for(home[1], "nt-flag nt-flag-post")}</td>'
        f"{_score_cell(match)}"
        f'<td class="v2-res-away">'
        f'{fl.img_for(away[1], "nt-flag nt-flag-pre")}'
        f"{escape(away[0])}</td></tr>",
    ]
    out += [r for r in extra_rows if r]
    return "".join(out)


def _results_table(body: str) -> str:
    return (
        '<div class="v2-results-outer">'
        '<table class="v2-results-table v2-results-compact">'
        "<thead><tr>"
        '<th class="v2-res-th-home">HOME</th>'
        '<th class="v2-res-th-score">RESULT</th>'
        '<th class="v2-res-th-away">AWAY</th>'
        "</tr></thead>"
        f"<tbody>{body}</tbody></table></div>"
    )


def _more_link(href: str, label: str) -> str:
    return (f'<p class="nt-more"><a class="nt-link" href="{href}">'
            f"{escape(label)} &rarr;</a></p>")


_captain_badge = lineups.captain_badge


def _player_href_for(page_ids):
    """A profile link, but ONLY for a player a page was actually written for.

    Every id in these tabs was its own MW_W_### namespace until 0020 merged
    them into `players`, and an opponent's scorer still carries an id from no
    registry at all (INT_LIB_KOSIAH). Beyond that, being NAMED in a squad
    announcement does not earn a page — hubs.player_page_ids wants a goal, an
    assist or an appearance — so a squad member who has not played yet would
    otherwise link straight to a 404. The set is passed in for the same reason
    club_hub_ids is: this module must not guess which pages exist.
    """
    page_ids = frozenset(page_ids or ())

    def href(player_id: str) -> str:
        return (f"../players/{player_id}.html"
                if player_id and player_id in page_ids else "")
    return href


# ── 1. Header ────────────────────────────────────────────────────────────────

def _display_name(team_data) -> str:
    page = page_for(team_data.team.team_code)
    return page.display_name if page else team_data.team.team_name


def _header(team_data, flag_url, picker="") -> str:
    logo = (f'<img class="v2-mini-logo" src="{escape(flag_url)}" alt="">'
            if flag_url else "")
    coach = (f'<p class="nt-coach">Coach &middot; {escape(team_data.coach)}</p>'
             if team_data.coach else "")
    return (
        '<div class="v2-mini-banner">'
        f"{logo}"
        f'<p class="v2-season">{escape(team_data.team.team_name.upper())}</p>'
        f'<h2 class="v2-mini-league">{escape(_display_name(team_data).upper())}</h2>'
        f"{coach}{picker}"
        "</div>"
    )


# ── 2. Next match ────────────────────────────────────────────────────────────

def _next_sides(match, fl, cls):
    """"Egypt [flag] vs [flag] Malawi" — the two spans of a next-match panel,
    home side first, each flag nearest the "vs"."""
    home, away = match.sides((SIDE_NAME, OUR_COUNTRY),
                             (match.opponent, match.opponent))
    return (f'<span class="{cls}-side">{escape(home[0])}'
            f'{fl.img_for(home[1], "nt-flag nt-flag-post")}</span>'
            f'<span class="{cls}-v">vs</span>'
            f'<span class="{cls}-side">'
            f'{fl.img_for(away[1], "nt-flag nt-flag-pre")}'
            f"{escape(away[0])}</span>")


def _next_match(match, fl) -> str:
    if match is None:
        return ""
    meta = [p for p in (_date_label(match), match.kickoff_label,
                        match.ground_label) if p]
    if match.venue_label:
        meta.append(match.venue_label)
    return (
        '<h3 class="v2-sec-title">Next Match</h3>'
        '<div class="nt-next">'
        + (f'<p class="nt-next-comp">{escape(match.competition)}</p>'
           if match.competition else "")
        + f'<p class="nt-next-teams">{_next_sides(match, fl, "nt-next")}</p>'
        f'<p class="nt-next-meta">{" &middot; ".join(escape(p) for p in meta)}</p>'
        "</div>"
    )


# ── 3. Group table ───────────────────────────────────────────────────────────

def _group_row(group, row, fl) -> str:
    """One team's line. Ours is highlighted and uses the site's short label."""
    ours = group.is_us(row)
    name = SIDE_NAME if ours else (row.team_name or row.team_code)
    cells = "".join(
        f"<td>{escape(v) if v else _DASH}</td>"
        for v in (row.played, row.won, row.drawn, row.lost)
    )
    if group.has_goals:
        cells += (f'<td class="nt-goals">{escape(row.goals_label) or _DASH}</td>'
                  f"<td>{escape(row.goal_difference) or _DASH}</td>")
    return (
        f'<tr class="{"nt-row-us" if ours else ""}">'
        f'<td class="nt-pos">{escape(row.position) + "." if row.position else _DASH}</td>'
        f'<td class="nt-team">{fl.img_for(name, "nt-flag nt-flag-pre")}'
        f"{escape(name)}</td>"
        f"{cells}"
        f'<td class="nt-pts">{escape(row.points) if row.points else _DASH}</td>'
        "</tr>"
    )


def _group_table(group, fl) -> str:
    """One hand-maintained group table, as recorded — never computed.

    Renders however many rows the sheet holds: with only our own row it is the
    one-line snapshot this page started with, and it grows into the full group
    as the rivals' rows are filled in.
    """
    goals_head = "<th>GOALS</th><th>DIFF</th>" if group.has_goals else ""
    legend = []
    if group.last_update:
        legend.append(f"As of {escape(render._format_date(group.last_update))}")
    if group.wikipedia_url:
        legend.append(
            f'<a class="nt-link" href="{escape(group.wikipedia_url)}" '
            f'target="_blank" rel="noopener">Full group table on Wikipedia '
            f"&rarr;</a>"
        )
    return (
        f'<h3 class="v2-sec-title">{escape(group.title)}</h3>'
        '<div class="v2-table-outer">'
        '<table class="nt-table">'
        "<thead><tr>"
        '<th class="nt-th-pos">POS</th>'
        '<th class="nt-th-team">TEAM</th>'
        "<th>P</th><th>W</th><th>D</th><th>L</th>"
        f"{goals_head}"
        '<th class="nt-th-pts">PTS</th>'
        "</tr></thead>"
        "<tbody>"
        + "".join(_group_row(group, r, fl) for r in group.rows)
        + "</tbody></table></div>"
        + (f'<p class="v2-res-legend">{" &middot; ".join(legend)}</p>'
           if legend else "")
    )


# ── 3b. Knockout bracket ─────────────────────────────────────────────────────
#
# The other half of a tournament, rendered in the cup pages' own `v2-br-*`
# classes so /scorchers/ and /top8/ show the same bracket. Written here rather
# than calling render.render_bracket because that one is a whole-page renderer
# wired to league teams and crests; only the markup is shared, as with every
# other v2- class this module reuses.
#
# Two differences, both of them things this page already does elsewhere: flags
# stand in for crests, and a side with no name yet is a TBD slot rather than a
# missing row. The third-place play-off renders under the tree, not as a fourth
# column — four columns inside the site's 660px column leaves ~160px each,
# too narrow for country names, and it is not part of the tree anyway.

# The rounds that make up the tree itself, earliest first. "3p" is deliberately
# absent: adapt.STAGE_ORDER puts it between the semis and the final, which is
# when it is played but not where it belongs in a bracket.
_TREE_STAGES = tuple(s for s in sorted(adapt.STAGE_ORDER, key=adapt.STAGE_ORDER.get)
                     if s != "3p")


def _feed_label(tie, side, by_id) -> str:
    """"Winner QF1" — where an unfilled slot will come from.

    Deliberately the short chip form rather than "Winner Quarter-final 1":
    three columns inside the site's 660px column leaves ~200px each, and the
    long form wraps to two lines on every placeholder, which makes the empty
    rounds taller than the played ones. `adapt.STAGE_CHIPS` is the same
    abbreviation the cup results pager already uses.
    """
    feed = tie.feed(side)
    source = by_id.get(feed[1]) if feed else None
    if source is None:
        return ""
    stage = adapt.STAGE_CHIPS.get(source.stage, source.stage.upper())
    return f"{feed[0].capitalize()} {stage}{source.slot}"


def _br_side(name, score, is_winner, fl, placeholder="") -> str:
    """One side of a tie, or the slot's source while the draw is pending."""
    if not name:
        label = escape(placeholder) if placeholder else _MDASH
        return ('<div class="v2-br-side v2-br-tbd">'
                f'<span class="v2-br-team">{label}</span></div>')
    cls = "v2-br-side v2-br-win" if is_winner else "v2-br-side"
    score_html = (f'<span class="v2-br-score">{score}</span>'
                  if score is not None else "")
    return (f'<div class="{cls}"><span class="v2-br-team">'
            f'{fl.img_for(name, "nt-flag nt-flag-pre")}{escape(name)}</span>'
            f"{score_html}</div>")


def _br_tie(tie, fl, by_id) -> str:
    """One tie card: caption, both sides, and any pens/AET note beneath."""
    parts = []
    meta = [p for p in (_date_label(tie) if tie.date else "",
                        tie.kickoff_label, tie.venue_label) if p]
    if meta:
        parts.append('<p class="v2-br-meta">'
                     + " &middot; ".join(escape(p) for p in meta) + "</p>")

    if tie.played:
        winner = tie.winner_name
        parts.append(_br_side(tie.home_name, tie.home_score,
                              bool(winner) and winner == tie.home_name, fl))
        parts.append(_br_side(tie.away_name, tie.away_score,
                              bool(winner) and winner == tie.away_name, fl))
        if tie.score_note:
            parts.append(f'<p class="v2-br-note">{escape(tie.score_note)}</p>')
    else:
        parts.append(_br_side(tie.home_name, None, False, fl,
                              _feed_label(tie, "home", by_id)))
        parts.append('<p class="v2-br-vs">vs</p>')
        parts.append(_br_side(tie.away_name, None, False, fl,
                              _feed_label(tie, "away", by_id)))

    # An empty tie is still a real slot in the tree, so it gets the dashed
    # border the cup bracket uses for a round it has not reached.
    empty = " v2-br-tie-tbd" if not (tie.home_name or tie.away_name) else ""
    return f'<div class="v2-br-tie{empty}">{"".join(parts)}</div>'


def _bracket(bracket, fl) -> str:
    """The knockout tree, or "" when the sheet holds no ties for it yet.

    Only the rounds actually present render, so a bracket seeded as far as the
    quarter-finals is three columns and one seeded as a round of 16 is four —
    nothing here knows any particular tournament's shape.
    """
    by_stage = {}
    for tie in bracket.ties:
        by_stage.setdefault(tie.stage, []).append(tie)
    if not by_stage:
        return ""
    by_id = {t.tie_id: t for t in bracket.ties}

    cols = []
    for stage in _TREE_STAGES:
        ties = by_stage.get(stage)
        if not ties:
            continue
        title = escape(adapt.STAGE_LABELS.get(stage, stage).upper())
        cards = "".join(_br_tie(t, fl, by_id) for t in ties)
        cols.append(f'<section class="v2-br-round">'
                    f'<h3 class="v2-br-title">{title}</h3>'
                    f'<div class="v2-br-ties">{cards}</div></section>')

    out = [f'<h3 class="v2-sec-title">Knockout Stage</h3>']
    if cols:
        out.append(f'<div class="v2-br-outer"><div class="v2-bracket">'
                   f'{"".join(cols)}</div></div>')
    for tie in by_stage.get("3p", []):
        out.append(f'<h3 class="v2-sec-title">'
                   f'{escape(adapt.STAGE_LABELS["3p"])}</h3>'
                   f'<div class="v2-br-outer">'
                   f'<div class="v2-bracket v2-br-solo">'
                   f'<section class="v2-br-round">'
                   f'<div class="v2-br-ties">{_br_tie(tie, fl, by_id)}</div>'
                   f"</section></div></div>")
    return "".join(out)


# ── 4. Results ───────────────────────────────────────────────────────────────

def _scorers_row(result) -> str:
    """Both sides' scorers under one result, under the side they scored for."""
    if not (result.our_goals or result.their_goals):
        return ""
    left, right = result.match.sides(result.our_goals, result.their_goals)

    def column(goals, side_cls):
        lines = "".join(
            f'<span class="v2-ms-line">{escape(g.annotation)}</span>' for g in goals
        )
        return f'<div class="v2-ms-{side_cls}">{lines}</div>'

    return (
        '<tr class="v2-scorers-row"><td colspan="3">'
        '<div class="v2-match-scorers">'
        f'{column(left, "home")}{column(right, "away")}'
        "</div></td></tr>"
    )


def _lineup_row(lineup, player_href) -> str:
    """The collapsible line-up block under a result, or "" when there is none.

    The markup is shared with the league results table — see src/lineups.py.
    """
    return lineups.lineup_row_html(lineup, player_href=player_href)


def _results(results, fl, title="Recent Results", empty="No results yet.",
             more=None, player_href=None) -> str:
    player_href = player_href or _player_href_for(())
    head = f'<h3 class="v2-sec-title">{escape(title)}</h3>'
    if not results:
        return head + f'<p class="v2-empty">{escape(empty)}</p>'
    body = "".join(
        _match_rows(r.match, fl,
                    extra_rows=(_scorers_row(r),
                                _lineup_row(r.lineup, player_href)))
        for r in results
    )
    return head + _results_table(body) + (more or "")


def results_href(year, latest) -> str:
    """The latest year is results.html, the tab; every other year its own."""
    return "results.html" if year == latest else f"results-{year}.html"


def _year_picker(years, year) -> str:
    """render.season_nav's picker, over years: "Past years" on the latest,
    "Change year" on an older one, nothing for a team with one year."""
    if not years:
        return ""
    links = [(str(y), results_href(y, years[0]), y == year) for y in years]
    return render.season_nav(links, past="Past years", change="Change year",
                             label="Years")


def _credit(matches) -> str:
    """RSSSF's acknowledgement, on any page that shows a match it supplied.

    Its condition for copying is "proper acknowledgement is given to the
    author", so the line goes wherever an archive match renders — a year
    page, the history, an opponent's page — read off the rows' own
    source_ref, as build._source_credit does for a league archive. A page of
    matches a reporter typed carries none.
    """
    refs = sorted({m.source_ref.split(",")[0].strip() for m in matches
                   if "rsssf.org" in (m.source_ref or "")})
    if not refs:
        return ""
    link = f'<a href="{escape(refs[0], quote=True)}" rel="noopener">RSSSF</a>'
    return (f'<p class="v2-credit">Archive results from {link} — Barrie '
            f'Courtney, &ldquo;Malawi - List of International Matches&rdquo;, '
            f'Rec.Sport.Soccer Statistics Foundation. Matches marked '
            f'Unofficial are not full internationals and are counted in no '
            f'total.</p>')


# ── 5. Fixtures ──────────────────────────────────────────────────────────────

def _fixtures(fixtures, fl, title="Upcoming Fixtures",
              empty="No upcoming fixtures.", more=None) -> str:
    head = f'<h3 class="v2-sec-title">{escape(title)}</h3>'
    if not fixtures:
        return head + f'<p class="v2-empty">{escape(empty)}</p>'
    body = "".join(_match_rows(m, fl) for m in fixtures)
    return head + _results_table(body) + (more or "")


# ── 5b. History ──────────────────────────────────────────────────────────────

def vs_slug(country: str) -> str:
    """'São Tomé and Príncipe' -> 'sao-tome-and-principe', for vs-<slug>.html.

    A filename from a display name, the one sanctioned kind of string
    transform (CLAUDE.md: the competition slug for URLs is the other). It is
    never read back.
    """
    ascii_ = unicodedata.normalize("NFKD", country).encode("ascii", "ignore").decode()
    return re.sub(r"[^a-z0-9]+", "-", ascii_.lower()).strip("-") or "unknown"


def _record_cells(rec) -> str:
    goals = f"{rec.goals_for}:{rec.goals_against}" if rec.played else _DASH
    return (f"<td>{rec.played}</td>"
            f"<td>{rec.won}</td><td>{rec.drawn}</td><td>{rec.lost}</td>"
            f'<td class="nt-goals">{goals}</td>')


_RECORD_HEAD = ("<th>P</th><th>W</th><th>D</th><th>L</th><th>GOALS</th>")


def _left_out(n) -> str:
    """The legend line a record table owes its reader when it skipped rows."""
    if not n:
        return ""
    return (f'<p class="v2-res-legend">{n} unofficial or abandoned '
            f'match{"" if n == 1 else "es"} shown elsewhere and not counted '
            "here.</p>")


def _record_table(title, total, rows, fl=None, href=None) -> str:
    """A record table: an "All matches" line first, then one line per group.

    `rows` is nt.group_record's output. With `fl`, the label is a country and
    gets its flag; with `href`, it links to that country's page.
    """
    body = [f'<tr class="nt-row-us"><td class="nt-team">All matches</td>'
            f"{_record_cells(total)}</tr>"]
    for key, _ms, rec in rows:
        label = escape(key)
        if href:
            label = f'<a class="nt-link" href="{escape(href(key))}">{label}</a>'
        flag = fl.img_for(key, "nt-flag nt-flag-pre") if fl else ""
        body.append(f'<tr><td class="nt-team">{flag}{label}</td>'
                    f"{_record_cells(rec)}</tr>")
    return (f'<h3 class="v2-sec-title">{escape(title)}</h3>'
            '<div class="v2-table-outer"><table class="nt-table">'
            f'<thead><tr><th class="nt-th-team">'
            f'{"OPPONENT" if fl else "COMPETITION"}</th>{_RECORD_HEAD}</tr></thead>'
            f'<tbody>{"".join(body)}</tbody></table></div>'
            + _left_out(total.not_counted))


def _match_list(title, matches, fl) -> str:
    """Plain result rows — no scorers, no line-ups — for a list of matches."""
    if not matches:
        return ""
    body = "".join(_match_rows(m, fl) for m in matches)
    return f'<h3 class="v2-sec-title">{escape(title)}</h3>' + _results_table(body)


def render_history(team_data, fl, flag_url="") -> str:
    """The record, against everyone and in everything, from matches that count."""
    matches = [r.match for r in team_data.results]
    if not matches:
        return _page([_header(team_data, flag_url),
                      '<h3 class="v2-sec-title">History</h3>'
                      '<p class="v2-empty">No results yet.</p>'])
    total = nt.record("All matches", matches)
    first = min((m.year for m in matches if m.year), default=None)
    intro = (f'<p class="nt-squad-note">Every result since {first} &middot; '
             f"{total.won} won, {total.drawn} drawn, {total.lost} lost</p>"
             if first else "")
    return _page([
        _header(team_data, flag_url),
        intro,
        _record_table("Head to Head", total,
                      nt.group_record(matches, lambda m: m.country_key),
                      fl=fl, href=lambda k: f"vs-{vs_slug(k)}.html"),
        _record_table("By Competition", total,
                      nt.group_record(matches, lambda m: m.competition)),
        _match_list("Biggest Wins", nt.biggest(matches, wins=True), fl),
        _match_list("Heaviest Defeats", nt.biggest(matches, wins=False), fl),
        _credit(matches),
    ])


def render_vs(team_data, fl, country, flag_url="", player_href=None) -> str:
    """Every meeting with one opponent country, newest first."""
    results = [r for r in team_data.results if r.match.country_key == country]
    rec = nt.record(country, [r.match for r in results])
    names = sorted({r.match.opponent for r in results} - {country})
    also = (f'<p class="nt-squad-note">Also played as {escape(", ".join(names))}</p>'
            if names else "")
    summary = (f'<p class="nt-squad-note">{rec.played} played &middot; '
               f"{rec.won} won, {rec.drawn} drawn, {rec.lost} lost &middot; "
               f"goals {rec.goals_for}:{rec.goals_against}</p>")
    return _page([
        _header(team_data, flag_url),
        f'<h3 class="v2-sec-title">{fl.img_for(country, "nt-flag nt-flag-pre")}'
        f"Malawi v {escape(country)}</h3>",
        summary, also, _left_out(rec.not_counted),
        _results(results, fl, title="Every Meeting", player_href=player_href),
        _more_link("history.html", "Head to head with everyone"),
        _credit([r.match for r in results]),
    ])


# ── 6. Current squad ─────────────────────────────────────────────────────────

def _club_cell(player, ds, club_hub_ids) -> str:
    """The player's club, linked to its hub when they play in Malawi.

    `domestic_team_id` is blank for foreign-based players — expected, not an
    error — and the club name from the squad announcement shows either way.
    """
    if not player.club:
        return ""
    label = escape(player.club)
    team = ds.teams.get(player.domestic_team_id) if player.domestic_team_id else None
    if team is not None and team.club_id in club_hub_ids:
        label = (f'<a class="club-link" href="../clubs/'
                 f'{escape(team.club_id)}.html">{label}</a>')
    country = player.club_country
    if country and country.strip().lower() != "malawi":
        label += f'<span class="nt-club-country"> &middot; {escape(country)}</span>'
    return f'<span class="nt-club">{label}</span>'


def _squad_name(player, player_href) -> str:
    """A squad member's name, linked to their profile when one was written."""
    href = player_href(getattr(player, "player_id", "") or "")
    return (f'<a class="el-player-link" href="{escape(href)}">'
            f"{escape(player.player_name)}</a>") if href else escape(player.player_name)


def _squad(squad, ds, club_hub_ids, player_href) -> str:
    title = '<h3 class="v2-sec-title">Current Squad</h3>'
    if squad is None or not squad.players:
        return title + '<p class="v2-empty">No squad announced yet.</p>'

    note = []
    if squad.announcement_date:
        note.append(f"Announced {escape(render._format_date(squad.announcement_date))}")
    if squad.competition_context:
        note.append(escape(squad.competition_context))
    groups = []
    for label, players in squad.by_position():
        items = "".join(
            '<li class="nt-player">'
            + (f'<span class="nt-shirt">{escape(p.shirt_number)}</span>'
               if p.shirt_number else '<span class="nt-shirt"></span>')
            + '<span class="nt-player-main">'
            f'<span class="nt-player-name">{_squad_name(p, player_href)}'
            f"{_captain_badge(p.is_captain, p.is_vice_captain)}</span>"
            f"{_club_cell(p, ds, club_hub_ids)}"
            "</span></li>"
            for p in players
        )
        groups.append(
            f'<section class="nt-pos-group">'
            f'<h4 class="nt-pos-heading">{escape(label)}</h4>'
            f'<ul class="nt-players nt-squad-players">{items}</ul></section>'
        )
    return (
        title
        + (f'<p class="nt-squad-note">{" &middot; ".join(note)}</p>' if note else "")
        + f'<div class="nt-squad">{"".join(groups)}</div>'
    )


# ── Pages ────────────────────────────────────────────────────────────────────

def _page(parts) -> str:
    return "\n".join(x for x in ['<div class="v2-content">', *parts, "</div>"] if x)


def render_home(team_data, fl, flag_url="", player_href=None) -> str:
    """The tournament the team is in right now — and nothing else.

    With a tournament on (WAFCON, qualifiers, a COSAFA), the sections below the
    banner are all about it. Between tournaments there is nothing to be current
    about, so the page falls back to the most recent handful of matches, with
    the full lists a tab away either way.
    """
    competition = team_data.current_competition
    if competition:
        results = team_data.competition_results(competition)
        fixtures = team_data.competition_fixtures(competition)
        result_block = _results(
            results, fl, title=f"{competition} Results",
            empty=f"No {competition} matches played yet.",
            more=_more_link("results.html", "All results"),
            player_href=player_href)
        fixture_block = _fixtures(
            fixtures, fl, title=f"{competition} Fixtures",
            empty=f"No {competition} matches left to play.",
            more=_more_link("fixtures.html", "All fixtures"))
    else:
        result_block = _results(
            team_data.results[:HOME_FALLBACK_LIMIT], fl,
            more=_more_link("results.html", "All results"),
            player_href=player_href)
        fixture_block = _fixtures(
            team_data.fixtures[:HOME_FALLBACK_LIMIT], fl,
            more=_more_link("fixtures.html", "All fixtures"))

    return _page([
        _header(team_data, flag_url),
        _next_match(team_data.next_match, fl),
        # A group whose every row is blank (MW_M's 2026 Four Nations row: a
        # name and no figures) is a placeholder, and a placeholder renders
        # nothing — it was a line of dashes above the real fixtures.
        *[_group_table(g, fl) for g in team_data.groups
          if any(r.played or r.points or r.position for r in g.rows)],
        *[_bracket(b, fl) for b in team_data.brackets],
        result_block,
        fixture_block,
    ])


def render_results(team_data, fl, flag_url="", player_href=None,
                   year=None) -> str:
    """One year's results, the latest by default, with the year picker."""
    years = team_data.result_years
    year = year if year is not None else (years[0] if years else None)
    results = team_data.results_in(year) if year is not None else []
    return _page([
        _header(team_data, flag_url, _year_picker(years, year)),
        _results(results, fl,
                 title=f"{year} Results" if year is not None else "Results",
                 player_href=player_href),
        _credit([r.match for r in results]),
    ])


def render_fixtures(team_data, fl, flag_url="") -> str:
    return _page([
        _header(team_data, flag_url),
        _fixtures(team_data.fixtures, fl, title="All Fixtures"),
    ])


def render_squad(team_data, fl, ds, club_hub_ids=(), flag_url="",
                 player_href=None) -> str:
    return _page([
        _header(team_data, flag_url),
        _squad(team_data.squad, ds, club_hub_ids,
               player_href or _player_href_for(())),
    ])


def build_page(dist, templates_dir, static_dir, team_data, ds, updated,
               club_hub_ids=(), player_page_ids=()):
    """Write dist/<slug>/*.html for one team in PAGES. Returns the page count."""
    page = page_for(team_data.team.team_code)
    if page is None:
        raise ValueError(f"no national-team page for {team_data.team.team_code!r}")
    display_name = page.display_name
    out_dir = os.path.join(dist, page.slug)
    os.makedirs(out_dir, exist_ok=True)
    base = render._read(os.path.join(templates_dir, "base.html"))
    css_ver = render.css_version(static_dir)
    # Every page of this section sits one level down, so the flag and the CSS
    # share the same "../" prefix; a missing file simply renders no logo (as
    # with club crests).
    flag_url = ("../" + FLAG_FILE
                if os.path.exists(os.path.join(static_dir, FLAG_FILE)) else "")
    fl = flags.Flags(static_dir, prefix="../")
    player_href = _player_href_for(player_page_ids)

    pages = {
        "index.html": render_home(team_data, fl, flag_url=flag_url,
                                  player_href=player_href),
        "results.html": render_results(team_data, fl, flag_url=flag_url,
                                       player_href=player_href),
        "fixtures.html": render_fixtures(team_data, fl, flag_url=flag_url),
        "squad.html": render_squad(team_data, fl, ds, club_hub_ids,
                                   flag_url=flag_url, player_href=player_href),
        "history.html": render_history(team_data, fl, flag_url=flag_url),
    }
    # The pages behind the History and Results tabs: a title of their own,
    # and the tab they belong to lit in the nav.
    titles, tab_of = {}, {}
    years = team_data.result_years
    for y in years[1:]:
        name = results_href(y, years[0])
        pages[name] = render_results(team_data, fl, flag_url=flag_url,
                                     player_href=player_href, year=y)
        titles[name], tab_of[name] = f"{y} Results", "results.html"
    if years:
        titles["results.html"] = f"{years[0]} Results"
    for country in sorted({r.match.country_key for r in team_data.results}):
        name = f"vs-{vs_slug(country)}.html"
        pages[name] = render_vs(team_data, fl, country, flag_url=flag_url,
                                player_href=player_href)
        titles[name], tab_of[name] = f"Malawi v {country}", "history.html"
    header_logo = (f'<img class="site-logo" src="{escape(flag_url)}" alt="">'
                   if flag_url else "")
    # The nav label is what distinguishes these four pages, so it is the first
    # half of the <title> — the same shape league pages use ("Results · Malawi
    # Scorchers"), instead of the team name twice.
    nav_labels = dict(NAV_ITEMS)
    for filename, content in pages.items():
        page_title = titles.get(filename) or nav_labels.get(filename, display_name)
        html = (
            base.replace("{{TITLE}}", escape(f"{page_title} · {display_name}"))
            .replace("{{LEAGUE_NAME}}", escape(display_name))
            .replace("{{LEAGUE_LOGO}}", header_logo)
            .replace("{{LAST_UPDATED}}", escape(updated))
            .replace("{{NAV}}", render._nav(tab_of.get(filename, filename),
                                            items=NAV_ITEMS))
            .replace("{{SEARCH}}", render.search_widget("../"))
            .replace("{{CONTENT}}", content)
            .replace("{{CSS_PREFIX}}", "../")
            .replace("{{CSS_VER}}", css_ver)
            .replace("{{BACK_LINK}}", BACK_LINK)
            .replace("{{FOOTER}}", render.footer(updated))
            .replace("{{SOCIAL}}",
                     render.social_meta(f"{page_title} · {display_name}"))
        )
        render._write(os.path.join(out_dir, filename), html)
    return len(pages)


def landing_meta(team_data) -> str:
    """The caption for this team's landing-page card: its current competition."""
    if team_data.groups:
        return f"National Team &middot; {escape(team_data.groups[0].competition_name)}"
    return "National Team"


def landing_next_match(team_data, fl) -> str:
    """The next-match panel for the landing page's featured card, or "".

    The same fixture the team's own page leads on — the soonest scheduled match
    — so it moves on by itself: once a played match is entered as a result it
    leaves `fixtures`, and the next hourly build promotes the one behind it.
    Spans rather than <p>/<h3> because the whole card is one <a>.
    """
    match = team_data.next_match
    if match is None:
        return ""
    # Date, kickoff and ground on one line, the venue on its own: in a column
    # this narrow a single run would break stadium names mid-name.
    meta = " &middot; ".join(escape(p) for p in (_date_label(match),
                                                match.kickoff_label,
                                                match.ground_label) if p)
    venue = (f'<span class="el-next-venue">{escape(match.venue_label)}</span>'
             if match.venue_label else "")
    return (
        '<div class="el-next">'
        '<span class="el-next-label">Next match</span>'
        '<span class="el-next-card">'
        + (f'<span class="el-next-comp">{escape(match.competition)}</span>'
           if match.competition else "")
        + f'<span class="el-next-teams">{_next_sides(match, fl, "el-next")}</span>'
        f'<span class="el-next-meta">{meta}</span>{venue}'
        "</span></div>"
    )
