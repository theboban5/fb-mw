"""League table — computed standings plus one derived stat line.

Standings are computed from `matches` by the site's own module, never
hardcoded and never copied from anywhere. Cups have no table and are refused.
"""

from .. import captions, config
from . import base


class Table(base.PostType):
    name = "table"

    DEFAULT_COMPETITION = "MW_SL"

    def _competition(self, ctx) -> str:
        return ctx.options.get("competition") or self.DEFAULT_COMPETITION

    def is_available(self, ctx):
        cid = self._competition(ctx)
        if cid not in ctx.ds.competitions:
            return False, f"unknown competition {cid!r}"
        if ctx.ds.competitions[cid].type != "league":
            return False, f"{cid} is a cup — a knockout has no table"
        rows = ctx.table(cid)
        if not any(r.played for r in rows):
            return False, f"no matches played yet in {cid}"
        return True, ""

    def build(self, ctx):
        cid = self._competition(ctx)
        season_id = ctx.season_for(cid)
        rows = ctx.table(cid)
        slug = ctx.competition_slug(cid)
        competition = ctx.competition_name(cid, season_id)
        stat = _stat_line(rows)

        payload = captions.Payload(
            headline=f"{competition} table",
            lines=[_caption_line(r) for r in rows],
            note=stat,
            campaign="table",
            path=f"/{slug}/",
            competition_ids=(cid,),
            emoji=config.EMOJI["chart"],
        )
        return [base.Draft(
            key=f"table-{slug}",
            post_type=self.name,
            template="table.html",
            context={
                "eyebrow": competition,
                "kind": "Table",
                "standfirst_left": "Standings",
                "standfirst_right": f"After {max(r.played for r in rows)} matches",
                "rows": rows,
                "stat": stat,
                "season_label": _season_label_for(ctx, season_id),
            },
            payload=payload,
            alt_text=_alt_text(competition, rows),
            warnings=(),
        )]


def _season_label_for(ctx, season_id) -> str:
    from src import adapt
    return adapt.short_season_label(ctx.ds.seasons[season_id].label)


def _stat_line(rows) -> str:
    """One derived line: best form over the last five, stated as a fact.

    Points from the last five matches, ties broken by the letters themselves
    then alphabetically, so the same table always yields the same line. No
    'flying high', no 'in trouble' — the table already says what happened.
    """
    scored = []
    for r in rows:
        if len(r.form) < 5:
            continue
        points = sum({"W": 3, "D": 1}.get(x, 0) for x in r.form[-5:])
        scored.append((-points, r.team.name, r))
    if not scored:
        return ""
    scored.sort()
    points, _name, row = scored[0]
    return (f"Best form over the last five: {row.team.name}, "
            f"{' '.join(row.form[-5:])} ({-points} points).")


def _caption_line(row) -> str:
    return (f"{row.position}. {row.team.name} — {row.points} pts "
            f"({row.played} played, {_signed(row.goal_difference)} GD)")


def _signed(value: int) -> str:
    return f"+{value}" if value > 0 else str(value)


def _alt_text(competition, rows) -> str:
    listed = "; ".join(
        f"{r.position} {r.team.name}, {r.points} points from {r.played} matches"
        for r in rows)
    return f"{competition} table. {listed}."


base.register(Table())
