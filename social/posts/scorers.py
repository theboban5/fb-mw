"""Golden boot — top scorers for one competition, ties shown as joint.

Own goals are never a scorer credit and unnamed scorers (CAF_MW_UNKNOWN) never
appear in a ranking; both rules come from the site's own aggregation, which
this reuses rather than reimplements.
"""

from .. import captions, config
from . import base


class Scorers(base.PostType):
    name = "scorers"

    DEFAULT_COMPETITION = "MW_SL"
    DEFAULT_TOP_N = 8

    def _competition(self, ctx) -> str:
        return ctx.options.get("competition") or self.DEFAULT_COMPETITION

    def _standings(self, ctx):
        cid = self._competition(ctx)
        if cid not in ctx.ds.competitions:
            return []
        top_n = int(ctx.options.get("top_n", self.DEFAULT_TOP_N))
        return ctx.top_scorers(cid, top_n=top_n)

    def is_available(self, ctx):
        cid = self._competition(ctx)
        if cid not in ctx.ds.competitions:
            return False, f"unknown competition {cid!r}"
        rows = self._standings(ctx)
        if not rows:
            return False, f"no goals recorded for {cid}"
        return True, ""

    def build(self, ctx):
        cid = self._competition(ctx)
        season_id = ctx.season_for(cid)
        rows = self._standings(ctx)
        slug = ctx.competition_slug(cid)
        competition = ctx.competition_name(cid, season_id)

        warnings = []
        # A club-less scorer means the tally could not be joined back to an
        # entry — worth surfacing rather than rendering a blank cell.
        for r in rows:
            if r.team is None:
                warnings.append(f"{r.name}: no club resolved for this tally")

        payload = captions.Payload(
            headline=f"{competition} top scorers",
            lines=[_caption_line(r) for r in rows],
            campaign="scorers",
            path=f"/{slug}/goalscorers.html",
            competition_ids=(cid,),
            emoji=config.EMOJI["ball"],
        )
        return [base.Draft(
            key=f"scorers-{slug}",
            post_type=self.name,
            template="scorers.html",
            context={
                "eyebrow": competition,
                "kind": "Top scorers",
                "standfirst_left": "Golden boot",
                "standfirst_right": f"Top {len(rows)}",
                "rows": rows,
                "season_label": _season_label_for(ctx, season_id),
            },
            payload=payload,
            alt_text=_alt_text(competition, rows),
            warnings=tuple(dict.fromkeys(warnings)),
        )]


def _season_label_for(ctx, season_id) -> str:
    from src import adapt
    return adapt.short_season_label(ctx.ds.seasons[season_id].label)


def _position(row) -> str:
    """'=2' marks a shared position, so a joint tally never reads as a rank."""
    return f"={row.position}" if row.joint else str(row.position)


def _caption_line(row) -> str:
    club = f" ({row.team.name})" if row.team else ""
    return f"{_position(row)}. {row.name}{club} — {row.goals}"


def _alt_text(competition, rows) -> str:
    listed = "; ".join(
        f"{_position(r)} {r.name}"
        f"{' of ' + r.team.name if r.team else ''}, {r.goals} goals"
        for r in rows)
    return f"{competition} top scorers. {listed}."


base.register(Scorers())
