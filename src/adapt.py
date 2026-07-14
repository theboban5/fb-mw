"""Adapt the new normalized schema to the shapes the site renderer consumes.

The renderer (src/render.py), standings and scorers modules were written for
the old per-league sheets: teams keyed by a short code, flat match rows, goals
with resolved player names. This module produces exactly those shapes from a
parsed Dataset, one league (competition+season) at a time, so the pages stay
pixel-identical while the data source changes underneath.

Conventions preserved deliberately:
  * The team "code" is `teams.legacy_code` (SL_BE, CRFA_AR, ...). Club page
    URLs (/{slug}/clubs/SL_BE.html) and logo lookups are keyed by it, and
    those URLs are live — do not switch them to team_id.
  * `source_type=placeholder` matches (and their goals) are dropped here, so
    nothing downstream can ever render them.
  * Scorer names resolve via player_id -> players; CAF_MW_UNKNOWN scorer
    lines are dropped from display/rankings (own-goal totals still count
    them via LeagueData.own_goal_total).
"""

from dataclasses import dataclass, field

from . import dataset, standings

# competition_id -> live URL slug. These URLs are public and must not change.
# A competition not listed here gets the same derivation the originals used:
# the id minus its country prefix, lowercased (MW_WP -> wp).
COMPETITION_SLUGS = {
    "MW_SL": "sl",
    "MW_NDL": "ndl",
    "MW_SRFA": "srfa",
    "MW_CRFA": "crfa",
    "MW_NRFA": "nrfa",
    "MW_SRFA2": "srfa2",
    "MW_WP": "wp",
    "MW_KU19": "ku19",
    "MW_U16": "u16",
}


def competition_slug(competition_id: str, country: str = "mw") -> str:
    if competition_id in COMPETITION_SLUGS:
        return COMPETITION_SLUGS[competition_id]
    prefix = f"{country.upper()}_"
    slug = competition_id[len(prefix):] if competition_id.startswith(prefix) else competition_id
    return slug.lower().replace("_", "-")


def short_season_label(label: str) -> str:
    """'2026/27' -> '26/27' (the form the site has always displayed)."""
    a, sep, b = label.partition("/")
    if sep and len(a) == 4 and a.isdigit():
        return f"{a[2:]}/{b}"
    return label


@dataclass(frozen=True)
class TeamView:
    """Old-schema team shape: code + display name (+ club linkage for logos)."""
    code: str            # legacy_code — the public URL / logo key
    name: str
    location: str = ""
    team_id: str = ""
    club_id: str = ""


@dataclass
class MatchView:
    """Old-schema match shape, plus the new-schema fields the renderer shows.

    `.played` means "carries a real score into tables/results": played or
    awarded. postponed/cancelled/abandoned render as fixtures with a status
    badge, never with a score.
    """
    row: int
    matchday: int
    date: str
    home_code: str
    away_code: str
    home_goals: "int | None"
    away_goals: "int | None"
    stadium: str = ""
    match_id: "str | None" = None
    status: str = "played"
    confidence: str = "confirmed"
    awarded_note: str = ""

    @property
    def played(self) -> bool:
        return (self.status in ("played", "awarded")
                and self.home_goals is not None and self.away_goals is not None)

    @property
    def status_badge(self) -> str:
        """Short label shown instead of a score, or "" for normal rows."""
        return {"postponed": "PPD", "cancelled": "CANC", "abandoned": "ABD"}.get(
            self.status, "")

    @property
    def unconfirmed(self) -> bool:
        return self.confidence == "unconfirmed"


@dataclass(frozen=True)
class GoalView:
    """Old-schema goal shape with the player name already resolved."""
    match_id: str
    team_code: str       # the team this goal counted FOR (own goals: beneficiary)
    player_name: str
    minute: str          # display string, "" when unknown
    goal_type: str = ""  # "" normal, "penalty", or "own goal"
    player_id: str = ""  # for scorer-table links to /players/{player_id}.html

    @property
    def is_own_goal(self) -> bool:
        return self.goal_type == "own goal"

    @property
    def is_penalty(self) -> bool:
        return self.goal_type == "penalty"

    @property
    def minute_sort(self) -> "tuple[int, int]":
        base, _, added = self.minute.partition("+")
        try:
            b = int(base.strip())
        except ValueError:
            return (10**6, 0)
        try:
            a = int(added.strip()) if added.strip() else 0
        except ValueError:
            a = 0
        return (b, a)

    @property
    def annotation(self) -> str:
        label = f"{self.player_name} {self.minute}'" if self.minute else self.player_name
        if self.is_penalty:
            label += " (P)"
        elif self.is_own_goal:
            label += " (OG)"
        return label


@dataclass
class LeagueData:
    """Everything one league page-set needs, in renderer-ready shape."""
    competition_id: str
    season_id: str
    slug: str
    league_name: str      # sponsor_name if set, else competitions.name
    season: str           # short label, e.g. "26/27"
    teams: "dict[str, TeamView]"
    matches: "list[MatchView]"
    goals: "list[GoalView]"
    points_win: int
    points_draw: int
    adjustments: "dict[str, int]"           # code -> points_adjustment (non-zero only)
    adjustment_reasons: "dict[str, str]"    # code -> reason (for footnotes)
    withdrawn: "dict[str, str]"             # code -> withdrawn|expelled
    own_goal_total: int                     # includes unknown-player own goals
    promotion_places: int = 0
    relegation_places: int = 0


def _goal_display_minute(g: "dataset.Goal") -> str:
    if not g.minute:
        return ""
    return f"{g.minute}+{g.stoppage}" if g.stoppage else g.minute


# new-schema goal_type -> the old display vocabulary the renderer expects.
_GOAL_TYPE_DISPLAY = {"own_goal": "own goal", "penalty": "penalty"}


def league_data(ds: "dataset.Dataset", competition_id: str, season_id: str) -> LeagueData:
    """Build one league's renderer-ready view from the Dataset.

    Teams come from entries (a team with 0 matches still appears; withdrawn
    and expelled entries stay in the table, marked). Placeholder matches and
    their goals are excluded from everything.
    """
    comp = ds.competitions[competition_id]
    season = ds.seasons[season_id]
    cs = ds.competition_seasons.get((competition_id, season_id))
    if cs is None:
        raise dataset.DataError(
            f"no competition_seasons row for {competition_id}+{season_id}"
        )

    entries = [e for e in ds.entries.values()
               if e.competition_id == competition_id and e.season_id == season_id]

    # team_id -> legacy code, and the renderer's teams dict keyed by code.
    code_of: "dict[str, str]" = {}
    teams: "dict[str, TeamView]" = {}
    adjustments: "dict[str, int]" = {}
    adjustment_reasons: "dict[str, str]" = {}
    withdrawn: "dict[str, str]" = {}
    for e in entries:
        team = ds.teams[e.team_id]
        club = ds.clubs[team.club_id]
        code = team.legacy_code or team.team_id
        code_of[e.team_id] = code
        teams[code] = TeamView(
            code=code, name=team.display_name, location=club.city,
            team_id=team.team_id, club_id=team.club_id,
        )
        if e.points_adjustment:
            adjustments[code] = e.points_adjustment
            if e.adjustment_reason:
                adjustment_reasons[code] = e.adjustment_reason
        if e.status in ("withdrawn", "expelled"):
            withdrawn[code] = e.status

    matches: "list[MatchView]" = []
    kept_match_ids = set()
    for i, m in enumerate(ds.matches.values(), start=2):
        if m.competition_id != competition_id or m.season_id != season_id:
            continue
        if m.is_placeholder:
            continue  # known-fake seed rows render nowhere
        venue = ds.venues.get(m.venue_id)
        matches.append(MatchView(
            row=i,
            matchday=m.matchday if m.matchday is not None else 0,
            date=m.date,
            home_code=code_of.get(m.home_team_id, m.home_team_id),
            away_code=code_of.get(m.away_team_id, m.away_team_id),
            home_goals=m.home_goals if m.counts_for_table else None,
            away_goals=m.away_goals if m.counts_for_table else None,
            stadium=venue.name if venue else "",
            match_id=m.match_id,
            status=m.status,
            confidence=m.confidence,
            awarded_note=m.awarded_note,
        ))
        kept_match_ids.add(m.match_id)

    goals: "list[GoalView]" = []
    own_goal_total = 0
    for g in ds.goals.values():
        if g.match_id not in kept_match_ids:
            continue
        if g.is_own_goal:
            own_goal_total += 1
        if g.player_id == dataset.UNKNOWN_PLAYER_ID:
            # Counts toward totals (own_goal_total above; match/team totals
            # come from the score), but never renders a scorer line or a
            # ranking entry — same as the old sheets' blank-scorer rows.
            continue
        goals.append(GoalView(
            match_id=g.match_id,
            team_code=code_of.get(g.team_id, g.team_id),
            player_name=ds.player_display_name(g.player_id),
            minute=_goal_display_minute(g),
            goal_type=_GOAL_TYPE_DISPLAY.get(g.goal_type, ""),
            player_id=g.player_id,
        ))

    return LeagueData(
        competition_id=competition_id,
        season_id=season_id,
        slug=competition_slug(competition_id, comp.country),
        league_name=ds.league_display_name(competition_id, season_id),
        season=short_season_label(season.label),
        teams=teams,
        matches=matches,
        goals=goals,
        points_win=cs.points_win,
        points_draw=cs.points_draw,
        adjustments=adjustments,
        adjustment_reasons=adjustment_reasons,
        withdrawn=withdrawn,
        own_goal_total=own_goal_total,
        promotion_places=cs.promotion_places or 0,
        relegation_places=cs.relegation_places or 0,
    )


def current_competition_seasons(ds: "dataset.Dataset") -> "list[dataset.CompetitionSeason]":
    """The competition_seasons row to build for each competition.

    Prefers the active season's row; a competition with no row for the active
    season (e.g. Women's Premiership while 25/26 is its latest) falls back to
    its most recent season by start_date. Season choice always comes from the
    seasons tab, never the system clock.
    """
    active = ds.active_season()
    by_comp: "dict[str, list[dataset.CompetitionSeason]]" = {}
    for cs in ds.competition_seasons.values():
        by_comp.setdefault(cs.competition_id, []).append(cs)
    out = []
    for comp_id in ds.competitions:
        rows = by_comp.get(comp_id, [])
        if not rows:
            continue
        exact = [cs for cs in rows if cs.season_id == active.season_id]
        if exact:
            out.append(exact[0])
        else:
            out.append(max(rows, key=lambda cs: ds.seasons[cs.season_id].start_date))
    return out
