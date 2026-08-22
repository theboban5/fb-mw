"""Thin adapter over the site's existing data layer.

Nothing here re-implements parsing, standings or scorer aggregation — it calls
`src.dataset`, `src.standings` and `src.scorers` and reshapes the result into
the handful of views the post types render. If you find yourself writing
football logic in this file, it belongs in `src/` where the site can use it too.

The site's rules are inherited, not restated:
  * `source_type=placeholder` matches render nowhere.
  * Scorer names resolve via player_id -> players; `goals.player_name` is junk.
  * CAF_MW_UNKNOWN scores count toward the team total but is never named.
  * Own goals show on the beneficiary's scorer line marked (OG), and never in
    a scorer ranking.
  * The active season comes from `seasons.status`, never the system clock.
"""

import datetime
import os
from dataclasses import dataclass, field

from src import adapt, dataset, scorers as scorers_mod, standings

from . import config


# ── Views ────────────────────────────────────────────────────────────────────

@dataclass(frozen=True)
class TeamRef:
    team_id: str
    club_id: str
    name: str
    crest: "str | None"     # absolute path to a PNG, or None -> monogram
    monogram: str

    @property
    def has_crest(self) -> bool:
        return self.crest is not None


@dataclass(frozen=True)
class ScorerLine:
    """One player's goals in one match, already display-formatted."""
    player_id: str
    name: str
    minutes: "tuple[str, ...]"   # ("13'", "45+2'") — may hold "" when unknown
    own_goal: bool = False
    penalty: bool = False

    @property
    def label(self) -> str:
        """'Malizwe 13', 62' (P)' — the site's own annotation convention.

        A goal whose minute was never recorded still has to be counted, or a
        player who scored twice with one minute known reads as having scored
        once: 'Banda 67' (+1)'. Same idiom as the '+N not recorded' line for
        goals with no named scorer at all.
        """
        shown = [m for m in self.minutes if m]
        missing = len(self.minutes) - len(shown)
        if shown:
            text = f"{self.name} {', '.join(shown)}"
            if missing:
                text += f" (+{missing})"
        elif len(self.minutes) > 1:
            text = f"{self.name} ×{len(self.minutes)}"
        else:
            text = self.name
        if self.penalty:
            text += " (P)"
        if self.own_goal:
            text += " (OG)"
        return text


@dataclass(frozen=True)
class Side:
    team: TeamRef
    goals: "int | None"
    scorers: "tuple[ScorerLine, ...]" = ()
    recorded_goals: int = 0      # goal rows found, incl. unnamed scorers

    @property
    def named_goals(self) -> int:
        return sum(len(s.minutes) for s in self.scorers)

    @property
    def unrecorded(self) -> int:
        """Goals in the score with no named scorer behind them.

        Non-zero is normal — scorer data arrives late and incomplete. What is
        not allowed is showing a partial list as if it were the whole one, so
        every surface that prints `scorers` must also print this.
        """
        if self.goals is None:
            return 0
        return max(0, self.goals - self.named_goals)

    @property
    def scorers_complete(self) -> bool:
        """Every goal in the score has a named scorer behind it."""
        if self.goals is None:
            return False
        return self.unrecorded == 0


@dataclass(frozen=True)
class MatchView:
    match_id: str
    competition_id: str
    competition: str             # display name (sponsor_name where set)
    slug: str
    season_id: str
    stage_label: str             # "Matchday 12" / "Semi-finals" / ""
    date: str                    # YYYY-MM-DD, may be ""
    kickoff: str                 # "15:00" or ""
    venue: str
    status: str
    home: Side
    away: Side
    awarded_note: str = ""
    extra_time: bool = False
    home_pens: "int | None" = None
    away_pens: "int | None" = None

    @property
    def played(self) -> bool:
        return self.status in ("played", "awarded")

    @property
    def date_label(self) -> str:
        """'Sat 15 Aug' — absolute, because a board gets re-shared."""
        return format_short(self.date)

    @property
    def scoreline(self) -> str:
        if self.home.goals is None or self.away.goals is None:
            return ""
        return f"{self.home.goals}-{self.away.goals}"

    @property
    def scorers_complete(self) -> bool:
        return self.home.scorers_complete and self.away.scorers_complete

    @property
    def has_any_scorer(self) -> bool:
        return bool(self.home.scorers or self.away.scorers)

    @property
    def shootout(self) -> str:
        if self.home_pens is None or self.away_pens is None:
            return ""
        return f"{self.home_pens}-{self.away_pens} on penalties"


@dataclass(frozen=True)
class ScorerStanding:
    position: int                # joint positions repeat the number
    joint: bool
    player_id: str
    name: str
    team: "TeamRef | None"
    goals: int


@dataclass(frozen=True)
class TableRow:
    position: int
    team: TeamRef
    played: int
    won: int
    drawn: int
    lost: int
    goals_for: int
    goals_against: int
    goal_difference: int
    points: int
    form: "tuple[str, ...]" = ()   # ("W", "D", "L", ...) oldest first
    # entries."group" — the cluster whose table this row belongs to, "" for a
    # competition played as one table. The position is a rank INSIDE it.
    group: str = ""


@dataclass
class Ctx:
    """Everything a post type needs to answer is_available() and build()."""
    ds: "dataset.Dataset"
    date: datetime.date
    season: "dataset.Season"
    warnings: "list[str]" = field(default_factory=list)
    # CLI flags a post type may read: match_id, competition, top_n, days.
    # Kept as a plain dict so adding a flag never changes this signature.
    options: "dict" = field(default_factory=dict)

    # ── lookups ──────────────────────────────────────────────────────────

    def team(self, team_id: str) -> TeamRef:
        team = self.ds.teams[team_id]
        club = self.ds.clubs[team.club_id]
        return TeamRef(
            team_id=team_id,
            club_id=team.club_id,
            name=team.display_name,
            crest=crest_path(team.legacy_code, team.club_id),
            monogram=_monogram(team.display_name or club.name),
        )

    def competition_name(self, competition_id: str, season_id: str) -> str:
        return self.ds.league_display_name(competition_id, season_id)

    def competition_slug(self, competition_id: str) -> str:
        comp = self.ds.competitions[competition_id]
        return adapt.competition_slug(competition_id, comp.country)

    def is_cup(self, competition_id: str) -> bool:
        return self.ds.competitions[competition_id].type == "cup"

    # ── queries ──────────────────────────────────────────────────────────

    def matches_between(self, start: datetime.date, end: datetime.date,
                        played: "bool | None" = None) -> "list[MatchView]":
        """Every non-placeholder match dated in [start, end], inclusive.

        `played=True` keeps results, `played=False` keeps scheduled fixtures,
        `None` keeps both. An undated match belongs to no day and is never
        returned — the same rule /matches/ follows.
        """
        lo, hi = start.isoformat(), end.isoformat()
        out = []
        for m in self.ds.matches.values():
            if m.is_placeholder or not m.date or not (lo <= m.date <= hi):
                continue
            if played is True and not m.counts_for_table:
                continue
            if played is False and m.status != "scheduled":
                continue
            out.append(self.match_view(m))
        out.sort(key=_match_sort_key)
        return out

    def match(self, match_id: str) -> MatchView:
        return self.match_view(self.ds.matches[match_id])

    def match_view(self, m: "dataset.Match") -> MatchView:
        home_scorers, home_recorded = self._scorers_for(m, m.home_team_id)
        away_scorers, away_recorded = self._scorers_for(m, m.away_team_id)
        venue = ""
        if m.venue_id and m.venue_id in self.ds.venues:
            venue = self.ds.venues[m.venue_id].name
        return MatchView(
            match_id=m.match_id,
            competition_id=m.competition_id,
            competition=self.competition_name(m.competition_id, m.season_id),
            slug=self.competition_slug(m.competition_id),
            season_id=m.season_id,
            stage_label=self._stage_label(m),
            date=m.date,
            kickoff=_clock(m.kickoff),
            venue=venue,
            status=m.status,
            home=Side(self.team(m.home_team_id), m.home_goals, home_scorers,
                      home_recorded),
            away=Side(self.team(m.away_team_id), m.away_goals, away_scorers,
                      away_recorded),
            awarded_note=m.awarded_note,
            extra_time=m.extra_time,
            home_pens=m.home_pens,
            away_pens=m.away_pens,
        )

    def _stage_label(self, m: "dataset.Match") -> str:
        if m.stage in adapt.STAGE_LABELS:
            return adapt.STAGE_LABELS[m.stage]
        if m.matchday:
            return f"Matchday {m.matchday}"
        return ""

    def _scorers_for(self, m: "dataset.Match", team_id: str):
        """Scorer lines for one side, grouped by player, in minute order.

        An unnamed scorer (CAF_MW_UNKNOWN) is counted but never listed — that
        is what makes a side's scorer list incomplete, which the captions have
        to disclose rather than paper over.
        """
        rows = [g for g in self.ds.goals.values()
                if g.match_id == m.match_id and g.team_id == team_id]
        rows.sort(key=lambda g: g.minute_sort)
        recorded = len(rows)
        order: "list[str]" = []
        grouped: "dict[str, list]" = {}
        for g in rows:
            if g.player_id == dataset.UNKNOWN_PLAYER_ID:
                continue
            if g.player_id not in grouped:
                grouped[g.player_id] = []
                order.append(g.player_id)
            grouped[g.player_id].append(g)
        lines = []
        for pid in order:
            goals = grouped[pid]
            name = self.ds.players[pid].display_name if pid in self.ds.players else pid
            lines.append(ScorerLine(
                player_id=pid,
                name=_surname(name),
                minutes=tuple(_minute(g) for g in goals),
                own_goal=any(g.is_own_goal for g in goals),
                # all(), not any(): one penalty among two goals does not make
                # the pair '(P)'.
                penalty=all(g.is_penalty for g in goals),
            ))
        return tuple(lines), recorded

    # ── derived tables ───────────────────────────────────────────────────

    def league(self, competition_id: str, season_id: str = "") -> "adapt.LeagueData":
        """The site's own per-league view — standings and scorers build on it."""
        return adapt.league_data(self.ds, competition_id,
                                 season_id or self.season_for(competition_id))

    def season_for(self, competition_id: str) -> str:
        """The season this competition is currently building.

        The active season if it has a row, else its most recent one — the rule
        that keeps a finished Women's Premiership season up while 26/27 runs.
        """
        seasons = [sid for (cid, sid) in self.ds.competition_seasons
                   if cid == competition_id]
        if not seasons:
            raise dataset.DataError(
                f"no competition_seasons row for {competition_id}")
        if self.season.season_id in seasons:
            return self.season.season_id
        return max(seasons, key=lambda sid: self.ds.seasons[sid].start_date)

    def table(self, competition_id: str, season_id: str = "",
              group: "str | None" = None) -> "list[TableRow]":
        """Standings computed from matches by the site's own module.

        A competition played in clusters comes back as every cluster's table
        end to end, in cluster order, each row ranked inside its own — pass
        `group` for one of them. `clusters()` lists what there is to ask for.
        """
        league = self.league(competition_id, season_id)
        rows = standings.compute_standings(
            league.matches, league.teams,
            points_win=league.points_win, points_draw=league.points_draw,
            adjustments=league.adjustments, groups=league.groups,
        )
        if group is not None:
            rows = [r for r in rows if r.group == group]
        form = standings.recent_form(league.matches, league.teams)
        by_code = {code: view.team_id for code, view in league.teams.items()}
        out = []
        for r in rows:
            out.append(TableRow(
                # The rank is on the row, not the loop counter: counting down
                # a clustered competition's list gives a team playing seven
                # others a position out of thirty-two.
                position=r.position,
                group=r.group,
                team=self.team(by_code[r.code]),
                played=r.played, won=r.won, drawn=r.drawn, lost=r.lost,
                goals_for=r.gf, goals_against=r.ga,
                goal_difference=r.gd, points=r.points,
                form=tuple(form.get(r.code, ())),
            ))
        return out

    def clusters(self, competition_id: str, season_id: str = "") -> "list[str]":
        """The cluster labels this competition is played in, in table order.

        [] for the ordinary case of one table — which is what makes "one post
        per cluster" and "one post" the same loop.
        """
        league = self.league(competition_id, season_id)
        return sorted(set(league.groups.values()), key=standings.group_key)

    def top_scorers(self, competition_id: str, season_id: str = "",
                    top_n: int = 8) -> "list[ScorerStanding]":
        """Top N scorers with ties shown as joint positions.

        A tie that straddles the cut is kept whole: showing four of six players
        on 5 goals would be a false ranking.
        """
        league = self.league(competition_id, season_id)
        tallies, _own_goals, _more = scorers_mod.top_scorers(league.goals)
        by_code = {code: view.team_id for code, view in league.teams.items()}

        # `rank` is already standard competition ranking (joint 2nd, joint
        # 2nd, 4th). Cut at top_n but never mid-tie: naming four of six
        # players level on 5 goals asserts an order the data does not have.
        kept = list(tallies[:top_n])
        for t in tallies[len(kept):]:
            if kept and t.goals == kept[-1].goals:
                kept.append(t)
            else:
                break

        rank_counts: "dict[int, int]" = {}
        for t in kept:
            rank_counts[t.rank] = rank_counts.get(t.rank, 0) + 1
        return [
            ScorerStanding(
                position=t.rank,
                joint=rank_counts[t.rank] > 1,
                player_id=t.player_id,
                name=t.player_name,
                team=self.team(by_code[t.team_code]) if t.team_code in by_code else None,
                goals=t.goals,
            )
            for t in kept
        ]


# ── Helpers ──────────────────────────────────────────────────────────────────

def _match_sort_key(m: MatchView):
    """Date, then kickoff (unknown last), then competition, then id."""
    return (m.date, m.kickoff == "", m.kickoff, m.competition, m.match_id)


def _clock(value: str) -> str:
    """'15:00:00' / '15:00' -> '15:00'; anything else -> ''. Always CAT."""
    v = (value or "").strip()
    if not v or v.lower() in ("tbd", "tba"):
        return ""
    parts = v.split(":")
    if len(parts) >= 2 and parts[0].isdigit() and parts[1].isdigit():
        return f"{int(parts[0]):02d}:{parts[1][:2]}"
    return ""


def _minute(g: "dataset.Goal") -> str:
    if not g.minute:
        return ""
    base = f"{g.minute}+{g.stoppage}" if g.stoppage else g.minute
    return f"{base}'"


def _surname(name: str) -> str:
    """Display surname on a graphic; the caption keeps the full name.

    A 1080px board fits 'Malizwe 13'' where it cannot fit a full name twice
    over. One-word names are returned as they are.
    """
    parts = name.split()
    return parts[-1] if len(parts) > 1 else name


def _monogram(name: str) -> str:
    words = [w for w in name.split() if w[:1].isalnum()]
    if not words:
        return "?"
    if len(words) == 1:
        return words[0][:2].upper()
    return (words[0][:1] + words[1][:1]).upper()


def crest_path(legacy_code: str, club_id: str) -> "str | None":
    """static/logos/clubs/<legacy_code>.png, else <club_id>.png, else None.

    Same order as src/render.py: a team with its own crest file (a women's
    side kept alongside the club's) keeps it, otherwise the club's is used.
    """
    for key in (legacy_code, club_id):
        if not key:
            continue
        path = os.path.join(config.CRESTS, f"{key}.png")
        if os.path.exists(path):
            return path
    return None


def competition_logo(competition_id: str) -> "str | None":
    path = os.path.join(config.COMP_LOGOS, f"{competition_id}.png")
    return path if os.path.exists(path) else None


def load(date: "datetime.date | None" = None) -> Ctx:
    """Load the dataset the same way the site build does.

    Honours DATASET_LOCAL_DIR, so `DATASET_LOCAL_DIR=data/canonical` generates
    a pack fully offline from the last validated fetch.
    """
    ds = dataset.load()
    return Ctx(ds=ds, date=date or today(), season=ds.active_season())


def today() -> datetime.date:
    """Today in CAT — the clock the site's audience is on."""
    tz = datetime.timezone(datetime.timedelta(hours=config.TZ_OFFSET_HOURS))
    return datetime.datetime.now(tz).date()


def format_date(value: "str | datetime.date") -> str:
    """'2026-08-09' -> '9 Aug 2026'. Absolute, never 'today'."""
    if isinstance(value, str):
        if not value:
            return ""
        value = datetime.date.fromisoformat(value)
    return f"{value.day} {value.strftime('%b %Y')}"


def format_short(value: "str | datetime.date") -> str:
    """'2026-08-15' -> 'Sat 15 Aug'. For caption lines, where the full form
    would crowd out the fixture itself."""
    if isinstance(value, str):
        if not value:
            return ""
        value = datetime.date.fromisoformat(value)
    return f"{value.strftime('%a')} {value.day} {value.strftime('%b')}"


def format_day(value: "str | datetime.date") -> str:
    """'2026-08-09' -> 'Sunday 9 August'."""
    if isinstance(value, str):
        if not value:
            return ""
        value = datetime.date.fromisoformat(value)
    return f"{value.strftime('%A')} {value.day} {value.strftime('%B')}"
