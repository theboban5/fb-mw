"""Fixtures — what is coming up, one board per competition.

House rule: a fixture card states who, when and where. No framing of a match
as must-win, must-watch or decisive — that is editorial, and this module does
not do editorial.
"""

import datetime

from .. import captions, config, data
from . import base
from .results import _by_competition, _season_label


class Fixtures(base.PostType):
    name = "fixtures"

    DEFAULT_DAYS = 3

    def _matches(self, ctx):
        days = int(ctx.options.get("days", self.DEFAULT_DAYS))
        end = ctx.date + datetime.timedelta(days=days)
        matches = ctx.matches_between(ctx.date, end, played=False)
        competition = ctx.options.get("competition")
        if competition:
            matches = [m for m in matches if m.competition_id == competition]
        return matches

    def is_available(self, ctx):
        if not self._matches(ctx):
            days = int(ctx.options.get("days", self.DEFAULT_DAYS))
            return False, f"no scheduled fixtures in the next {days} days"
        return True, ""

    def build(self, ctx):
        drafts = []
        for competition_id, group in _by_competition(self._matches(ctx)):
            pages = base.paginate(group)
            for page, chunk in enumerate(pages, start=1):
                drafts.append(self._draft(ctx, competition_id, chunk, page,
                                          len(pages)))
        return drafts

    def _draft(self, ctx, competition_id, matches, page, pages):
        slug = ctx.competition_slug(competition_id)
        first = matches[0]
        key = f"fixtures-{slug}" + (f"-{page}" if pages > 1 else "")

        warnings = []
        for m in matches:
            if not m.kickoff:
                warnings.append(
                    f"{m.home.team.name} v {m.away.team.name}: no kickoff time")
            if not m.venue:
                warnings.append(
                    f"{m.home.team.name} v {m.away.team.name}: no venue")
            for team in (m.home.team, m.away.team):
                if not team.has_crest:
                    warnings.append(f"no crest for {team.name}")

        payload = captions.Payload(
            headline=f"{first.competition} — {_window_label(matches)}",
            lines=[_caption_line(m) for m in matches],
            note="All times CAT.",
            campaign="fixtures",
            path=f"/matches/{first.date}.html" if len({m.date for m in matches}) == 1 else "/matches/",
            competition_ids=(competition_id,),
        )
        return base.Draft(
            key=key,
            post_type=self.name,
            template="fixtures.html",
            context={
                "eyebrow": first.competition,
                "kind": "Fixtures",
                "standfirst_left": _window_label(matches),
                "standfirst_right": (f"Part {page} of {pages}" if pages > 1
                                     else "All times CAT"),
                "matches": matches,
                "density": base.density_for(len(matches)),
                "season_label": _season_label(ctx, first),
            },
            payload=payload,
            alt_text=_alt_text(first.competition, matches),
            warnings=tuple(dict.fromkeys(warnings)),
            match_ids=tuple(m.match_id for m in matches),
        )


def _window_label(matches) -> str:
    dates = sorted({m.date for m in matches})
    if len(dates) == 1:
        return data.format_day(dates[0])
    return f"{data.format_date(dates[0])} – {data.format_date(dates[-1])}"


def _when(m) -> str:
    """'Sat 15 Aug, 15:00 CAT, Kamuzu Stadium' — absolute, never 'tomorrow'."""
    parts = [data.format_short(m.date)]
    if m.kickoff:
        parts.append(f"{m.kickoff} {config.TZ_LABEL}")
    if m.venue:
        parts.append(m.venue)
    return ", ".join(parts)


def _caption_line(m) -> str:
    return f"{m.home.team.name} v {m.away.team.name}\n{_when(m)}"


def _alt_text(competition, matches) -> str:
    lines = "; ".join(
        f"{m.home.team.name} versus {m.away.team.name}, {_when(m)}"
        for m in matches)
    return f"{competition} fixtures. {lines}."


base.register(Fixtures())
