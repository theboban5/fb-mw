"""Referee and coach pages — /officials/{official_id}.html.

The third kind of person page on this site, after the club hub and the player
profile, and built the same way: from the tabs, by asking one question of the
whole dataset rather than one competition at a time. A referee takes a Super
League match on Saturday and a Castle Challenge Cup tie on Wednesday, and the
page that is worth having is the one that shows both.

WHERE THE DATA IS. Six columns on `matches` hold the NAME as reported and six
more hold the id it was resolved to (0023, 0024). This module only ever reads
the id columns: a name nobody has tapped onto a registry row belongs to no
page, renders as plain text under the result, and is not a person as far as
this site is concerned. That is the same rule a team-sheet row with a blank
`player_id` lives by.

WHAT COUNTS AS ONE PERSON. `officials.kind` is referee | coach, and the four
match-official roles share the referee kind, because the same person referees
one match and runs the line at the next — so a referee's page lists all four
roles together, newest first, and says which each was.

`official_page_ids` is the single source of which officials get a page. The
pages, the search index and every link under a result ask it, for the reason
`hubs.player_page_ids` exists: deriving that set twice is how a link 404s.
"""

from dataclasses import dataclass
from html import escape
import os

from . import adapt, hubs, render

# The six columns, in the order a page lists them, with the label each earns.
# home_coach/away_coach carry which side they were on; that is what turns a
# result into "their" result and lets the page say W/D/L.
ROLES = (
    ("referee_id", "Referee", None),
    ("assistant_referee_1_id", "Assistant referee", None),
    ("assistant_referee_2_id", "Assistant referee", None),
    ("fourth_official_id", "Fourth official", None),
    ("home_coach_id", "Head coach", True),
    ("away_coach_id", "Head coach", False),
)

# Same cutoff, and the same reason, as the player profile's match table: the
# whole record is in the page for search and for one tap, without twenty
# seasons of table on first paint.
MATCH_ROWS_SHOWN = 15


@dataclass(frozen=True)
class Duty:
    """One match this person was named on, and in what capacity."""
    date: str
    competition: str      # label, not an id
    slug: str             # the competition's URL slug, for the caption link
    role: str             # Referee | Assistant referee | Fourth official | Head coach
    home_name: str
    away_name: str
    home_id: str          # club_id, for the hub link; "" when there is no hub
    away_id: str
    scoreline: str        # "2-1" from the home side's view, or ""
    outcome: str          # W/D/L from THIS person's side — coaches only
    side: "bool | None"   # True home, False away, None for a match official

    @property
    def sort_key(self):
        # Undated matches sort last on a newest-first list, which is where a
        # fixture with no settled day belongs.
        return (self.date or "0000-00-00", self.competition)

    @property
    def is_coach(self) -> bool:
        return self.side is not None


def duties(ds) -> "dict[str, list[Duty]]":
    """official_id -> every match they were named on, newest first.

    Placeholder matches are skipped here as everywhere: they parse, and they
    render nowhere.
    """
    out: "dict[str, list[Duty]]" = {}
    for m in ds.matches.values():
        if m.is_placeholder:
            continue
        named = [(getattr(m, column), role, side)
                 for column, role, side in ROLES if getattr(m, column)]
        if not named:
            continue

        home = ds.teams.get(m.home_team_id)
        away = ds.teams.get(m.away_team_id)
        home_club = ds.clubs.get(home.club_id) if home else None
        away_club = ds.clubs.get(away.club_id) if away else None
        competition = ds.league_display_name(m.competition_id, m.season_id)
        comp = ds.competitions.get(m.competition_id)
        slug = (adapt.competition_slug(m.competition_id, comp.country)
                if comp else "")
        scoreline = (f"{m.home_goals}-{m.away_goals}"
                     if m.has_score and m.counts_for_table else "")

        for official_id, role, side in named:
            if official_id not in ds.officials:
                continue
            outcome = ""
            if side is not None and scoreline:
                ours = m.home_goals if side else m.away_goals
                theirs = m.away_goals if side else m.home_goals
                outcome = "W" if ours > theirs else ("D" if ours == theirs else "L")
            out.setdefault(official_id, []).append(Duty(
                date=m.date, competition=competition, slug=slug, role=role,
                home_name=home.display_name if home else m.home_team_id,
                away_name=away.display_name if away else m.away_team_id,
                home_id=home_club.club_id if home_club else "",
                away_id=away_club.club_id if away_club else "",
                scoreline=scoreline, outcome=outcome, side=side,
            ))

    for lst in out.values():
        lst.sort(key=lambda d: d.sort_key, reverse=True)
    return out


def official_page_ids(ds, by_official=None) -> "set[str]":
    """Exactly the set of officials a page is written for.

    An official earns a page by having been named on a match. A registry row
    nobody has used yet — minted in the portal and then not saved onto
    anything — has nothing to put on a page, exactly as a `players` row with no
    goal and no team sheet has none.
    """
    if by_official is None:
        by_official = duties(ds)
    return {oid for oid, lst in by_official.items() if lst and oid in ds.officials}


def _tiles(official, career) -> str:
    """Counts, and only the ones that are non-zero.

    A referee who has also run the line gets both numbers; one who has only
    ever refereed gets one, rather than three zeroes reading as "this person
    has done nothing".
    """
    tiles = []
    if official.kind == "coach":
        tiles.append(render_tile(len(career), "Matches"))
        for label, letter in (("Won", "W"), ("Drawn", "D"), ("Lost", "L")):
            n = sum(1 for d in career if d.outcome == letter)
            if n:
                tiles.append(render_tile(n, label))
    else:
        for label, role in (("As referee", "Referee"),
                            ("As assistant", "Assistant referee"),
                            ("Fourth official", "Fourth official")):
            n = sum(1 for d in career if d.role == role)
            if n:
                tiles.append(render_tile(n, label))
    if not tiles:
        return ""
    return f'<div class="pl-tiles">{"".join(tiles)}</div>'


def render_tile(value, label) -> str:
    return (f'<div class="pl-tile"><span class="pl-tile-n">{value}</span>'
            f'<span class="pl-tile-l">{escape(label)}</span></div>')


def _header(official, career) -> str:
    kind = "Head coach" if official.kind == "coach" else "Match official"
    latest = career[0] if career else None
    # A coach's most recent side is the one fact about them worth putting in
    # the header. A referee has no side, by definition, and naming the last
    # club they saw would read as an allegiance.
    club_line = ""
    if latest is not None and latest.is_coach:
        name = latest.home_name if latest.side else latest.away_name
        club_id = latest.home_id if latest.side else latest.away_id
        club_line = (
            f'<p class="pl-club"><a class="club-link" '
            f'href="../clubs/{escape(club_id)}.html">{escape(name)}</a></p>'
            if club_id else f'<p class="pl-club">{escape(name)}</p>')
    return (
        '<div class="v2-mini-banner">'
        f'<p class="v2-season">{escape(kind.upper())}</p>'
        f'<h2 class="v2-mini-league">{escape(official.display_name.upper())}</h2>'
        f"{club_line}"
        "</div>"
    )


def _duty_row(d, show_role=True) -> str:
    """One match on an official's page: who played, what happened, their job.

    The same two-line shape as the player profile's match table — a caption
    carrying the date and the competition, then the row — because it is read on
    the same phone and has the same problem: six facts and 390 pixels.
    """
    date = render._format_date(d.date) if d.date else ""
    comp = (f'<a class="club-link" href="../{escape(d.slug)}/">'
            f'{escape(d.competition)}</a>' if d.slug else escape(d.competition))
    caption = " &middot; ".join(b for b in (escape(date), comp) if b)

    def team(name, club_id, emphasise):
        label = escape(name)
        inner = (f'<a class="club-link" href="../clubs/{escape(club_id)}.html">'
                 f"{label}</a>" if club_id else label)
        # The side a coach was on is the point of their row, so it is the one
        # that reads as theirs.
        return f"<strong>{inner}</strong>" if emphasise else inner

    fixture = (team(d.home_name, d.home_id, d.side is True) + " v "
               + team(d.away_name, d.away_id, d.side is False))
    score = escape(d.scoreline)
    if d.outcome:
        score = (f'<span class="pl-res pl-res-{d.outcome.lower()}">'
                 f'{escape(d.outcome)}</span> {score}')
    cols = 3 if show_role else 2
    role = f'<td class="pl-role">{escape(d.role)}</td>' if show_role else ""
    return (
        f'<tr class="pl-cap-row"><td colspan="{cols}">'
        f'<span class="v2-res-meta">{caption}</span></td></tr>'
        f'<tr class="pl-match-row">'
        f'<td class="pl-opp">{fixture}</td>'
        f'<td class="pl-score">{score}</td>'
        f"{role}"
        "</tr>"
    )


def _matches_table(career) -> str:
    """Every match, newest first.

    THE ROLE COLUMN ONLY EXISTS WHEN THE ROLE VARIES. A coach's every row says
    "Head coach" and most referees have only ever refereed, so on those pages
    the column is a third of a 390px table repeating one word — and dropping it
    is what leaves the two names of the fixture room to breathe. Same rule, and
    the same reason, as `show_side` on a player profile.
    """
    if not career:
        return ""
    show_role = len({d.role for d in career}) > 1
    head = (
        '<thead><tr>'
        '<th class="pl-th-opp">MATCH</th>'
        '<th class="pl-th-score">RES</th>'
        + ('<th class="pl-th-role">ROLE</th>' if show_role else "")
        + "</tr></thead>"
    )
    rows = [_duty_row(d, show_role) for d in career]
    shown, hidden = rows[:MATCH_ROWS_SHOWN], rows[MATCH_ROWS_SHOWN:]
    out = [
        '<h3 class="v2-sec-title">Matches</h3>',
        '<div class="v2-table-outer">'
        f'<table class="v2-standings pl-matches of-matches">{head}'
        f'<tbody>{"".join(shown)}</tbody></table></div>',
    ]
    if hidden:
        out.append(
            '<details class="pl-more">'
            f'<summary>{len(hidden)} earlier match'
            f'{"es" if len(hidden) != 1 else ""}</summary>'
            '<div class="v2-table-outer">'
            f'<table class="v2-standings pl-matches of-matches">{head}'
            f'<tbody>{"".join(hidden)}</tbody></table></div></details>'
        )
    return "".join(out)


def build_official_pages(dist, templates_dir, static_dir, ds, updated):
    """Write /officials/{official_id}.html for everyone named on a match.

    Returns the number written. Nothing here can fail a build: an official with
    no matches gets no page, and a match with no officials contributes nothing.
    """
    base = render._read(os.path.join(templates_dir, "base.html"))
    css_ver = render.css_version(static_dir)
    out_dir = os.path.join(dist, "officials")
    by_official = duties(ds)
    page_ids = official_page_ids(ds, by_official)
    if not page_ids:
        return 0
    os.makedirs(out_dir, exist_ok=True)

    count = 0
    for official_id in sorted(page_ids):
        official = ds.officials[official_id]
        career = by_official[official_id]
        content = "\n".join(part for part in [
            '<div class="v2-content">',
            _header(official, career),
            _tiles(official, career),
            _matches_table(career),
            "</div>",
        ] if part)
        # The same back link a player profile carries, and for the same reason:
        # every route here goes through a match, and back is where the reader
        # wants to go.
        html = hubs._page(base, official.display_name, content, updated,
                          css_ver, back=hubs.PLAYER_BACK)
        render._write(os.path.join(out_dir, f"{official_id}.html"), html)
        count += 1
    return count
