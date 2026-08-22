"""Round-up — every result in a date range on one board, grouped by competition.

This is the mixed board: `results` gives one clean card per competition,
roundup gives the whole weekend at a glance. Rows are compact and carry no
scorers — the detail lives on the per-competition cards and on the site.
"""

import datetime

from .. import captions, config, data
from . import base
from .results import _by_competition, _season_label


class Roundup(base.PostType):
    name = "roundup"

    # Rows including group headers. Past this a 1080px board stops being
    # readable in a WhatsApp thumbnail, so it splits.
    MAX_MATCHES = 12

    def _window(self, ctx) -> "tuple[datetime.date, datetime.date]":
        """The most recent Friday–Sunday, or an explicit --days window.

        Counted back from the run date, so a Monday run reports the weekend
        just gone rather than an empty one just starting.
        """
        if ctx.options.get("days"):
            days = int(ctx.options["days"])
            return ctx.date - datetime.timedelta(days=days), ctx.date
        # weekday(): Mon=0 ... Fri=4, Sun=6.
        back = (ctx.date.weekday() - 4) % 7
        friday = ctx.date - datetime.timedelta(days=back)
        if friday > ctx.date:
            friday -= datetime.timedelta(days=7)
        sunday = min(friday + datetime.timedelta(days=2), ctx.date)
        return friday, sunday

    def _matches(self, ctx):
        start, end = self._window(ctx)
        return ctx.matches_between(start, end, played=True)

    def is_available(self, ctx):
        matches = self._matches(ctx)
        if not matches:
            start, end = self._window(ctx)
            return False, (f"no results between {start.isoformat()} and "
                           f"{end.isoformat()}")
        if len({m.competition_id for m in matches}) < 2:
            # One competition is a `results` card, not a round-up. Building
            # both would post the same football twice.
            return False, "only one competition has results; use `results`"
        return True, ""

    def build(self, ctx):
        matches = self._matches(ctx)
        start, end = self._window(ctx)
        window = _window_label(start, end)
        pages = base.paginate(matches, self.MAX_MATCHES)
        drafts = []
        for page, chunk in enumerate(pages, start=1):
            drafts.append(self._draft(ctx, chunk, window, page, len(pages)))
        return drafts

    def _draft(self, ctx, matches, window, page, pages):
        # Tier order, not first-appearance order: on a mixed board the top
        # flight leads. Tier comes from the competitions tab, so this is the
        # pyramid's own ordering rather than an editorial judgement.
        groups = [
            {
                "competition": group[0].competition,
                "matches": group,
            }
            for _cid, group in sorted(
                _by_competition(matches),
                key=lambda pair: _tier_key(ctx, pair[0]))
        ]
        key = "roundup" + (f"-{page}" if pages > 1 else "")
        competition_ids = tuple(dict.fromkeys(m.competition_id for m in matches))
        table_line = _table_line(ctx, matches)

        # One entry per competition, kept whole so X truncation drops a whole
        # competition rather than half its results. Platform markup (WhatsApp
        # bold and so on) is captions.py's job, not this module's.
        lines = []
        for g in groups:
            lines.append("\n".join(
                [g["competition"]] +
                [f"{m.home.team.name} {m.home.goals}-{m.away.goals} "
                 f"{m.away.team.name}" for m in g["matches"]]))

        payload = captions.Payload(
            headline=f"Malawi football round-up — {window}",
            lines=lines,
            note=table_line,
            campaign="roundup",
            path="/matches/",
            competition_ids=competition_ids,
            emoji=config.EMOJI["ball"],
        )
        return base.Draft(
            key=key,
            post_type=self.name,
            template="roundup.html",
            context={
                "eyebrow": "Round-up",
                "kind": f"Part {page} of {pages}" if pages > 1 else "Results",
                "standfirst_left": window,
                "standfirst_right": f"{len(matches)} matches",
                "groups": groups,
                "table_line": table_line,
                "season_label": _season_label(ctx, matches[0]),
            },
            payload=payload,
            alt_text=_alt_text(window, groups),
            warnings=(),
            match_ids=tuple(m.match_id for m in matches),
        )


def _tier_key(ctx, competition_id) -> "tuple":
    """Tier first (a blank tier sorts last), then name, so the order is stable."""
    comp = ctx.ds.competitions[competition_id]
    return (comp.tier if comp.tier is not None else 99, comp.name)


def _window_label(start, end) -> str:
    if start == end:
        return data.format_day(start)
    if start.year == end.year and start.month == end.month:
        return f"{start.day}–{end.day} {end.strftime('%b %Y')}"
    return f"{data.format_date(start)} – {data.format_date(end)}"


def _table_line(ctx, matches) -> str:
    """One factual line of table context, for the highest-tier league present.

    Stated as standings, not as a story: who leads and on how many points.
    Cups have no table and are skipped.
    """
    leagues = []
    for cid in dict.fromkeys(m.competition_id for m in matches):
        comp = ctx.ds.competitions[cid]
        if comp.type != "league":
            continue
        leagues.append((comp.tier if comp.tier is not None else 99, cid))
    if not leagues:
        return ""
    _tier, cid = min(leagues)
    try:
        rows = ctx.table(cid)
    except Exception:
        return ""
    if not rows or not rows[0].played:
        return ""
    top = rows[0]
    name = ctx.competition_name(cid, ctx.season_for(cid))
    # rows[0] is the top of the FIRST table, which in a competition played in
    # clusters is one of several leaders — so the cluster is named. Saying
    # "lead the NRFA Division Two League" of a team that leads Cluster A would
    # be a claim about thirty-one teams it has not played.
    where = f"{top.group} of the {name}" if top.group else f"the {name}"
    return (f"{top.team.name} lead {where} on {top.points} points "
            f"from {top.played} matches.")


def _alt_text(window, groups) -> str:
    parts = []
    for g in groups:
        scores = "; ".join(
            f"{m.home.team.name} {m.home.goals}, {m.away.team.name} {m.away.goals}"
            for m in g["matches"])
        parts.append(f"{g['competition']}: {scores}")
    return f"Malawi football results, {window}. " + ". ".join(parts) + "."


base.register(Roundup())
