"""Results — one board per competition, or one hero board for --match-id."""

import datetime

from .. import captions, config, data
from . import base


class Results(base.PostType):
    name = "results"

    # Matches played on the run date or the day before, so a Sunday-morning
    # run still catches Saturday's football. Overridable with --days.
    DEFAULT_DAYS = 1

    def _matches(self, ctx) -> "list[data.MatchView]":
        match_id = ctx.options.get("match_id")
        if match_id:
            if match_id not in ctx.ds.matches:
                return []
            m = ctx.match(match_id)
            return [m] if m.played else []
        days = int(ctx.options.get("days", self.DEFAULT_DAYS))
        start = ctx.date - datetime.timedelta(days=days)
        return ctx.matches_between(start, ctx.date, played=True)

    def is_available(self, ctx):
        matches = self._matches(ctx)
        if ctx.options.get("match_id") and not matches:
            return False, (f"match {ctx.options['match_id']!r} is not a played "
                           f"match in the data")
        if not matches:
            return False, "no results in the window"
        return True, ""

    def build(self, ctx):
        matches = self._matches(ctx)
        drafts = []
        for competition_id, group in _by_competition(matches):
            # One competition can still overflow a board: split rather than
            # shrink the scoreline past thumbnail legibility.
            for page, chunk in enumerate(base.paginate(group), start=1):
                drafts.append(self._draft(ctx, competition_id, chunk, page,
                                          pages=len(base.paginate(group))))
        return drafts

    def _draft(self, ctx, competition_id, matches, page, pages):
        slug = ctx.competition_slug(competition_id)
        first = matches[0]
        key = f"results-{slug}" + (f"-{page}" if pages > 1 else "")
        day_label = _day_label(matches)
        density = base.density_for(len(matches))

        warnings = []
        for m in matches:
            for side in (m.home, m.away):
                if side.unrecorded and not side.scorers:
                    warnings.append(
                        f"{m.home.team.name} v {m.away.team.name}: "
                        f"{side.unrecorded} goal(s) for {side.team.name} have "
                        f"no recorded scorer")
            if not m.home.team.has_crest:
                warnings.append(f"no crest for {m.home.team.name}")
            if not m.away.team.has_crest:
                warnings.append(f"no crest for {m.away.team.name}")

        # Deep-link to the matchday the results belong to, not the homepage.
        path = f"/matches/{first.date}.html" if len({m.date for m in matches}) == 1 else "/matches/"

        payload = captions.Payload(
            headline=f"{first.competition} — {day_label}",
            lines=[_caption_line(m) for m in matches],
            campaign="results",
            path=path,
            competition_ids=(competition_id,),
            emoji=config.EMOJI["ball"],
        )
        return base.Draft(
            key=key,
            post_type=self.name,
            template="results.html",
            context={
                "eyebrow": first.competition,
                "kind": "Full time" if len(matches) == 1 else "Results",
                "standfirst_left": day_label,
                "standfirst_right": _stage_label(matches, page, pages),
                "matches": matches,
                "density": density,
                "season_label": _season_label(ctx, first),
            },
            payload=payload,
            alt_text=_alt_text(first.competition, day_label, matches),
            warnings=tuple(dict.fromkeys(warnings)),
            match_ids=tuple(m.match_id for m in matches),
        )


def _by_competition(matches):
    """Group preserving first-appearance order (which is date, then name)."""
    order, groups = [], {}
    for m in matches:
        if m.competition_id not in groups:
            groups[m.competition_id] = []
            order.append(m.competition_id)
        groups[m.competition_id].append(m)
    return [(cid, groups[cid]) for cid in order]


def _day_label(matches) -> str:
    dates = sorted({m.date for m in matches})
    if len(dates) == 1:
        return data.format_day(dates[0])
    return f"{data.format_date(dates[0])} – {data.format_date(dates[-1])}"


def _stage_label(matches, page, pages) -> str:
    if pages > 1:
        return f"Part {page} of {pages}"
    stages = {m.stage_label for m in matches if m.stage_label}
    return stages.pop() if len(stages) == 1 else ""


def _season_label(ctx, match) -> str:
    from src import adapt
    return adapt.short_season_label(ctx.ds.seasons[match.season_id].label)


def _scorer_text(side) -> str:
    """'Daah 53', Kalondole 90+3'', with unnamed goals declared."""
    parts = [s.label for s in side.scorers]
    if parts and side.unrecorded:
        parts.append(f"+{side.unrecorded} not recorded")
    return ", ".join(parts)


def _caption_line(m) -> str:
    """One match as one caption entry — kept whole so X truncation drops
    complete matches rather than orphaning a scorer from its scoreline."""
    line = f"{m.home.team.name} {m.home.goals}-{m.away.goals} {m.away.team.name}"
    home, away = _scorer_text(m.home), _scorer_text(m.away)
    if home and away:
        line += f"\n{home} — {away}"
    elif home or away:
        line += f"\n{home or away}"
    if m.shootout:
        line += f"\n{m.shootout}"
    return line


def _alt_text(competition, day_label, matches) -> str:
    scores = "; ".join(
        f"{m.home.team.name} {m.home.goals}, {m.away.team.name} {m.away.goals}"
        for m in matches)
    return f"{competition} results, {day_label}. {scores}."


base.register(Results())
