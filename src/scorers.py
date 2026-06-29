"""Goalscorer aggregation for the Super League. Pure functions, no I/O.

Kept separate from standings.py because goals are a Super-League-only concern;
the other leagues never call into here.
"""

from dataclasses import dataclass

TOP_N = 20       # minimum rows in the overall top-scorers table (ties extend it)
TEAM_TOP_N = 3   # scorers shown per team in the per-team breakdown


@dataclass
class ScorerTally:
    rank: int
    player_name: str
    team_code: str
    goals: int


def goals_by_match(goals) -> "dict[int, list]":
    """Group goals by match_id, each list sorted by minute (injury time aware)."""
    out: "dict[int, list]" = {}
    for g in goals:
        out.setdefault(g.match_id, []).append(g)
    for lst in out.values():
        lst.sort(key=lambda g: g.minute_sort)
    return out


def _tally(goals):
    """Map (player, team_code) -> goal count, excluding own goals."""
    counts: "dict[tuple[str, str], int]" = {}
    for g in goals:
        if g.is_own_goal:
            continue
        key = (g.player_name, g.team_code)
        counts[key] = counts.get(key, 0) + 1
    return counts


def top_scorers(goals):
    """Return (ranked_tallies, own_goal_total).

    Personal tallies exclude own goals but include penalties. Sorted by goals
    descending, then player name (A-Z). The list is at least TOP_N long; if there
    are ties at the cutoff every tied player is kept. Ranks use standard
    competition ranking (joint 2nd, joint 2nd, 4th).
    """
    counts = _tally(goals)
    ordered = sorted(
        counts.items(),
        key=lambda kv: (-kv[1], kv[0][0].lower()),
    )

    # Keep the top TOP_N, then extend through anyone tied on the cutoff's goals.
    if len(ordered) > TOP_N:
        cutoff_goals = ordered[TOP_N - 1][1]
        kept = [kv for kv in ordered if kv[1] >= cutoff_goals]
    else:
        kept = ordered

    tallies = []
    for i, ((player, team_code), n) in enumerate(kept):
        # Standard competition ranking: share the rank of the first tied player.
        if i > 0 and n == kept[i - 1][1]:
            rank = tallies[-1].rank
        else:
            rank = i + 1
        tallies.append(ScorerTally(rank, player, team_code, n))

    own_goal_total = sum(1 for g in goals if g.is_own_goal)
    return tallies, own_goal_total


def team_top_scorers(goals, teams):
    """Per-team top-TEAM_TOP_N scorers (own goals excluded).

    Returns a list of (team_code, team_name, [(player, goals), ...]) for every
    team that has at least one scorer, ordered by team name (A-Z). Within a team:
    goals descending, then player name (A-Z).
    """
    counts = _tally(goals)
    per_team: "dict[str, list]" = {}
    for (player, team_code), n in counts.items():
        per_team.setdefault(team_code, []).append((player, n))

    out = []
    for team_code, players in per_team.items():
        players.sort(key=lambda pc: (-pc[1], pc[0].lower()))
        name = teams[team_code].name if team_code in teams else team_code
        out.append((team_code, name, players[:TEAM_TOP_N]))
    out.sort(key=lambda t: t[1].lower())
    return out
