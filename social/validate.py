"""Data checks that run before anything is drawn.

The site's own `validate.py` already gates the build; this is the narrower
question a post has to answer: *is the data behind this specific board sound
enough to publish?* An error blocks the boards that touch the offending match
or competition — a plausible-looking wrong graphic is worse than no post,
because credibility is the whole product. Warnings are surfaced on the board's
card in the post pack and left to your judgement.
"""

import datetime
from dataclasses import dataclass, field

from src import dataset


@dataclass(frozen=True)
class Issue:
    message: str
    match_id: str = ""
    competition_id: str = ""


@dataclass
class Report:
    errors: "list[Issue]" = field(default_factory=list)
    warnings: "list[Issue]" = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.errors

    def blocked_matches(self) -> "set[str]":
        return {i.match_id for i in self.errors if i.match_id}

    def blocked_competitions(self) -> "set[str]":
        return {i.competition_id for i in self.errors if i.competition_id}

    def blocks(self, match_ids=(), competition_ids=()) -> "list[Issue]":
        """The errors that should stop a board built from these ids.

        A board that names its matches is judged on those matches only — a
        bad row in July must not block this weekend's card for the same
        competition. Boards derived from a whole competition instead (a table,
        a scorer chart) have no match list, so any error in that competition
        is theirs: it feeds the computation.
        """
        matches, competitions = set(match_ids), set(competition_ids)
        hits = []
        for issue in self.errors:
            if not issue.match_id and not issue.competition_id:
                hits.append(issue)          # whole-dataset problem
            elif matches:
                if issue.match_id in matches:
                    hits.append(issue)
            elif issue.competition_id and issue.competition_id in competitions:
                hits.append(issue)
        return hits


def run(ctx, window_days: int = 45) -> Report:
    """Validate the matches a run could plausibly touch, plus their goals.

    Scoped to a window rather than the whole history: a bad row in a 2025
    fixture must not block this weekend's results post, and the site build
    already refuses to deploy the dataset if it is broken overall.
    """
    report = Report()
    lo = (ctx.date - datetime.timedelta(days=window_days)).isoformat()
    hi = (ctx.date + datetime.timedelta(days=window_days)).isoformat()

    matches = {mid: m for mid, m in ctx.ds.matches.items()
               if not m.is_placeholder and m.date and lo <= m.date <= hi}

    _check_matches(ctx, matches, report)
    _check_goals(ctx, matches, report)
    _check_duplicates(matches, report)
    return report


def _check_matches(ctx, matches, report) -> None:
    for mid, m in matches.items():
        label = _label(ctx, m)

        if m.status in ("played", "awarded") and not m.has_score:
            report.errors.append(Issue(
                f"{label}: marked {m.status} but has no score",
                match_id=mid, competition_id=m.competition_id))

        season = ctx.ds.seasons.get(m.season_id)
        if season and not (season.start_date <= m.date <= season.end_date):
            report.errors.append(Issue(
                f"{label}: date {m.date} is outside season {season.label} "
                f"({season.start_date} to {season.end_date})",
                match_id=mid, competition_id=m.competition_id))

        for team_id in (m.home_team_id, m.away_team_id):
            if team_id not in ctx.ds.teams:
                report.errors.append(Issue(
                    f"{label}: team {team_id} is not in the teams tab",
                    match_id=mid, competition_id=m.competition_id))

        # ── warnings ──
        if not m.venue_id:
            report.warnings.append(Issue(f"{label}: no venue", match_id=mid))
        for team_id in (m.home_team_id, m.away_team_id):
            team = ctx.ds.teams.get(team_id)
            if team is None:
                continue
            from .data import crest_path
            if crest_path(team.legacy_code, team.club_id) is None:
                report.warnings.append(Issue(
                    f"no crest for {team.display_name} ({team.club_id})",
                    match_id=mid))


def _check_goals(ctx, matches, report) -> None:
    per_side: "dict[tuple[str, str], int]" = {}
    for gid, g in ctx.ds.goals.items():
        if g.match_id not in matches:
            # Either out of window or a placeholder match; the site validator
            # owns unknown-match references across the whole dataset.
            if g.match_id not in ctx.ds.matches:
                report.errors.append(Issue(
                    f"goal {gid} references unknown match {g.match_id}"))
            continue
        m = matches[g.match_id]
        label = _label(ctx, m)
        if g.team_id not in (m.home_team_id, m.away_team_id):
            report.errors.append(Issue(
                f"{label}: goal {gid} is credited to {g.team_id}, which is "
                f"not playing in this match",
                match_id=g.match_id, competition_id=m.competition_id))
            continue
        if g.player_id not in ctx.ds.players:
            report.errors.append(Issue(
                f"{label}: goal {gid} references unknown player {g.player_id}",
                match_id=g.match_id, competition_id=m.competition_id))
        if not g.minute:
            report.warnings.append(Issue(
                f"{label}: a goal has no recorded minute", match_id=g.match_id))
        per_side[(g.match_id, g.team_id)] = per_side.get(
            (g.match_id, g.team_id), 0) + 1

    for (mid, team_id), count in per_side.items():
        m = matches[mid]
        scored = m.home_goals if team_id == m.home_team_id else m.away_goals
        if scored is not None and count > scored:
            report.errors.append(Issue(
                f"{_label(ctx, m)}: {count} goal rows recorded for "
                f"{_team_name(ctx, team_id)} but the score says {scored}",
                match_id=mid, competition_id=m.competition_id))


def _check_duplicates(matches, report) -> None:
    """Two rows for the same fixture on the same day.

    Deliberately keyed on the date as well as the teams: a competition can
    legitimately hold a reverse fixture, a replay, or the second leg of a tie,
    and none of those share a date.
    """
    seen: "dict[tuple, str]" = {}
    for mid, m in matches.items():
        key = (m.competition_id, m.season_id, m.date,
               m.home_team_id, m.away_team_id)
        if key in seen:
            report.errors.append(Issue(
                f"duplicate fixture: {mid} and {seen[key]} are the same "
                f"match on {m.date}",
                match_id=mid, competition_id=m.competition_id))
        else:
            seen[key] = mid


def _team_name(ctx, team_id: str) -> str:
    team = ctx.ds.teams.get(team_id)
    return team.display_name if team else team_id


def _label(ctx, m: "dataset.Match") -> str:
    return (f"{_team_name(ctx, m.home_team_id)} v "
            f"{_team_name(ctx, m.away_team_id)} ({m.date})")
