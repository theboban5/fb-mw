"""Line-ups, shared by the league pipeline and the national-team one.

Both schemas record a team sheet the same way — one row per named player,
carrying a role (`starting | sub_on | unused_sub`), a shirt, a position, the
captain's armband, the three card states, and for a substitute the minute they
came on and whose name they replaced. `nt_lineups` got there first; the league
`lineups` tab (0018) copies it column for column precisely so this module can
serve both.

Nothing here imports a schema. Every function takes rows structurally — any
object with the attribute names above works — which is what lets
`nt.NTLineupRow` and `dataset.LineupRow` stay separate dataclasses in their own
modules while the folding and the markup live in one place. The alternative,
one shared row class, would have dragged the league's team_id FK into the NT
tab, where the opponent's side has nothing to resolve against.

Two halves:

  * **the model** — `fold()` turns a flat list of rows into starters,
    substitutions and unused subs, pairing a `sub_on` row to the starter it
    replaced by name (that is all either tab offers);
  * **the markup** — `lineup_row_html()` renders the collapsible block that
    sits under a result. One list in position order with each name tagged
    GK/DF/MF/FW, never a pitch diagram: it has to work in one column on a
    phone, which is where most of this site is read.

`player_href` is the one thing the two callers differ on, so it is a callback:
give it a `player_id` and it returns a URL or "". A row whose id does not
resolve renders as plain text, exactly as an unidentified scorer already does.
"""

from dataclasses import dataclass, replace
from html import escape

# Squad/line-up position vocabulary, in the order a team sheet is always read.
POSITIONS = ("GK", "DF", "MF", "FW")
POSITION_LABELS = {
    "GK": "Goalkeepers",
    "DF": "Defenders",
    "MF": "Midfielders",
    "FW": "Forwards",
}

# _enum lowercases before comparing, so a position check needs the lowercase
# vocabulary; parsed values are upper-cased straight back to GK/DF/MF/FW.
POSITION_SET = frozenset(p.lower() for p in POSITIONS)

ROLES = frozenset({"starting", "sub_on", "unused_sub"})


def id_sort(value: str) -> "tuple[int, int, str]":
    """Sort ids numerically when they are numbers, else lexically."""
    try:
        return (0, int(value), "")
    except ValueError:
        return (1, 0, value)


# ── Model ────────────────────────────────────────────────────────────────────

@dataclass(frozen=True)
class Substitution:
    on: object                  # the row coming on
    off: "object | None"        # None when the replaced player has no row
    minute: str

    @property
    def off_name(self) -> str:
        return self.off.player_name if self.off else self.on.replaced_player


@dataclass
class Lineup:
    """One match's team sheet, from one side's rows only."""
    starting: list
    substitutions: "list[Substitution]"
    unused: list

    @property
    def any_rows(self) -> bool:
        return bool(self.starting or self.substitutions or self.unused)

    def starting_by_position(self) -> "list[tuple[str, list]]":
        """[(label, rows)] in GK/DF/MF/FW order; empty groups omitted."""
        return group_by_position(self.starting)


@dataclass(frozen=True)
class Officials:
    """Everyone on a team-sheet graphic who is not a player (0023, 0024).

    Every field optional and blank on almost every match — the same shape as a
    kickoff or a venue and rendered by the same rule: what is known shows, what
    is not shows nothing at all.

    Each name comes with an id, and the pair means exactly what
    `lineups.player_name` + `lineups.player_id` mean: the name is what was
    reported, the id is who that turned out to be. A blank id is the ordinary
    case — nobody has tapped that name onto a registry row — and renders as
    plain text with no link, which is how an unidentified player already
    renders three lines further up the same block.
    """
    referee: str = ""
    assistant_referee_1: str = ""
    assistant_referee_2: str = ""
    fourth_official: str = ""
    home_coach: str = ""
    away_coach: str = ""
    referee_id: str = ""
    assistant_referee_1_id: str = ""
    assistant_referee_2_id: str = ""
    fourth_official_id: str = ""
    home_coach_id: str = ""
    away_coach_id: str = ""

    @property
    def crew(self) -> "list[tuple[str, list]]":
        """[(label, [(name, official_id), ...])] for the match officials.

        A list per label, not a joined string, because the two assistants share
        one label and each is a different person who may have their own page.
        The joining is the markup's job.
        """
        out = []
        if self.referee:
            out.append(("Referee", [(self.referee, self.referee_id)]))
        both = [(n, i) for n, i in (
            (self.assistant_referee_1, self.assistant_referee_1_id),
            (self.assistant_referee_2, self.assistant_referee_2_id)) if n]
        if both:
            out.append(("Assistant" + ("s" if len(both) > 1 else ""), both))
        if self.fourth_official:
            out.append(("Fourth official",
                        [(self.fourth_official, self.fourth_official_id)]))
        return out

    @property
    def any_officials(self) -> bool:
        return bool(self.crew)

    def coach_for(self, home: bool) -> "tuple[str, str]":
        """(name, official_id) for one side's coach; ("", "") when unnamed."""
        return ((self.home_coach, self.home_coach_id) if home
                else (self.away_coach, self.away_coach_id))


def _no_official_href(_official_id):
    return ""


def _official_html(name, official_id, official_href) -> str:
    """One official's name, linked to their page when the id resolves."""
    href = official_href(official_id or "")
    return (f'<a class="el-official-link" href="{escape(href)}">{escape(name)}</a>'
            if href else escape(name))


def coach_html(name, official_id="", official_href=_no_official_href) -> str:
    """The coach line under one side's sheet, or "" when nobody named one."""
    if not name:
        return ""
    return (f'<p class="el-coach"><span class="el-coach-l">Head coach</span>'
            f'{_official_html(name, official_id, official_href)}</p>')


def officials_html(officials, official_href=_no_official_href) -> str:
    """The referee line at the foot of a line-up block, or "".

    One paragraph rather than a table: it is three short facts on a phone, and
    a two-column table of three rows would be wider than the sheet above it.
    """
    if officials is None or not officials.any_officials:
        return ""
    items = "".join(
        f'<span class="el-official"><span class="el-official-l">'
        f'{escape(label)}</span>'
        + ", ".join(_official_html(n, i, official_href) for n, i in people)
        + "</span>"
        for label, people in officials.crew)
    return f'<p class="el-officials">{items}</p>'


def group_by_position(rows):
    """[(label, rows)] in GK -> DF -> MF -> FW order, by shirt within.

    **A row with no position is not dropped.** It ends up in a trailing group
    with a BLANK label, which the markup renders as a plain list under no
    heading. Position is optional on the league tab — a sheet routinely arrives
    as eleven names off a photo — and grouping only by position meant a whole
    starting XI could vanish under its own "Starting XI" heading, which is
    exactly what happened to the first league sheet ever entered.

    Sorting is by shirt number alone, and Python's sort is stable, so a sheet
    with no shirts keeps the order it was entered in — which is the order the
    team sheet itself was written in, and better than alphabetical. Adding
    player_name as a tiebreak (as this once did) threw that away for precisely
    the sheets that have nothing else to order by.
    """
    out = []
    for pos in POSITIONS:
        group = sorted((r for r in rows if r.position == pos),
                       key=lambda r: r.shirt_sort)
        if group:
            out.append((POSITION_LABELS[pos], group))
    rest = sorted((r for r in rows if r.position not in POSITION_LABELS),
                  key=lambda r: r.shirt_sort)
    if rest:
        out.append(("", rest))
    return out


def with_canonical_names(rows, name_of):
    """Rows again, with an identified player's name read from the registry.

    THE NAME ON A TEAM SHEET IS A LABEL ON AN ID, NOT A FACT ABOUT THE MATCH.
    `player_name` holds the name AS REPORTED, and what gets reported is
    whatever the graphic said — Malawian team-sheet graphics say "4. A.
    Josephy", first initial and surname, because that is what fits on the
    picture of a pitch. Rendering that column meant the site could never learn
    the first name: correcting `players` fixed the profile, the scorer table
    and the search index, and left every team sheet still saying "A. Josephy",
    so a player's own page disagreed with the line-up it was linked from.

    So an identified row renders the registry's name, exactly as a goal has
    since the schema was written ("goals.player_name is ignored entirely"),
    and the reported spelling stays in the database as the archive of what was
    actually typed. One rename now moves every page that names them.

    A row whose id resolves to nothing keeps what it has, which covers both
    cases that matter: a blank id (nobody has identified them yet) and an
    opponent's national-team id, which belongs to no registry and never will.

    `name_of` is a callback taking a player_id and returning a name or "" —
    the same shape as `player_href`, and for the same reason: the league
    schema and the nt_* schema resolve ids differently and neither belongs in
    this module.

    `replaced_player` moves with it. A substitution pairs BY NAME against this
    same side's `player_name` (that is all either tab records), so renaming one
    side of that comparison and not the other would unpair every substitution
    the first time anyone corrected a spelling. Both are rewritten through the
    same map, or neither is.
    """
    rows = list(rows)
    renames = {}
    for r in rows:
        name = name_of(getattr(r, "player_id", "") or "")
        if name and name != r.player_name:
            renames[r.player_name] = name
    if not renames:
        return rows
    return [replace(
        r,
        player_name=renames.get(r.player_name, r.player_name),
        replaced_player=renames.get(r.replaced_player, r.replaced_player),
    ) for r in rows]


def with_goals(rows, goals_of):
    """Rows again, each carrying what that player did in front of goal.

    WHAT WAS MISSING. A team sheet said who played and a separate block above
    it said who scored, and nothing joined the two — the one thing a reader
    scanning eleven names actually looks for. A ball beside the name is how
    every team-sheet graphic in the world says it.

    `goals_of` is a callback taking (player_id, player_name) and returning
    (goals, own_goals, assists), so this module keeps knowing nothing about
    either schema. The id is the join wherever there is one; the name is the fallback,
    which is the same pairing rule `fold` uses for a substitution and is what
    keeps a scorer nobody has identified from silently losing their ball.

    CALL IT AFTER `with_canonical_names`, never before. That function rewrites
    the reported name to the registry's, and a name-keyed lookup built from
    goals — which resolve through the same registry — would miss every renamed
    row if the two sides of the comparison were canonicalised at different
    times.

    Own goals are counted separately and are NOT goals. They belong to the
    scorer's own sheet (the goals tab credits the beneficiary team, so the
    caller does that flip) and render as their own marker: a ball on the wrong
    end. Adding them to the tally would put a player top of a sheet for a
    mistake, which is the same reason they have never appeared in a scorer
    table.

    Assists come the same way and land beside the same name. They used to be
    named in brackets after the scorer on the result line, which put two people
    inside what a reader scans as one fact; here the credit sits on the person
    who earned it. A sheet is the ONLY place they render now — where nobody has
    entered one, the assist is still counted on the assister's own profile,
    because that is a fact about the player rather than about the goal.
    """
    rows = list(rows)
    out = []
    for r in rows:
        goals, own, assists = goals_of(
            getattr(r, "player_id", "") or "", r.player_name)
        out.append(replace(r, goals=goals, own_goals=own, assists=assists)
                   if (goals or own or assists) else r)
    return out


def fold(rows) -> "Lineup | None":
    """Fold one side's rows into starters, substitutions and unused subs.

    `replaced_player` is a name, so a substitution pairs by name against the
    same side's rows (that is all either tab offers). A sub_on row whose name
    does not match anything still renders — with the raw name and no minute
    from the other side — rather than being dropped.
    """
    if not rows:
        return None
    by_name = {r.player_name: r for r in rows}
    starting = [r for r in rows if r.role == "starting"]
    unused = [r for r in rows if r.role == "unused_sub"]
    subs = []
    for r in sorted((r for r in rows if r.role == "sub_on"),
                    key=lambda r: (id_sort(r.minute_on), r.player_name)):
        off = by_name.get(r.replaced_player) if r.replaced_player else None
        minute = r.minute_on or (off.minute_off if off else "")
        subs.append(Substitution(on=r, off=off, minute=minute))
    lineup = Lineup(starting=starting, substitutions=subs, unused=unused)
    return lineup if lineup.any_rows else None


# ── Markup ───────────────────────────────────────────────────────────────────

def cards_html(row) -> str:
    """Card markers for one player: yellow, second yellow, straight red."""
    out = []
    if row.yellow_card and not row.yellow_red_card:
        out.append(("el-card-y", "Yellow card"))
    if row.yellow_red_card:
        out.append(("el-card-yr", "Second yellow card"))
    if row.red_card:
        out.append(("el-card-r", "Red card"))
    return "".join(
        f'<span class="el-card {cls}" title="{label}" aria-label="{label}"></span>'
        for cls, label in out
    )


def contribution_badges(row) -> str:
    """What this player did in this match: a ball per goal, an A per assist.

    Counted rather than summarised ("x2") because at a glance the count IS the
    fact, and three balls read faster than a number on a line already carrying
    a shirt, a position and possibly a card. A hat-trick is the most this ever
    draws in practice.

    The assist is a letter and not a second ball on purpose — it has to be
    unmistakably not a goal, and it is the only mark on this line that says
    what someone did for somebody else.
    """
    goals = getattr(row, "goals", 0) or 0
    own = getattr(row, "own_goals", 0) or 0
    assists = getattr(row, "assists", 0) or 0
    ball = ('<span class="el-goal" title="Goal" aria-label="Goal"></span>')
    og = ('<span class="el-goal el-goal-og" title="Own goal" '
          'aria-label="Own goal"></span>')
    a = ('<span class="el-assist" title="Assist" aria-label="Assist">A</span>')
    return ball * goals + og * own + a * assists


def captain_badge(is_captain=False, is_vice=False) -> str:
    if is_captain:
        return '<span class="el-cap" title="Captain" aria-label="Captain">C</span>'
    if is_vice:
        return ('<span class="el-cap el-cap-vice" title="Vice-captain" '
                'aria-label="Vice-captain">VC</span>')
    return ""


def _no_href(_player_id):
    return ""


def _name_html(row, player_href) -> str:
    """The player's name, linked to their profile when the id resolves."""
    name = escape(row.player_name)
    href = player_href(getattr(row, "player_id", "") or "")
    return f'<a class="el-player-link" href="{escape(href)}">{name}</a>' if href else name


def player_html(row, off_minute="", player_href=_no_href,
                show_position=False) -> str:
    """One player on a team sheet: shirt, position, name, badges.

    THE POSITION IS A TAG ON THE LINE, NOT A HEADING OVER A GROUP. It used to
    be a heading, and on the sheets this site actually gets that read as a
    statement about the wrong people: position is optional here, so a graphic
    naming only the keeper produced "Goalkeepers" followed by all eleven names,
    the other ten sitting under a heading that did not describe them. A tag
    beside each name says exactly as much as is known about that one player and
    nothing at all about the next.

    `show_position` reserves the column, and is false when NOBODY on the sheet
    has a position — which is the common case outside the top flight. An empty
    column indented every name by a tag that was never coming.
    """
    shirt = (f'<span class="el-shirt">{escape(row.shirt_number)}</span>'
             if row.shirt_number else '<span class="el-shirt"></span>')
    # Rendered empty rather than omitted for the rows that have no position, so
    # the names stay in one column instead of stepping in and out.
    pos = (f'<span class="el-pos-tag">'
           f'{escape(row.position) if row.position in POSITION_LABELS else ""}'
           "</span>") if show_position else ""
    off = (f'<span class="el-off">&darr; {escape(off_minute)}\'</span>'
           if off_minute else "")
    return (
        '<li class="el-player">'
        f"{shirt}{pos}"
        f'<span class="el-player-name">{_name_html(row, player_href)}'
        f"{captain_badge(is_captain=row.captain)}{contribution_badges(row)}"
        f"{cards_html(row)}{off}</span>"
        "</li>"
    )


def lineup_body(lineup, player_href=_no_href) -> str:
    """The three blocks — XI, substitutions, unused — or "" when there is none."""
    if lineup is None:
        return ""
    parts = []

    if lineup.starting:
        # One list, in reading order — keepers, defenders, midfield, attack,
        # then anyone whose position nobody recorded. `starting_by_position`
        # still does the ordering; only the headings it used to draw are gone
        # (see player_html).
        show_position = any(r.position in POSITION_LABELS for r in lineup.starting)
        players = "".join(
            player_html(r, off_minute=r.minute_off, player_href=player_href,
                        show_position=show_position)
            for _label, rows in lineup.starting_by_position() for r in rows)
        parts.append(
            '<p class="el-lineup-title">Starting XI</p>'
            f'<ul class="el-players">{players}</ul>'
        )

    if lineup.substitutions:
        def _sub(s):
            minute = (f'<span class="el-sub-min">{escape(s.minute)}\'</span>'
                      if s.minute else '<span class="el-sub-min"></span>')
            # A substitution nobody was named for renders as the arrival alone.
            # "Felix Dumakude for" with nothing after it reads as a truncated
            # page rather than as a fact that was never recorded — and a
            # reporter naming three replacements out of four is the normal case.
            if s.off is not None or s.off_name:
                off = (f'<span class="el-sub-for"> for </span>'
                       f'<span class="el-sub-off">'
                       + (_name_html(s.off, player_href) if s.off
                          else escape(s.off_name))
                       + "</span>")
            else:
                off = ""
            return (
                '<li class="el-sub">' + minute
                + '<span class="el-sub-text">'
                f'<span class="el-sub-on">{_name_html(s.on, player_href)}</span>'
                f"{off}{contribution_badges(s.on)}{cards_html(s.on)}</span></li>"
            )
        items = "".join(_sub(s) for s in lineup.substitutions)
        parts.append(
            '<p class="el-lineup-title">Substitutions</p>'
            f'<ul class="el-subs">{items}</ul>'
        )

    if lineup.unused:
        names = ", ".join(
            _name_html(r, player_href)
            + (f" ({escape(r.shirt_number)})" if r.shirt_number else "")
            for r in sorted(lineup.unused, key=lambda r: (r.shirt_sort, r.player_name))
        )
        parts.append(
            '<p class="el-lineup-title el-lineup-title-quiet">Unused substitutes</p>'
            f'<p class="el-unused">{names}</p>'
        )
    return "".join(parts)


def lineup_row_html(lineup, player_href=_no_href, summary="Line-up",
                    colspan=3) -> str:
    """The collapsible line-up block as a results-table row, or "".

    Open by default: on the matches that do have this data it is the point of
    the page.
    """
    body = lineup_body(lineup, player_href)
    if not body:
        return ""
    return (
        f'<tr class="el-lineup-row"><td colspan="{colspan}">'
        '<details class="el-lineup">'
        f'<summary class="el-lineup-summary">{escape(summary)}</summary>'
        f'<div class="el-lineup-body">{body}</div>'
        "</details></td></tr>"
    )


def two_sided_row_html(home, away, home_name, away_name,
                       player_href=_no_href, colspan=3, officials=None,
                       official_href=_no_official_href) -> str:
    """Both sides' sheets in one collapsible block, for a league result.

    A league match has two real teams, so unlike the national-team page — where
    only our own rows are ever held — the block is titled per side. A match
    with only one side entered renders that side alone rather than nothing,
    because half a team sheet is still worth reading.

    `officials` (0023) rides in the same block: each side's coach under its
    sheet, where the graphic puts it, and the referee at the foot. A match with
    officials and NO sheet at all still opens the block — that is a real state
    (the result post named the referee, the line-up photo never appeared) and
    the alternative is throwing away the only thing anyone entered.
    """
    parts = []
    for lineup, name, is_home in ((home, home_name, True), (away, away_name, False)):
        body = lineup_body(lineup, player_href)
        coach = (coach_html(*officials.coach_for(is_home),
                            official_href=official_href) if officials else "")
        if body or coach:
            parts.append(f'<div class="el-lineup-side">'
                         f'<p class="el-lineup-side-name">{escape(name)}</p>'
                         f"{body}{coach}</div>")
    crew = officials_html(officials, official_href)
    if not parts and not crew:
        return ""
    # Named for what is actually inside it. A block holding only a referee is
    # not a line-up, and calling it one would read as a broken feature.
    summary = "Line-ups" if parts else "Match officials"
    return (
        f'<tr class="el-lineup-row"><td colspan="{colspan}">'
        '<details class="el-lineup">'
        f'<summary class="el-lineup-summary">{summary}</summary>'
        '<div class="el-lineup-body el-lineup-two">'
        f'{"".join(parts)}{crew}</div>'
        "</details></td></tr>"
    )
