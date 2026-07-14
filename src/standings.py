"""League standings computation. Pure functions, no I/O — easy to test."""

from dataclasses import dataclass


@dataclass
class Standing:
    code: str
    name: str
    played: int = 0
    won: int = 0
    drawn: int = 0
    lost: int = 0
    gf: int = 0
    ga: int = 0
    points_win: int = 3
    points_draw: int = 1
    adjustment: int = 0    # entries.points_adjustment; can be negative

    @property
    def gd(self) -> int:
        return self.gf - self.ga

    @property
    def points(self) -> int:
        return self.won * self.points_win + self.drawn * self.points_draw + self.adjustment


def compute_standings(matches, teams, points_win=3, points_draw=1,
                      adjustments=None) -> "list[Standing]":
    """Return standings rows, fully sorted.

    Points per win/draw default to 3/1 but come from competition_seasons in
    the new schema; `adjustments` maps team code -> points_adjustment. Only
    matches with both goal values present are counted. Sort: points desc,
    GD desc, GF desc, then name (A-Z). Teams with no played matches still
    appear, with zeros.
    """
    adjustments = adjustments or {}
    table = {
        code: Standing(code, t.name, points_win=points_win,
                       points_draw=points_draw,
                       adjustment=adjustments.get(code, 0))
        for code, t in teams.items()
    }
    for m in matches:
        if not m.played:
            continue
        home = table[m.home_code]
        away = table[m.away_code]
        home.played += 1
        away.played += 1
        home.gf += m.home_goals
        home.ga += m.away_goals
        away.gf += m.away_goals
        away.ga += m.home_goals
        if m.home_goals > m.away_goals:
            home.won += 1
            away.lost += 1
        elif m.home_goals < m.away_goals:
            away.won += 1
            home.lost += 1
        else:
            home.drawn += 1
            away.drawn += 1

    return sorted(
        table.values(),
        key=lambda s: (-s.points, -s.gd, -s.gf, s.name.lower()),
    )


def _outcome(home_goals, away_goals):
    """('W','L') if the home team won, ('L','W') if it lost, ('D','D') if drawn."""
    if home_goals > away_goals:
        return "W", "L"
    if home_goals < away_goals:
        return "L", "W"
    return "D", "D"


def recent_form(matches, teams, last_n=5):
    """Map each team code to its last `last_n` results, oldest first.

    Each result is 'W', 'D' or 'L' from that team's point of view. Teams with
    fewer than `last_n` played matches get a shorter list (possibly empty).
    """
    ordered = sorted(
        (m for m in matches if m.played),
        key=lambda m: (m.matchday, m.date, m.row),
    )
    form = {code: [] for code in teams}
    for m in ordered:
        home_res, away_res = _outcome(m.home_goals, m.away_goals)
        form[m.home_code].append(home_res)
        form[m.away_code].append(away_res)
    return {code: results[-last_n:] for code, results in form.items()}


def _positions_through(matches, teams, matchday, **table_kwargs):
    """Rank of every team (1-based) using only matches up to `matchday`."""
    subset = [m for m in matches if m.matchday <= matchday]
    rows = compute_standings(subset, teams, **table_kwargs)
    return {s.code: i for i, s in enumerate(rows, start=1)}


def position_changes(matches, teams, **table_kwargs):
    """Map each team code to 'up', 'down' or 'same' versus the previous matchday.

    Compares the table after the latest played matchday with the table after the
    one before it. With fewer than two played matchdays there is nothing to
    compare against, so every team is reported as 'same'.
    """
    days = sorted({m.matchday for m in matches if m.played})
    if len(days) < 2:
        return {code: "same" for code in teams}
    cur = _positions_through(matches, teams, days[-1], **table_kwargs)
    prev = _positions_through(matches, teams, days[-2], **table_kwargs)
    out = {}
    for code in teams:
        # A smaller number is a higher position, so moving up means cur < prev.
        if cur[code] < prev[code]:
            out[code] = "up"
        elif cur[code] > prev[code]:
            out[code] = "down"
        else:
            out[code] = "same"
    return out


def position_history(matches, teams, **table_kwargs):
    """Return (matchdays, {code: [position, ...]}) — a team's rank after each
    played matchday, for plotting position over the season.

    `matchdays` is the sorted list of played matchday numbers; each team's list
    has one position per entry, aligned to that list.
    """
    days = sorted({m.matchday for m in matches if m.played})
    history = {code: [] for code in teams}
    for d in days:
        pos = _positions_through(matches, teams, d, **table_kwargs)
        for code in teams:
            history[code].append(pos[code])
    return days, history
