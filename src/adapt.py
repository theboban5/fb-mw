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
    "MW_CRFA2": "crfa2",
    "MW_WP": "wp",
    "MW_KU19": "ku19",
    "MW_U16": "u16",
    "MW_BDU16": "bdu16",
    # Derivation would give the same slug, but this dict doubles as the
    # landing-page order (build._LANDING_ORDER), so listing it pins the row's
    # place in the men's cup group as well as the URL.
    "MW_TOP8": "top8",
}


# Presentation of matches.stage. A md_<n> league stage is not listed here:
# stage-label lookups fall back to the classic "MATCHDAY <n>" header derived
# from MatchView.matchday, so league pages render byte-identically.
STAGE_LABELS = {
    "r64": "Round of 64",
    "r32": "Round of 32",
    "r16": "Round of 16",
    "qf": "Quarter-finals",
    "sf": "Semi-finals",
    "3p": "Third-place play-off",
    "final": "Final",
}

# Sort rank, earliest round first. The third-place play-off sits between the
# semis and the final because that is when it is played.
STAGE_ORDER = {"r64": 0, "r32": 1, "r16": 2, "qf": 3, "sf": 4, "3p": 5, "final": 6}

# Short labels for the results pager chips ("SF", not a bare round number).
STAGE_CHIPS = {"r64": "R64", "r32": "R32", "r16": "R16", "qf": "QF",
               "sf": "SF", "3p": "3P", "final": "F"}

# Kickoffs are entered in Malawi's clock and shown with the zone named.
KICKOFF_TZ = dataset.KICKOFF_TZ


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


def clock(raw: str) -> str:
    """"14:30" from the sheet's "14:30" or Sheets' "14:30:00"; "" when blank.

    Both export forms are in the matches tab — for years nothing rendered the
    kickoff column, so nothing normalised it either. A value that is neither
    (a stray note, "TBD") normalises to "", which reads as "kickoff not known
    yet" everywhere: an unannounced kickoff must never hold up a build.
    """
    raw = (raw or "").strip()
    if not raw or raw.lower() in ("tbd", "tba"):
        return ""
    h, sep, rest = raw.partition(":")
    m = rest.partition(":")[0]
    if not (sep and h.isdigit() and len(m) == 2 and m.isdigit()):
        return ""
    return f"{int(h):02d}:{m}" if 0 <= int(h) <= 23 and int(m) <= 59 else ""


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
    kickoff: str = ""    # "HH:MM" in Malawi time, "" when not announced
    stadium: str = ""
    match_id: "str | None" = None
    status: str = "played"
    confidence: str = "confirmed"
    awarded_note: str = ""
    stage: str = ""
    extra_time: bool = False
    home_pens: "int | None" = None
    away_pens: "int | None" = None

    @property
    def played(self) -> bool:
        return (self.status in ("played", "awarded")
                and self.home_goals is not None and self.away_goals is not None)

    @property
    def kickoff_label(self) -> str:
        """"14:30 CAT", or "" when the sheet has no kickoff for this match.

        The zone is spelled out because the site is read from outside Malawi
        too, and it matches how the national-team pages label a kickoff.
        """
        return f"{self.kickoff} {KICKOFF_TZ}" if self.kickoff else ""

    @property
    def status_badge(self) -> str:
        """Short label shown instead of a score, or "" for normal rows."""
        return {"postponed": "PPD", "cancelled": "CANC", "abandoned": "ABD"}.get(
            self.status, "")

    @property
    def unconfirmed(self) -> bool:
        return self.confidence == "unconfirmed"

    @property
    def score_note(self) -> str:
        """"(4–3 pens)", "(AET)" or "(4–3 pens, AET)" beside a knockout score.

        Always "" on league matches (the validator keeps pens/extra_time off
        them), so league pages are untouched by rendering it unconditionally.
        """
        parts = []
        if self.home_pens is not None and self.away_pens is not None:
            parts.append(f"{self.home_pens}–{self.away_pens} pens")
        if self.extra_time:
            parts.append("AET")
        return f"({', '.join(parts)})" if parts else ""

    @property
    def winner_code(self) -> "str | None":
        """Who advances from a knockout tie: goals, else pens, else None.

        Penalties break only a level score — they never touch standings
        (standings.py reads goals alone).
        """
        if not self.played:
            return None
        if self.home_goals != self.away_goals:
            return self.home_code if self.home_goals > self.away_goals else self.away_code
        if (self.home_pens is not None and self.away_pens is not None
                and self.home_pens != self.away_pens):
            return self.home_code if self.home_pens > self.away_pens else self.away_code
        return None


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
    kind: str = "league"                    # competitions.type: league | cup
    # Cup only: derived matchday -> stage code, for round headers/pager chips.
    stage_of_matchday: "dict[int, str]" = field(default_factory=dict)


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

    kept: "list[tuple[int, dataset.Match]]" = []
    for i, m in enumerate(ds.matches.values(), start=2):
        if m.competition_id != competition_id or m.season_id != season_id:
            continue
        if m.is_placeholder:
            continue  # known-fake seed rows render nowhere
        kept.append((i, m))

    # Cups order their rounds by stage, not by a hand-maintained matchday
    # column: dense-rank the stages actually present so adding a later round
    # (quarter-finals, the final) is purely a sheet edit. The sheet's matchday
    # column is ignored for cups; leagues keep it exactly as before.
    is_cup = comp.type == "cup"
    cup_md: "dict[str, int]" = {}
    if is_cup:
        present = sorted({m.stage for _i, m in kept},
                         key=lambda s: STAGE_ORDER.get(s, len(STAGE_ORDER)))
        cup_md = {s: n for n, s in enumerate(present, start=1)}

    matches: "list[MatchView]" = []
    kept_match_ids = set()
    for i, m in kept:
        venue = ds.venues.get(m.venue_id)
        matches.append(MatchView(
            row=i,
            matchday=cup_md[m.stage] if is_cup
                     else (m.matchday if m.matchday is not None else 0),
            date=m.date,
            home_code=code_of.get(m.home_team_id, m.home_team_id),
            away_code=code_of.get(m.away_team_id, m.away_team_id),
            home_goals=m.home_goals if m.counts_for_table else None,
            away_goals=m.away_goals if m.counts_for_table else None,
            kickoff=clock(m.kickoff),
            stadium=venue.name if venue else "",
            match_id=m.match_id,
            status=m.status,
            confidence=m.confidence,
            awarded_note=m.awarded_note,
            stage=m.stage,
            extra_time=m.extra_time,
            home_pens=m.home_pens,
            away_pens=m.away_pens,
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
        kind="cup" if is_cup else "league",
        stage_of_matchday={n: s for s, n in cup_md.items()},
    )


@dataclass
class TieView:
    """One knockout tie: a single match, or a two-legged pair in leg order.

    Two matches form a tie when they share a stage and pair the same two
    teams with home and away swapped — that mirror is what distinguishes a
    second leg from a replay, so no `leg` column is needed in the sheet.
    The tie's winner is decided by aggregate, then away goals (the rule this
    pyramid's ties observably play to), then the deciding leg's shootout.
    """
    legs: "list[MatchView]"

    @property
    def two_legged(self) -> bool:
        return len(self.legs) > 1

    # Sides are presented in first-leg order throughout.
    @property
    def home_code(self) -> str:
        return self.legs[0].home_code

    @property
    def away_code(self) -> str:
        return self.legs[0].away_code

    @property
    def all_played(self) -> bool:
        return all(m.played for m in self.legs)

    @property
    def any_played(self) -> bool:
        return any(m.played for m in self.legs)

    def _totals(self, code: str) -> "tuple[int, int]":
        """(aggregate goals, away goals) for one side over the played legs."""
        agg = away = 0
        for m in self.legs:
            if not m.played:
                continue
            if m.home_code == code:
                agg += m.home_goals
            else:
                agg += m.away_goals
                away += m.away_goals
        return agg, away

    @property
    def agg_home(self) -> "int | None":
        return self._totals(self.home_code)[0] if self.any_played else None

    @property
    def agg_away(self) -> "int | None":
        return self._totals(self.away_code)[0] if self.any_played else None

    @property
    def winner_code(self) -> "str | None":
        if not self.all_played:
            return None
        if not self.two_legged:
            return self.legs[0].winner_code
        (h_agg, h_away) = self._totals(self.home_code)
        (a_agg, a_away) = self._totals(self.away_code)
        if h_agg != a_agg:
            return self.home_code if h_agg > a_agg else self.away_code
        if h_away != a_away:
            return self.home_code if h_away > a_away else self.away_code
        # Level on aggregate and away goals: the deciding leg's shootout.
        last = self.legs[-1]
        if (last.home_pens is not None and last.away_pens is not None
                and last.home_pens != last.away_pens):
            return (last.home_code if last.home_pens > last.away_pens
                    else last.away_code)
        return None

    @property
    def decided_by_away_goals(self) -> bool:
        """True when only the away-goals rule separates the sides."""
        if not (self.two_legged and self.all_played):
            return False
        (h_agg, h_away) = self._totals(self.home_code)
        (a_agg, a_away) = self._totals(self.away_code)
        return h_agg == a_agg and h_away != a_away

    @property
    def unconfirmed(self) -> bool:
        return any(m.unconfirmed and m.played for m in self.legs)


def _pair_legs(stage_matches: "list[MatchView]") -> "list[TieView]":
    """Fold a round's matches into ties, pairing mirrored home/away legs."""
    ties: "list[TieView]" = []
    open_tie: "dict[frozenset, TieView]" = {}
    for m in sorted(stage_matches, key=lambda m: (m.date, m.row)):
        key = frozenset((m.home_code, m.away_code))
        first = open_tie.get(key)
        # Only a reversed fixture is a second leg; a repeat with the same
        # home team is something else (a replay) and stays its own tie.
        if first is not None and m.home_code == first.away_code:
            first.legs.append(m)
            del open_tie[key]
            continue
        tie = TieView([m])
        ties.append(tie)
        open_tie[key] = tie
    return ties


def cup_rounds(matches: "list[MatchView]") -> "list[tuple[str, list[TieView]]]":
    """Group a cup's matches into bracket rounds of ties, earliest round first.

    When the highest round present is not the final, an empty ("final", [])
    round is appended: the bracket renders it as a placeholder tie fed by the
    winners of the last round present. Unknown participants are a rendering
    concern — never fake team rows — and "final comes last" is read off
    STAGE_ORDER, not hardcoded per competition.
    """
    by_stage: "dict[str, list[MatchView]]" = {}
    for m in matches:
        by_stage.setdefault(m.stage, []).append(m)
    stages = sorted(by_stage, key=lambda s: STAGE_ORDER.get(s, len(STAGE_ORDER)))
    rounds = [(s, _pair_legs(by_stage[s])) for s in stages]
    if stages and stages[-1] != "final":
        rounds.append(("final", []))
    return rounds


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
