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

    @property
    def gd(self) -> int:
        return self.gf - self.ga

    @property
    def points(self) -> int:
        return self.won * 3 + self.drawn


def compute_standings(matches, teams) -> "list[Standing]":
    """Return standings rows, fully sorted.

    Win = 3, draw = 1, loss = 0. Only matches with both goal values present
    are counted. Sort: points desc, GD desc, GF desc, then name (A-Z).
    Teams with no played matches still appear, with zeros.
    """
    table = {code: Standing(code, t.name) for code, t in teams.items()}
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
