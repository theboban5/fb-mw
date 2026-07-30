"""The national-team page: /scorchers/index.html.

One page, sections stacked in reading order: header, next match, group table,
recent results (with scorers and, where the data exists, line-ups), upcoming
fixtures, current squad. Like `hubs.py` this reuses `render.py`'s helpers and
the site's existing `v2-*` CSS classes, so it inherits the league pages' look
without touching their build path.

Two deliberate departures from the club pages:

  * **Malawi is always the left-hand side**, whatever `home_away` says, so the
    scorer columns stay aligned with the teams above them (home/away/neutral
    moves into the caption line). A national-team results list reads from one
    team's perspective; a club results table does not.
  * **A line-up section renders only when `nt_lineups` has rows for that
    match** — the same graceful degradation as scorers on club pages. Most
    matches have no line-up data and simply omit the block.

No JavaScript: the line-up is a `<details>` element, and a single team needs no
matchday pager.
"""

from html import escape
import os

from . import render

# Editorial name, matching the landing-page card this page replaces. The data's
# own `nt_teams.team_name` ("Malawi (Women's)") shows above it as the eyebrow.
DISPLAY_NAME = "Malawi Scorchers"
# The short side label used in the results/fixtures tables, where the column is
# 42% of a phone screen.
SIDE_NAME = "Malawi"
SLUG = "scorchers"

FLAG_FILE = "malawi_flag.svg"

BACK_LINK = '<a href="../" class="back-link">&#x2190; All Leagues</a>'

_DASH = "&ndash;"


# ── Small shared pieces ──────────────────────────────────────────────────────

def _date_label(match) -> str:
    """"28 Jul 2026", or "Date TBC" for a fixture the sheet dates as tbd."""
    return render._format_date(match.date) or "Date TBC"


def _meta_line(match, with_competition=True) -> str:
    """The caption above a result/fixture: date · competition · ground · venue."""
    parts = [_date_label(match)]
    if with_competition and match.competition:
        parts.append(match.competition)
    parts.append(match.ground_label)
    if match.venue_label:
        parts.append(match.venue_label)
    return " &middot; ".join(escape(p) for p in parts)


def _score_cell(match) -> str:
    """Malawi's score first — this table is always from Malawi's perspective."""
    if not match.played:
        return '<td class="v2-res-score v2-res-vs">vs</td>'
    note = match.score_note
    note_html = f'<span class="v2-res-note">{escape(note)}</span>' if note else ""
    return (f'<td class="v2-res-score">{match.team_score}:{match.opponent_score}'
            f"{note_html}</td>")


def _match_rows(match, extra_rows=()):
    """The caption row + result row for one match, plus any extra full-width rows."""
    out = [
        f'<tr class="v2-res-meta-row"><td colspan="3">'
        f'<span class="v2-res-meta">{_meta_line(match)}</span></td></tr>',
        f'<tr class="v2-res-row v2-res-row-compact'
        f'{"" if match.played else " v2-res-row-fixture"}">'
        f'<td class="v2-res-home">{escape(SIDE_NAME)}</td>'
        f"{_score_cell(match)}"
        f'<td class="v2-res-away">{escape(match.opponent)}</td></tr>',
    ]
    out += [r for r in extra_rows if r]
    return "".join(out)


def _results_table(body: str) -> str:
    return (
        '<div class="v2-results-outer">'
        '<table class="v2-results-table v2-results-compact">'
        "<thead><tr>"
        f'<th class="v2-res-th-home">{escape(SIDE_NAME.upper())}</th>'
        '<th class="v2-res-th-score">RESULT</th>'
        '<th class="v2-res-th-away">OPPONENT</th>'
        "</tr></thead>"
        f"<tbody>{body}</tbody></table></div>"
    )


def _cards(row) -> str:
    """Card markers for one player: yellow, second yellow, straight red."""
    out = []
    if row.yellow_card and not row.yellow_red_card:
        out.append(("nt-card-y", "Yellow card"))
    if row.yellow_red_card:
        out.append(("nt-card-yr", "Second yellow card"))
    if row.red_card:
        out.append(("nt-card-r", "Red card"))
    return "".join(
        f'<span class="nt-card {cls}" title="{label}" aria-label="{label}"></span>'
        for cls, label in out
    )


def _captain_badge(is_captain=False, is_vice=False) -> str:
    if is_captain:
        return '<span class="nt-cap" title="Captain" aria-label="Captain">C</span>'
    if is_vice:
        return ('<span class="nt-cap nt-cap-vice" title="Vice-captain" '
                'aria-label="Vice-captain">VC</span>')
    return ""


# ── 1. Header ────────────────────────────────────────────────────────────────

def _header(team_data, flag_url) -> str:
    logo = (f'<img class="v2-mini-logo" src="{escape(flag_url)}" alt="">'
            if flag_url else "")
    coach = (f'<p class="nt-coach">Coach &middot; {escape(team_data.coach)}</p>'
             if team_data.coach else "")
    return (
        '<div class="v2-mini-banner">'
        f"{logo}"
        f'<p class="v2-season">{escape(team_data.team.team_name.upper())}</p>'
        f'<h2 class="v2-mini-league">{escape(DISPLAY_NAME.upper())}</h2>'
        f"{coach}"
        "</div>"
    )


# ── 2. Next match ────────────────────────────────────────────────────────────

def _next_match(match) -> str:
    if match is None:
        return ""
    meta = [_date_label(match), match.ground_label]
    if match.venue_label:
        meta.append(match.venue_label)
    return (
        '<h3 class="v2-sec-title">Next Match</h3>'
        '<div class="nt-next">'
        + (f'<p class="nt-next-comp">{escape(match.competition)}</p>'
           if match.competition else "")
        + '<p class="nt-next-teams">'
        f'<span class="nt-next-side">{escape(SIDE_NAME)}</span>'
        f'<span class="nt-next-v">vs</span>'
        f'<span class="nt-next-side">{escape(match.opponent)}</span>'
        "</p>"
        f'<p class="nt-next-meta">{" &middot; ".join(escape(p) for p in meta)}</p>'
        "</div>"
    )


# ── 3. Group table ───────────────────────────────────────────────────────────

def _group_table(group) -> str:
    """One hand-maintained group-table row, as recorded — never computed."""
    cells = "".join(
        f"<td>{escape(v) if v else _DASH}</td>"
        for v in (group.played, group.won, group.drawn, group.lost)
    )
    position = f"{escape(group.position)}." if group.position else _DASH
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
        '<th class="nt-th-pts">PTS</th>'
        "</tr></thead>"
        "<tbody><tr>"
        f'<td class="nt-pos">{position}</td>'
        f'<td class="nt-team">{escape(SIDE_NAME)}</td>'
        f"{cells}"
        f'<td class="nt-pts">{escape(group.points) if group.points else _DASH}</td>'
        "</tr></tbody></table></div>"
        + (f'<p class="v2-res-legend">{" &middot; ".join(legend)}</p>'
           if legend else "")
    )


# ── 4. Recent results ────────────────────────────────────────────────────────

def _scorers_row(result) -> str:
    """Both sides' scorers under one result: ours left, the opponent's right."""
    if not (result.our_goals or result.their_goals):
        return ""

    def column(goals, side_cls):
        lines = "".join(
            f'<span class="v2-ms-line">{escape(g.annotation)}</span>' for g in goals
        )
        return f'<div class="v2-ms-{side_cls}">{lines}</div>'

    return (
        '<tr class="v2-scorers-row"><td colspan="3">'
        '<div class="v2-match-scorers">'
        f'{column(result.our_goals, "home")}{column(result.their_goals, "away")}'
        "</div></td></tr>"
    )


def _lineup_player(row, off_minute="") -> str:
    shirt = (f'<span class="nt-shirt">{escape(row.shirt_number)}</span>'
             if row.shirt_number else '<span class="nt-shirt"></span>')
    off = (f'<span class="nt-off">&darr; {escape(off_minute)}\'</span>'
           if off_minute else "")
    return (
        '<li class="nt-player">'
        f"{shirt}"
        f'<span class="nt-player-name">{escape(row.player_name)}'
        f"{_captain_badge(is_captain=row.captain)}{_cards(row)}{off}</span>"
        "</li>"
    )


def _lineup_row(lineup) -> str:
    """The collapsible line-up block under a result, or "" when there is none.

    Open by default: on the matches that do have this data it is the point of
    the page. A grouped list by position, never a pitch diagram — it has to
    work in one column on a phone.
    """
    if lineup is None:
        return ""
    parts = []

    if lineup.starting:
        blocks = []
        for label, rows in lineup.starting_by_position():
            players = "".join(
                _lineup_player(r, off_minute=r.minute_off) for r in rows)
            blocks.append(
                f'<div class="nt-pos-block">'
                f'<p class="nt-pos-title">{escape(label)}</p>'
                f'<ul class="nt-players">{players}</ul></div>'
            )
        parts.append(
            '<p class="nt-lineup-title">Starting XI</p>'
            f'<div class="nt-lineup-grid">{"".join(blocks)}</div>'
        )

    if lineup.substitutions:
        items = "".join(
            '<li class="nt-sub">'
            + (f'<span class="nt-sub-min">{escape(s.minute)}\'</span>'
               if s.minute else '<span class="nt-sub-min"></span>')
            + f'<span class="nt-sub-text">'
            f'<span class="nt-sub-on">{escape(s.on.player_name)}</span>'
            f'<span class="nt-sub-for"> for </span>'
            f'<span class="nt-sub-off">{escape(s.off_name)}</span>'
            f"{_cards(s.on)}</span></li>"
            for s in lineup.substitutions
        )
        parts.append(
            '<p class="nt-lineup-title">Substitutions</p>'
            f'<ul class="nt-subs">{items}</ul>'
        )

    if lineup.unused:
        names = ", ".join(
            escape(r.player_name)
            + (f" ({escape(r.shirt_number)})" if r.shirt_number else "")
            for r in sorted(lineup.unused, key=lambda r: (r.shirt_sort, r.player_name))
        )
        parts.append(
            '<p class="nt-lineup-title nt-lineup-title-quiet">Unused substitutes</p>'
            f'<p class="nt-unused">{names}</p>'
        )

    if not parts:
        return ""
    return (
        '<tr class="nt-lineup-row"><td colspan="3">'
        '<details class="nt-lineup" open>'
        '<summary class="nt-lineup-summary">Line-up</summary>'
        f'<div class="nt-lineup-body">{"".join(parts)}</div>'
        "</details></td></tr>"
    )


def _results(results) -> str:
    title = '<h3 class="v2-sec-title">Recent Results</h3>'
    if not results:
        return title + '<p class="v2-empty">No results yet.</p>'
    body = "".join(
        _match_rows(r.match, extra_rows=(_scorers_row(r), _lineup_row(r.lineup)))
        for r in results
    )
    return title + _results_table(body)


# ── 5. Upcoming fixtures ─────────────────────────────────────────────────────

def _fixtures(fixtures) -> str:
    title = '<h3 class="v2-sec-title">Upcoming Fixtures</h3>'
    if not fixtures:
        return title + '<p class="v2-empty">No upcoming fixtures.</p>'
    return title + _results_table("".join(_match_rows(m) for m in fixtures))


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


def _squad(squad, ds, club_hub_ids) -> str:
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
            f'<span class="nt-player-name">{escape(p.player_name)}'
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


# ── Page ─────────────────────────────────────────────────────────────────────

def render_page(team_data, ds, club_hub_ids=(), flag_url="") -> str:
    return "\n".join(x for x in [
        '<div class="v2-content">',
        _header(team_data, flag_url),
        _next_match(team_data.next_match),
        *[_group_table(g) for g in team_data.groups],
        _results(team_data.results),
        _fixtures(team_data.fixtures),
        _squad(team_data.squad, ds, club_hub_ids),
        "</div>",  # /v2-content
    ] if x)


def build_page(dist, templates_dir, static_dir, team_data, ds, updated,
               club_hub_ids=()):
    """Write dist/scorchers/index.html. Returns the number of pages written."""
    out_dir = os.path.join(dist, SLUG)
    os.makedirs(out_dir, exist_ok=True)
    base = render._read(os.path.join(templates_dir, "base.html"))
    css_ver = render.css_version(static_dir)
    # The flag sits at the static root, so it needs the page's "../" prefix;
    # a missing file simply renders no logo (as with club crests).
    flag_url = ("../" + FLAG_FILE
                if os.path.exists(os.path.join(static_dir, FLAG_FILE)) else "")

    content = render_page(team_data, ds, club_hub_ids, flag_url=flag_url)
    header_logo = (f'<img class="site-logo" src="{escape(flag_url)}" alt="">'
                   if flag_url else "")
    html = (
        base.replace("{{TITLE}}", escape(DISPLAY_NAME))
        .replace("{{LEAGUE_NAME}}", escape(DISPLAY_NAME))
        .replace("{{LEAGUE_LOGO}}", header_logo)
        .replace("{{LAST_UPDATED}}", escape(updated))
        .replace("{{NAV}}", render._nav("", items=(("../", "Home"),)))
        .replace("{{CONTENT}}", content)
        .replace("{{CSS_PREFIX}}", "../")
        .replace("{{CSS_VER}}", css_ver)
        .replace("{{BACK_LINK}}", BACK_LINK)
    )
    render._write(os.path.join(out_dir, "index.html"), html)
    return 1


def landing_meta(team_data) -> str:
    """The caption for this team's landing-page card: its current competition."""
    if team_data.groups:
        return f"National Team &middot; {escape(team_data.groups[0].competition_name)}"
    return "National Team"
