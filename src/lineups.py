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
    sits under a result. A grouped list by position, never a pitch diagram: it
    has to work in one column on a phone, which is where most of this site is
    read.

`player_href` is the one thing the two callers differ on, so it is a callback:
give it a `player_id` and it returns a URL or "". A row whose id does not
resolve renders as plain text, exactly as an unidentified scorer already does.
"""

from dataclasses import dataclass
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


def group_by_position(rows):
    """Group squad/line-up rows GK -> DF -> MF -> FW, by shirt number within."""
    out = []
    for pos in POSITIONS:
        group = sorted((r for r in rows if r.position == pos),
                       key=lambda r: (r.shirt_sort, r.player_name))
        if group:
            out.append((POSITION_LABELS[pos], group))
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


def player_html(row, off_minute="", player_href=_no_href) -> str:
    shirt = (f'<span class="el-shirt">{escape(row.shirt_number)}</span>'
             if row.shirt_number else '<span class="el-shirt"></span>')
    off = (f'<span class="el-off">&darr; {escape(off_minute)}\'</span>'
           if off_minute else "")
    return (
        '<li class="el-player">'
        f"{shirt}"
        f'<span class="el-player-name">{_name_html(row, player_href)}'
        f"{captain_badge(is_captain=row.captain)}{cards_html(row)}{off}</span>"
        "</li>"
    )


def lineup_body(lineup, player_href=_no_href) -> str:
    """The three blocks — XI, substitutions, unused — or "" when there is none."""
    if lineup is None:
        return ""
    parts = []

    if lineup.starting:
        blocks = []
        for label, rows in lineup.starting_by_position():
            players = "".join(
                player_html(r, off_minute=r.minute_off, player_href=player_href)
                for r in rows)
            blocks.append(
                f'<div class="el-pos-block">'
                f'<p class="el-pos-title">{escape(label)}</p>'
                f'<ul class="el-players">{players}</ul></div>'
            )
        parts.append(
            '<p class="el-lineup-title">Starting XI</p>'
            f'<div class="el-lineup-grid">{"".join(blocks)}</div>'
        )

    if lineup.substitutions:
        items = "".join(
            '<li class="el-sub">'
            + (f'<span class="el-sub-min">{escape(s.minute)}\'</span>'
               if s.minute else '<span class="el-sub-min"></span>')
            + f'<span class="el-sub-text">'
            f'<span class="el-sub-on">{_name_html(s.on, player_href)}</span>'
            f'<span class="el-sub-for"> for </span>'
            f'<span class="el-sub-off">'
            + (_name_html(s.off, player_href) if s.off else escape(s.off_name))
            + "</span>"
            f"{cards_html(s.on)}</span></li>"
            for s in lineup.substitutions
        )
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
                       player_href=_no_href, colspan=3) -> str:
    """Both sides' sheets in one collapsible block, for a league result.

    A league match has two real teams, so unlike the national-team page — where
    only our own rows are ever held — the block is titled per side. A match
    with only one side entered renders that side alone rather than nothing,
    because half a team sheet is still worth reading.
    """
    parts = []
    for lineup, name in ((home, home_name), (away, away_name)):
        body = lineup_body(lineup, player_href)
        if body:
            parts.append(f'<div class="el-lineup-side">'
                         f'<p class="el-lineup-side-name">{escape(name)}</p>'
                         f"{body}</div>")
    if not parts:
        return ""
    return (
        f'<tr class="el-lineup-row"><td colspan="{colspan}">'
        '<details class="el-lineup">'
        '<summary class="el-lineup-summary">Line-ups</summary>'
        f'<div class="el-lineup-body el-lineup-two">{"".join(parts)}</div>'
        "</details></td></tr>"
    )
