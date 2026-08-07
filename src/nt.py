"""National-team data layer: the six nt_* tabs, filtered to one team.

A separate schema from the 13 league tabs, deliberately kept apart:

  * Its ids are its own. `nt_teams.team_code` (MW_W) is not a `teams.team_id`,
    and national-team `player_id`s (MW_W_001, W_INT_NI_002) are not in the
    `players` tab. The only join back into the league data is
    `nt_squads.domestic_team_id` -> `teams.team_id`, which is blank for
    foreign-based players (expected, not an error).
  * A match row is one team's perspective: `opponent` is a NAME, not a code,
    while `nt_goals.team_id` / `nt_lineups.team_id` use codes (MW_W,
    NIGERIA_W). So goals split by "is this our team_code or not", never by
    resolving the opponent.
  * `nt_matches.date` may be the literal "tbd" (fixture with no date yet).
    That parses to "" here; the renderer shows "Date TBC".

**Every filter to one team lives in `team_data`** (which `load_team` calls
after parsing). Downstream code receives an NTTeamData holding no other
*tracked* team's rows, so a men's or U20 row can never leak onto the women's
page. The single deliberate exception is a group table, which is about the
rivals: `NTGroup` carries their rows too, and they are exactly the rows with no
`nt_teams` code of their own.
"""

from dataclasses import dataclass, field, replace
from datetime import datetime

from . import dataset
from .dataset import DataError, _enum, _opt_int, _put, _require, _rows

# The women's senior side — the team this site currently builds a page for.
SCORCHERS = "MW_W"

# Squad/lineup position vocabulary, in the order squads are always listed.
POSITIONS = ("GK", "DF", "MF", "FW")
POSITION_LABELS = {
    "GK": "Goalkeepers",
    "DF": "Defenders",
    "MF": "Midfielders",
    "FW": "Forwards",
}

# _enum lowercases before comparing, so the position check needs a lowercase
# vocabulary; parsed values are upper-cased straight back to GK/DF/MF/FW.
_POSITION_SET = frozenset(p.lower() for p in POSITIONS)

NT_MATCH_STATUSES = frozenset({"scheduled", "played", "awarded"})
# What a knockout slot can be filled by: the winner or the loser of another
# tie ("winner:WAFCON26_QF1"). Losers feed exactly one thing, the third-place
# play-off, but they feed it the same way.
FEED_KINDS = frozenset({"winner", "loser"})
NT_ROLES = frozenset({"starting", "sub_on", "unused_sub"})
# Already the display vocabulary in this tab ("own goal", not "own_goal" as in
# the league goals tab); the underscore form is accepted so either spelling works.
NT_GOAL_TYPES = frozenset({"", "penalty", "own goal", "own_goal"})

# What the sheet writes in a date cell for a fixture that has no date yet.
_NO_DATE = ("tbd", "tba")

# Kickoff times are entered — and shown — in Malawi's clock, so a reader here
# never has to convert one. Malawi keeps CAT (UTC+2) year round, no DST.
KICKOFF_TZ = "CAT"


# ── Parsing helpers ──────────────────────────────────────────────────────────

def _nt_date(value: str, label: str, tab: str, i: int) -> str:
    """Strict YYYY-MM-DD, but "tbd"/"tba" (unscheduled) parses to ""."""
    if value.strip().lower() in _NO_DATE:
        return ""
    return dataset._date(value, label, tab, i)


def _nt_time(value: str, label: str, tab: str, i: int) -> str:
    """24-hour HH:MM, blank when unknown; "tbd"/"tba" also parses to "".

    Sheets exports a time cell as either "20:00" or "20:00:00" depending on the
    cell format, so both are accepted and normalised to HH:MM. Anything else
    fails the build rather than reaching the page as a mystery string.
    """
    v = value.strip()
    if not v or v.lower() in _NO_DATE:
        return ""
    for fmt in ("%H:%M", "%H:%M:%S"):
        try:
            return datetime.strptime(v, fmt).strftime("%H:%M")
        except ValueError:
            pass
    raise DataError(f"{tab} row {i}: {label} {value!r} is not 24-hour HH:MM")


def _flag(value: str, label: str, tab: str, i: int) -> bool:
    """A Sheets checkbox column: blank | 0/1 | FALSE/TRUE."""
    v = value.strip().lower()
    if v not in ("", "0", "1", "false", "true"):
        raise DataError(
            f"{tab} row {i}: {label} {value!r} must be blank, 0/1 or TRUE/FALSE")
    return v in ("1", "true")


def parse_feed(value: str) -> "tuple[str, str] | None":
    """"winner:WAFCON26_QF1" -> ("winner", "WAFCON26_QF1"); blank -> None."""
    kind, _, tie_id = value.partition(":")
    kind, tie_id = kind.strip().lower(), tie_id.strip()
    return (kind, tie_id) if kind in FEED_KINDS and tie_id else None


def _feed(value: str, label: str, tab: str, i: int) -> str:
    """A slot's source, validated at parse time so a typo never renders."""
    if value.strip() and parse_feed(value) is None:
        raise DataError(
            f"{tab} row {i}: {label} {value!r} must be "
            f"'winner:<tie_id>' or 'loser:<tie_id>'")
    return value.strip()


def _id_sort(value: str) -> "tuple[int, int, str]":
    """Sort ids numerically when they are numbers, else lexically."""
    try:
        return (0, int(value), "")
    except ValueError:
        return (1, 0, value)


# ── Records ──────────────────────────────────────────────────────────────────

@dataclass(frozen=True)
class NTTeam:
    team_code: str
    team_name: str
    category: str        # senior | youth


@dataclass(frozen=True)
class NTMatch:
    match_id: str
    team_code: str
    date: str            # YYYY-MM-DD, or "" when the sheet says tbd
    competition: str
    opponent: str        # a display NAME, never a code
    home_away: str       # home | away (from our team's perspective)
    neutral: bool
    venue: str
    city: str
    country: str
    team_score: "int | None"
    opponent_score: "int | None"
    status: str          # scheduled | played | awarded
    coach: str
    extra_time: bool
    penalty_shootout: bool
    extra_time_result: str
    kickoff: str = ""    # HH:MM in Malawi time, or "" when not announced

    @property
    def has_score(self) -> bool:
        return self.team_score is not None and self.opponent_score is not None

    @property
    def played(self) -> bool:
        return self.status in ("played", "awarded") and self.has_score

    @property
    def scheduled(self) -> bool:
        return self.status == "scheduled"

    @property
    def venue_label(self) -> str:
        """"Al Medina Stadium, Rabat" — blank parts and "tbd" dropped."""
        parts = [p for p in (self.venue, self.city)
                 if p and p.strip().lower() not in _NO_DATE]
        return ", ".join(parts)

    @property
    def kickoff_label(self) -> str:
        """"20:00 CAT", or "" when the sheet has no kickoff for the fixture."""
        return f"{self.kickoff} {KICKOFF_TZ}" if self.kickoff else ""

    @property
    def ground_label(self) -> str:
        """Home / Away / Neutral — neutral wins, as it is the useful fact."""
        if self.neutral:
            return "Neutral"
        return "Home" if self.home_away == "home" else "Away"

    @property
    def score_note(self) -> str:
        """"(AET)" / "(pens)" / "(AET, lost on pens)" beside a score."""
        parts = []
        if self.extra_time:
            parts.append("AET")
        if self.penalty_shootout:
            result = self.extra_time_result.strip().lower()
            outcome = {"win": "won", "loss": "lost"}.get(result, "")
            parts.append(f"{outcome} on pens" if outcome else "pens")
        return f"({', '.join(parts)})" if parts else ""

    @property
    def sort_key(self):
        """Chronological; an undated fixture sorts after every dated one."""
        return (self.date == "", self.date, _id_sort(self.match_id))

    @property
    def recent_key(self):
        """Reverse-chronological ordering that still leaves undated rows last."""
        return (self.date != "", self.date, _id_sort(self.match_id))


@dataclass(frozen=True)
class NTGoal:
    goal_id: str
    match_id: str
    team_id: str         # the side the goal counted FOR (own goals: beneficiary)
    player_name: str
    player_id: str
    minute: str
    stoppage: str
    period: str
    goal_type: str       # "" | penalty | own goal
    source_ref: str

    @property
    def is_penalty(self) -> bool:
        return self.goal_type == "penalty"

    @property
    def is_own_goal(self) -> bool:
        return self.goal_type == "own goal"

    @property
    def minute_label(self) -> str:
        """"73", "90+5", or "" when the minute is unrecorded."""
        if not self.minute:
            return ""
        return f"{self.minute}+{self.stoppage}" if self.stoppage else self.minute

    @property
    def minute_sort(self) -> "tuple[int, int]":
        """45 < 45+1 < 46; an unknown minute sorts last."""
        try:
            base = int(self.minute)
        except ValueError:
            return (10**6, 0)
        try:
            added = int(self.stoppage) if self.stoppage else 0
        except ValueError:
            added = 0
        return (base, added)

    @property
    def annotation(self) -> str:
        """"Temwa Chawinga 90+5'", with " (P)" / " (OG)" appended."""
        label = (f"{self.player_name} {self.minute_label}'"
                 if self.minute_label else self.player_name)
        if self.is_penalty:
            label += " (P)"
        elif self.is_own_goal:
            label += " (OG)"
        return label


@dataclass(frozen=True)
class NTSquadPlayer:
    squad_id: str
    competition: str
    team_id: str
    announcement_date: str
    competition_context: str
    player_name: str
    player_id: str
    position: str        # GK | DF | MF | FW
    shirt_number: str
    club: str
    domestic_team_id: str   # -> teams.team_id; blank for foreign-based players
    club_country: str
    notes: str           # free text; carries "captain" / "vice-captain"
    coach: str

    @property
    def is_vice_captain(self) -> bool:
        return "vice" in self.notes.lower() and "captain" in self.notes.lower()

    @property
    def is_captain(self) -> bool:
        # Tested before the vice check everywhere, so exclude it here: a
        # vice-captain must never render as the captain.
        return "captain" in self.notes.lower() and not self.is_vice_captain

    @property
    def shirt_sort(self) -> "tuple[int, int, str]":
        return _id_sort(self.shirt_number)


@dataclass(frozen=True)
class NTLineupRow:
    match_id: str
    team_id: str
    player_name: str
    player_id: str
    shirt_number: str
    position: str
    role: str            # starting | sub_on | unused_sub
    captain: bool
    minute_on: str
    minute_off: str
    replaced_player: str  # a NAME (this tab has no replaced_player_id)
    yellow_card: bool
    yellow_red_card: bool
    red_card: bool

    @property
    def shirt_sort(self) -> "tuple[int, int, str]":
        return _id_sort(self.shirt_number)


@dataclass(frozen=True)
class NTGroupRow:
    """One team's line in a hand-maintained group table — never computed.

    A row is either **ours** (`team_code` is an `nt_teams` code) or **a rival's**
    (`team_code` is anything unique within the group — `NIGERIA_W`, `EGYPT_W` —
    which is why `team_name` exists: those codes have no `nt_teams` row to read
    a display name from).
    """
    team_code: str
    competition_name: str
    group_name: str
    position: str
    played: str
    won: str
    drawn: str
    lost: str
    points: str
    last_update: str
    wikipedia_url: str
    team_name: str = ""
    goals_for: str = ""
    goals_against: str = ""

    @property
    def title(self) -> str:
        return (f"{self.competition_name} · {self.group_name}"
                if self.group_name else self.competition_name)

    @property
    def group_key(self) -> "tuple[str, str]":
        return (self.competition_name, self.group_name)

    @property
    def has_goals(self) -> bool:
        return bool(self.goals_for or self.goals_against)

    @property
    def goals_label(self) -> str:
        """"6:0" — blank when neither goals column is filled in."""
        if not self.has_goals:
            return ""
        return f"{self.goals_for or '0'}:{self.goals_against or '0'}"

    @property
    def goal_difference(self) -> str:
        """"+6" / "-1" / "0", or "" when the goals columns cannot give one."""
        try:
            gd = int(self.goals_for or 0) - int(self.goals_against or 0)
        except ValueError:
            return ""
        if not self.has_goals:
            return ""
        return f"+{gd}" if gd > 0 else str(gd)

    @property
    def sort_key(self):
        """`position` when the sheet gives one, else points then goal difference.

        The sheet is the authority on order — a group table's tie-breaks are
        competition-specific and not worth re-deriving — so a blank position
        only falls back to the obvious ranking rather than trying to be right.
        """
        def num(value, default=0):
            try:
                return int(value)
            except ValueError:
                return default

        if self.position:
            return (0, num(self.position, 99), 0, 0, self.team_code)
        return (1, 0, -num(self.points), -num(self.goal_difference), self.team_code)


@dataclass(frozen=True)
class NTKnockoutTie:
    """One tie in a hand-maintained knockout bracket — never computed.

    The counterpart to `NTGroupRow` for the other half of a tournament, and it
    exists for the same reason: `nt_matches` holds one row per *our* match with
    `opponent` as a name, so there is nowhere to record Morocco vs Senegal. A
    bracket needs every tie, not just ours, so the whole tree is recorded here.

    Unlike a match row this is written from no one's perspective — `home_name`
    and `away_name` are plain country names, in the order the tie should read.
    **A blank side is a slot not yet filled** (the semi-final while the
    quarters are still being played), which is why every column but the
    structural ones is optional: a bracket is seeded with its full shape and
    fills in as the draw resolves.

    An unfilled slot says where it will come from: `home_from` /`away_from`
    hold `winner:<tie_id>` or `loser:<tie_id>`, which is what makes a bracket
    a tree rather than three lists. `_resolve_feeds` substitutes the real
    country the moment the feeding tie is decided, so the semi-finals fill
    themselves in and only the quarter-finals are ever typed twice.

    Our own ties appear in both tabs. `nt_match_id` names the `nt_matches` row
    this tie already is, and `_link_match` folds that row's result in — so the
    score is entered once, where every other result is entered.
    """
    tie_id: str
    competition_name: str
    stage: str                    # a dataset.KNOCKOUT_STAGES value
    slot: int                     # orders the ties within one round
    home_name: str = ""           # "" until the slot is filled
    away_name: str = ""
    home_from: str = ""           # "winner:<tie_id>" / "loser:<tie_id>"
    away_from: str = ""
    home_score: "int | None" = None
    away_score: "int | None" = None
    home_pens: "int | None" = None
    away_pens: "int | None" = None
    extra_time: bool = False
    date: str = ""
    kickoff: str = ""
    venue: str = ""
    city: str = ""
    status: str = "scheduled"
    nt_match_id: str = ""
    # Filled by _link_match, not by the sheet: the nt_matches row this tie is,
    # and which side of the tie we are ("home" | "away" | "").
    match: "NTMatch | None" = None
    ours_side: str = ""

    @property
    def has_score(self) -> bool:
        return self.home_score is not None and self.away_score is not None

    @property
    def played(self) -> bool:
        return self.status in ("played", "awarded") and self.has_score

    @property
    def scheduled(self) -> bool:
        return self.status == "scheduled"

    @property
    def is_ours(self) -> bool:
        return self.match is not None

    @property
    def winner_name(self) -> str:
        """The advancing side, or "" while the tie is undecided.

        Ninety minutes, then the shootout. A linked match row records a
        shootout as won/lost by us rather than as a score (`nt_matches` has no
        pens columns), so that is read from `extra_time_result` instead.
        """
        if not self.played:
            return ""
        if self.home_score > self.away_score:
            return self.home_name
        if self.away_score > self.home_score:
            return self.away_name
        if self.home_pens is not None and self.away_pens is not None:
            if self.home_pens > self.away_pens:
                return self.home_name
            if self.away_pens > self.home_pens:
                return self.away_name
        if self.match is not None and self.match.penalty_shootout and self.ours_side:
            ours, theirs = ((self.home_name, self.away_name)
                            if self.ours_side == "home"
                            else (self.away_name, self.home_name))
            return {"win": ours, "loss": theirs}.get(
                self.match.extra_time_result.strip().lower(), "")
        return ""

    @property
    def loser_name(self) -> str:
        """The eliminated side — what a third-place play-off is fed by."""
        winner = self.winner_name
        if not winner:
            return ""
        return self.away_name if winner == self.home_name else self.home_name

    def feed(self, side: str) -> "tuple[str, str] | None":
        """("winner", tie_id) for `side` ("home"/"away"), or None."""
        return parse_feed(getattr(self, f"{side}_from"))

    @property
    def score_note(self) -> str:
        """"(4–3 pens)" / "(AET)", or the linked match row's own note."""
        if self.match is not None:
            return self.match.score_note
        parts = []
        if self.home_pens is not None and self.away_pens is not None:
            hi, lo = sorted((self.home_pens, self.away_pens), reverse=True)
            parts.append(f"{hi}–{lo} pens")
        if self.extra_time:
            parts.append("AET")
        return f"({', '.join(parts)})" if parts else ""

    @property
    def venue_label(self) -> str:
        parts = [p for p in (self.venue, self.city)
                 if p and p.strip().lower() not in _NO_DATE]
        return ", ".join(parts)

    @property
    def kickoff_label(self) -> str:
        return f"{self.kickoff} {KICKOFF_TZ}" if self.kickoff else ""

    @property
    def sort_key(self):
        """Order *within* a round. Round order is presentation (see nt_page)."""
        return (self.slot, _id_sort(self.tie_id))


# ── Tab parsers ──────────────────────────────────────────────────────────────

def parse_nt_teams(text: str) -> "dict[str, NTTeam]":
    out: "dict[str, NTTeam]" = {}
    for i, r in _rows(text, "nt_teams", {"team_code", "team_name"}):
        code = _require(r, "team_code", "nt_teams", i)
        _put(out, code, NTTeam(
            code, _require(r, "team_name", "nt_teams", i),
            r.get("category", "").lower(),
        ), "nt_teams", i)
    return out


def parse_nt_matches(text: str) -> "dict[str, NTMatch]":
    out: "dict[str, NTMatch]" = {}
    required = {"match_id", "team_code", "date", "competition", "opponent",
                "home_away", "status"}
    for i, r in _rows(text, "nt_matches", required):
        mid = _require(r, "match_id", "nt_matches", i)
        ts = _opt_int(r.get("team_score", ""), "team_score", "nt_matches", i)
        os_ = _opt_int(r.get("opponent_score", ""), "opponent_score", "nt_matches", i)
        for label, g in (("team_score", ts), ("opponent_score", os_)):
            if g is not None and g < 0:
                raise DataError(f"nt_matches row {i}: {label} cannot be negative ({g})")
        _put(out, mid, NTMatch(
            mid, _require(r, "team_code", "nt_matches", i),
            _nt_date(r.get("date", ""), "date", "nt_matches", i),
            _require(r, "competition", "nt_matches", i),
            _require(r, "opponent", "nt_matches", i),
            _enum(_require(r, "home_away", "nt_matches", i), {"home", "away"},
                  "home_away", "nt_matches", i),
            _flag(r.get("neutral", ""), "neutral", "nt_matches", i),
            r.get("venue", ""), r.get("city", ""), r.get("country", ""),
            ts, os_,
            _enum(_require(r, "status", "nt_matches", i), NT_MATCH_STATUSES,
                  "status", "nt_matches", i),
            r.get("coach", ""),
            _flag(r.get("extra_time", ""), "extra_time", "nt_matches", i),
            _flag(r.get("penalty_shootout", ""), "penalty_shootout", "nt_matches", i),
            r.get("extra_time_result", ""),
            _nt_time(r.get("kickoff", ""), "kickoff", "nt_matches", i),
        ), "nt_matches", i)
    return out


def parse_nt_goals(text: str) -> "dict[str, NTGoal]":
    out: "dict[str, NTGoal]" = {}
    required = {"goal_id", "match_id", "team_id", "player_name", "minute"}
    for i, r in _rows(text, "nt_goals", required):
        gid = _require(r, "goal_id", "nt_goals", i)
        # Unlike the league goals tab, player_name IS the display name here:
        # national-team player_ids are not in the players tab.
        _put(out, gid, NTGoal(
            gid, _require(r, "match_id", "nt_goals", i),
            _require(r, "team_id", "nt_goals", i),
            _require(r, "player_name", "nt_goals", i),
            r.get("player_id", ""), r.get("minute", ""), r.get("stoppage", ""),
            r.get("period", ""),
            _enum(r.get("goal_type", ""), NT_GOAL_TYPES,
                  "goal_type", "nt_goals", i).replace("_", " "),
            r.get("source_ref", ""),
        ), "nt_goals", i)
    return out


def parse_nt_squads(text: str) -> "list[NTSquadPlayer]":
    """A list, not a dict: (squad_id, player) is the key, one row per player."""
    out: "list[NTSquadPlayer]" = []
    required = {"squad_id", "team_id", "player_name", "position"}
    for i, r in _rows(text, "nt_squads", required):
        out.append(NTSquadPlayer(
            _require(r, "squad_id", "nt_squads", i),
            r.get("competition", ""),
            _require(r, "team_id", "nt_squads", i),
            _nt_date(r.get("announcement_date", ""), "announcement_date",
                     "nt_squads", i),
            r.get("competition_context", ""),
            _require(r, "player_name", "nt_squads", i),
            r.get("player_id", ""),
            _enum(_require(r, "position", "nt_squads", i), _POSITION_SET,
                  "position", "nt_squads", i).upper(),
            r.get("shirt_number", ""), r.get("club", ""),
            r.get("domestic_team_id", ""), r.get("club_country", ""),
            r.get("notes", ""), r.get("coach", ""),
        ))
    return out


def parse_nt_lineups(text: str) -> "list[NTLineupRow]":
    out: "list[NTLineupRow]" = []
    required = {"match_id", "team_id", "player_name", "position", "role"}
    for i, r in _rows(text, "nt_lineups", required):
        out.append(NTLineupRow(
            _require(r, "match_id", "nt_lineups", i),
            _require(r, "team_id", "nt_lineups", i),
            _require(r, "player_name", "nt_lineups", i),
            r.get("player_id", ""), r.get("shirt_number", ""),
            _enum(_require(r, "position", "nt_lineups", i), _POSITION_SET,
                  "position", "nt_lineups", i).upper(),
            _enum(_require(r, "role", "nt_lineups", i), NT_ROLES,
                  "role", "nt_lineups", i),
            _flag(r.get("captain", ""), "captain", "nt_lineups", i),
            r.get("minute_on", ""), r.get("minute_off", ""),
            r.get("replaced_player", ""),
            _flag(r.get("yellow_card", ""), "yellow_card", "nt_lineups", i),
            _flag(r.get("yellow_red_card", ""), "yellow_red_card", "nt_lineups", i),
            _flag(r.get("red_card", ""), "red_card", "nt_lineups", i),
        ))
    return out


def parse_nt_competitions(text: str) -> "list[NTGroupRow]":
    out: "list[NTGroupRow]" = []
    required = {"team_code", "competition_name"}
    for i, r in _rows(text, "nt_competitions", required):
        out.append(NTGroupRow(
            _require(r, "team_code", "nt_competitions", i),
            _require(r, "competition_name", "nt_competitions", i),
            r.get("group_name", ""), r.get("position", ""), r.get("played", ""),
            r.get("won", ""), r.get("drawn", ""), r.get("lost", ""),
            r.get("points", ""),
            _nt_date(r.get("last_update", ""), "last_update", "nt_competitions", i),
            r.get("wikipedia_url", ""),
            # Optional columns: a sheet that predates the full group table has
            # neither, and one row per team still parses.
            r.get("team_name", ""),
            r.get("goals_for", ""), r.get("goals_against", ""),
        ))
    return out


def parse_nt_knockout(text: str) -> "list[NTKnockoutTie]":
    """Every knockout tie, in sheet order — grouping into rounds is presentation.

    Only the structural columns are required: a seeded row naming nothing but
    its round and slot is the point, since that is how a bracket gets its shape
    before the draw is known.
    """
    out: "list[NTKnockoutTie]" = []
    seen: "dict[str, str]" = {}
    required = {"tie_id", "competition_name", "stage"}
    for i, r in _rows(text, "nt_knockout", required):
        tid = _require(r, "tie_id", "nt_knockout", i)
        _put(seen, tid, tid, "nt_knockout", i)
        out.append(NTKnockoutTie(
            tie_id=tid,
            competition_name=_require(r, "competition_name", "nt_knockout", i),
            # The league cup vocabulary, not a second one of our own.
            stage=_enum(r.get("stage", ""), dataset.KNOCKOUT_STAGES,
                        "stage", "nt_knockout", i),
            slot=_opt_int(r.get("slot", ""), "slot", "nt_knockout", i) or 0,
            home_name=r.get("home_name", ""),
            away_name=r.get("away_name", ""),
            home_from=_feed(r.get("home_from", ""), "home_from",
                            "nt_knockout", i),
            away_from=_feed(r.get("away_from", ""), "away_from",
                            "nt_knockout", i),
            home_score=_opt_int(r.get("home_score", ""), "home_score",
                                "nt_knockout", i),
            away_score=_opt_int(r.get("away_score", ""), "away_score",
                                "nt_knockout", i),
            home_pens=_opt_int(r.get("home_pens", ""), "home_pens",
                               "nt_knockout", i),
            away_pens=_opt_int(r.get("away_pens", ""), "away_pens",
                               "nt_knockout", i),
            extra_time=_flag(r.get("extra_time", ""), "extra_time",
                             "nt_knockout", i),
            date=_nt_date(r.get("date", ""), "date", "nt_knockout", i),
            kickoff=_nt_time(r.get("kickoff", ""), "kickoff", "nt_knockout", i),
            venue=r.get("venue", ""),
            city=r.get("city", ""),
            # A seeded row leaves this blank, and an unplayed tie is scheduled.
            status=_enum(r.get("status", "") or "scheduled", NT_MATCH_STATUSES,
                         "status", "nt_knockout", i),
            nt_match_id=r.get("nt_match_id", ""),
        ))
    return out


_NT_PARSERS = {
    "nt_teams": parse_nt_teams,
    "nt_matches": parse_nt_matches,
    "nt_goals": parse_nt_goals,
    "nt_squads": parse_nt_squads,
    "nt_competitions": parse_nt_competitions,
    "nt_lineups": parse_nt_lineups,
    "nt_knockout": parse_nt_knockout,
}


@dataclass
class NTData:
    """Every national-team tab, parsed — all teams, nothing filtered yet."""
    nt_teams: "dict[str, NTTeam]" = field(default_factory=dict)
    nt_matches: "dict[str, NTMatch]" = field(default_factory=dict)
    nt_goals: "dict[str, NTGoal]" = field(default_factory=dict)
    nt_squads: "list[NTSquadPlayer]" = field(default_factory=list)
    nt_competitions: "list[NTGroupRow]" = field(default_factory=list)
    nt_lineups: "list[NTLineupRow]" = field(default_factory=list)
    nt_knockout: "list[NTKnockoutTie]" = field(default_factory=list)


def parse_all(texts: "dict[str, str]") -> NTData:
    missing = set(dataset.NT_TABS) - set(texts)
    if missing:
        raise DataError(f"missing national-team tab(s): {', '.join(sorted(missing))}")
    return NTData(**{tab: _NT_PARSERS[tab](texts[tab]) for tab in dataset.NT_TABS})


# ── One team's view ──────────────────────────────────────────────────────────

@dataclass(frozen=True)
class NTSubstitution:
    on: NTLineupRow
    off: "NTLineupRow | None"    # None when the replaced player has no row
    minute: str

    @property
    def off_name(self) -> str:
        return self.off.player_name if self.off else self.on.replaced_player


@dataclass
class NTLineup:
    """One match's line-up, from our team's rows only."""
    starting: "list[NTLineupRow]"
    substitutions: "list[NTSubstitution]"
    unused: "list[NTLineupRow]"

    @property
    def any_rows(self) -> bool:
        return bool(self.starting or self.substitutions or self.unused)

    def starting_by_position(self) -> "list[tuple[str, list[NTLineupRow]]]":
        """[(label, rows)] in GK/DF/MF/FW order; empty groups omitted."""
        return _group_by_position(self.starting)


@dataclass
class NTResult:
    """A played match with both sides' scorers and any line-up attached."""
    match: NTMatch
    our_goals: "list[NTGoal]"
    their_goals: "list[NTGoal]"
    lineup: "NTLineup | None"


@dataclass
class NTSquad:
    """The current squad: the row group sharing the latest announcement_date."""
    announcement_date: str
    competition_context: str
    coach: str
    players: "list[NTSquadPlayer]"

    def by_position(self) -> "list[tuple[str, list[NTSquadPlayer]]]":
        return _group_by_position(self.players)


@dataclass
class NTGroup:
    """A whole group table: our row plus every rival's, in table order.

    The one place another team's rows legitimately reach the page — a group
    table is *about* the other teams. They are rival rows only: rows belonging
    to another team this site tracks (MW_M, MW_U20W) are dropped upstream, so
    a men's line can still never appear on the women's page.
    """
    competition_name: str
    group_name: str
    our_code: str
    rows: "list[NTGroupRow]"

    @property
    def title(self) -> str:
        return (f"{self.competition_name} · {self.group_name}"
                if self.group_name else self.competition_name)

    @property
    def our_row(self) -> "NTGroupRow | None":
        return next((r for r in self.rows if r.team_code == self.our_code), None)

    @property
    def has_goals(self) -> bool:
        """Goals/difference columns render only when some row supplies them."""
        return any(r.has_goals for r in self.rows)

    @property
    def last_update(self) -> str:
        """The freshest `last_update` in the group — one caption for the table."""
        return max((r.last_update for r in self.rows if r.last_update), default="")

    @property
    def wikipedia_url(self) -> str:
        return next((r.wikipedia_url for r in self.rows if r.wikipedia_url), "")

    def is_us(self, row: NTGroupRow) -> bool:
        return row.team_code == self.our_code


@dataclass(frozen=True)
class NTBracket:
    """One competition's knockout ties, ours folded in, ordered within a round.

    Not grouped into rounds here: which rounds exist, what they are called and
    what order they read in is presentation, and lives with the league cup
    bracket's vocabulary in `adapt` (see `nt_page._bracket`).
    """
    competition_name: str
    ties: "list[NTKnockoutTie]"


@dataclass
class NTTeamData:
    """One national team's whole page-worth of data. Contains no other team."""
    team: NTTeam
    coach: str
    next_match: "NTMatch | None"
    fixtures: "list[NTMatch]"       # scheduled, chronological
    results: "list[NTResult]"       # played, reverse chronological
    groups: "list[NTGroup]"
    brackets: "list[NTBracket]"
    squad: "NTSquad | None"

    @property
    def current_competition(self) -> str:
        """The tournament the team is in right now, as `nt_matches` names it.

        The next scheduled match decides it; with nothing scheduled, the most
        recent result does. Friendlies are not a tournament, so they answer ""
        — the page then has no current-competition section to show.
        """
        candidates = []
        if self.next_match is not None:
            candidates.append(self.next_match.competition)
        if self.results:
            candidates.append(self.results[0].match.competition)
        for comp in candidates:
            if comp and comp.strip().lower() != "friendly":
                return comp
        return ""

    def competition_results(self, competition: str) -> "list[NTResult]":
        return [r for r in self.results if r.match.competition == competition]

    def competition_fixtures(self, competition: str) -> "list[NTMatch]":
        return [m for m in self.fixtures if m.competition == competition]


def _group_by_position(rows):
    """Group squad/line-up rows GK -> DF -> MF -> FW, by shirt number within."""
    out = []
    for pos in POSITIONS:
        group = sorted((r for r in rows if r.position == pos),
                       key=lambda r: (r.shirt_sort, r.player_name))
        if group:
            out.append((POSITION_LABELS[pos], group))
    return out


def _lineup_for(rows: "list[NTLineupRow]") -> "NTLineup | None":
    """Fold one match's rows into starters, substitutions and unused subs.

    `replaced_player` is a name, so a substitution pairs by name against the
    same match's rows (that is all the tab offers). A sub_on row whose name
    does not match anything still renders — with the raw name and no minute
    from the other side — rather than being dropped.
    """
    if not rows:
        return None
    by_name = {r.player_name: r for r in rows}
    starting = [r for r in rows if r.role == "starting"]
    unused = [r for r in rows if r.role == "unused_sub"]
    subs = []
    for r in sorted((r for r in rows if r.role == "sub_on"),
                    key=lambda r: (_id_sort(r.minute_on), r.player_name)):
        off = by_name.get(r.replaced_player) if r.replaced_player else None
        minute = r.minute_on or (off.minute_off if off else "")
        subs.append(NTSubstitution(on=r, off=off, minute=minute))
    lineup = NTLineup(starting=starting, substitutions=subs, unused=unused)
    return lineup if lineup.any_rows else None


def _current_squad(rows: "list[NTSquadPlayer]") -> "NTSquad | None":
    """The row group sharing the most recent announcement_date.

    Rows with no announcement_date are only used when no dated row exists at
    all, in which case the highest squad_id wins — a squad list is still
    better than no squad list.
    """
    if not rows:
        return None
    dated = [r for r in rows if r.announcement_date]
    if dated:
        latest = max(r.announcement_date for r in dated)
        group = [r for r in dated if r.announcement_date == latest]
    else:
        latest_id = max((r.squad_id for r in rows), key=_id_sort)
        group = [r for r in rows if r.squad_id == latest_id]
    first = group[0]
    return NTSquad(
        announcement_date=first.announcement_date,
        competition_context=first.competition_context or first.competition,
        coach=next((r.coach for r in group if r.coach), ""),
        players=group,
    )


def load_team(texts: "dict[str, str]", team_code: str = SCORCHERS) -> NTTeamData:
    """Parse the nt_* tabs and reduce them to one team's page data.

    This is the only filter to `team_code` in the codebase: the tabs hold
    other teams' rows (MW_M, MW_U20W, ...) and opponent-coded goal/line-up
    rows (NIGERIA_W), none of which survive past this function except as the
    opponent side of one of *our* matches.
    """
    nt = parse_all(texts)
    return team_data(nt, team_code)


def team_data(nt: NTData, team_code: str = SCORCHERS) -> NTTeamData:
    """The filtering half of load_team, for callers that already parsed."""
    team = nt.nt_teams.get(team_code)
    if team is None:
        raise DataError(f"nt_teams has no row for {team_code!r}")

    matches = [m for m in nt.nt_matches.values() if m.team_code == team_code]
    our_ids = {m.match_id for m in matches}

    # Goals for our matches, BOTH sides: ours are the rows whose team_id is
    # our code, the opponent's are everything else (the opponent has no code
    # we could resolve — see the module docstring).
    goals_by_match: "dict[str, list[NTGoal]]" = {}
    for g in nt.nt_goals.values():
        if g.match_id in our_ids:
            goals_by_match.setdefault(g.match_id, []).append(g)

    lineup_rows: "dict[str, list[NTLineupRow]]" = {}
    for r in nt.nt_lineups:
        if r.match_id in our_ids and r.team_id == team_code:
            lineup_rows.setdefault(r.match_id, []).append(r)

    results = []
    for m in sorted((m for m in matches if m.played),
                    key=lambda m: m.recent_key, reverse=True):
        gs = sorted(goals_by_match.get(m.match_id, []),
                    key=lambda g: (g.minute_sort, g.player_name))
        results.append(NTResult(
            match=m,
            our_goals=[g for g in gs if g.team_id == team_code],
            their_goals=[g for g in gs if g.team_id != team_code],
            lineup=_lineup_for(lineup_rows.get(m.match_id, [])),
        ))

    fixtures = sorted((m for m in matches if m.scheduled), key=lambda m: m.sort_key)
    # Soonest dated fixture; an undated one only if that is all there is.
    next_match = fixtures[0] if fixtures else None

    squad = _current_squad([r for r in nt.nt_squads if r.team_id == team_code])
    # The coach of record: the most recent match that names one (the sheet
    # leaves the column blank on older rows), else the current squad's.
    coach = next((m.coach for m in sorted(matches, key=lambda m: m.recent_key,
                                          reverse=True) if m.coach), "")
    if not coach and squad:
        coach = squad.coach

    # A bracket hangs off the competitions this team has a group table in, so
    # the groups have to exist before it can be looked up.
    groups = _groups_for(nt, team_code)

    return NTTeamData(
        team=team,
        coach=coach,
        next_match=next_match,
        fixtures=fixtures,
        results=results,
        groups=groups,
        brackets=_brackets_for(nt, groups),
        squad=squad,
    )


def _groups_for(nt: NTData, team_code: str) -> "list[NTGroup]":
    """Every group table our team appears in, each carrying its rivals' rows.

    Membership is by (competition_name, group_name): the sheet's rival rows sit
    in the same group as ours. Rows for *another* tracked national team are
    excluded even when they share a group key, so the filter promise in the
    module docstring still holds — only rivals with no `nt_teams` row join ours.
    """
    ours = [c for c in nt.nt_competitions if c.team_code == team_code]
    keys = []
    for row in ours:
        if row.group_key not in keys:
            keys.append(row.group_key)

    out = []
    for key in keys:
        rows = [
            c for c in nt.nt_competitions
            if c.group_key == key
            and (c.team_code == team_code or c.team_code not in nt.nt_teams)
        ]
        rows.sort(key=lambda r: r.sort_key)
        out.append(NTGroup(competition_name=key[0], group_name=key[1],
                           our_code=team_code, rows=rows))
    return out


def _norm(name: str) -> str:
    return name.strip().casefold()


def _link_match(tie: NTKnockoutTie, nt: NTData) -> NTKnockoutTie:
    """Fold the `nt_matches` row a tie names into it, or return it unchanged.

    Our own knockout ties exist in both tabs — here for the bracket's shape,
    and in `nt_matches` for the scorers, line-up and everything else a result
    carries. Rather than ask for the score twice and let the two drift, the
    match row is the authority for what happened and the knockout row keeps
    only what a Malawi-perspective row cannot express: which round, which slot,
    and which way round the two names read.

    Which side is ours is read off `NTMatch.opponent`, which names the other
    one — so nothing here needs to know our own country's name.
    """
    if not tie.nt_match_id:
        return tie
    m = nt.nt_matches.get(tie.nt_match_id)
    if m is None:
        # validate.check_nt reports the dangling id; a build never crashes here.
        return tie
    ours_home = _norm(tie.away_name) == _norm(m.opponent)
    home_score, away_score = ((m.team_score, m.opponent_score) if ours_home
                              else (m.opponent_score, m.team_score))
    return replace(
        tie,
        home_score=home_score, away_score=away_score,
        extra_time=m.extra_time,
        date=m.date, kickoff=m.kickoff, venue=m.venue, city=m.city,
        status=m.status,
        match=m, ours_side="home" if ours_home else "away",
    )


def _resolve_feeds(ties: "list[NTKnockoutTie]") -> "list[NTKnockoutTie]":
    """Fill each slot whose feeding tie has been decided, following chains.

    A quarter-final result promotes a name into the semi-final, which may then
    decide it and promote a name into the final, so this repeats until nothing
    more can be filled rather than assuming any round order. An already-named
    side is never overwritten: the sheet stays the authority, and a bracket
    where someone typed the semi-finalists by hand still works.
    """
    out = {t.tie_id: t for t in ties}
    # Each pass fills at least one slot or stops, so the tree depth bounds it.
    for _ in range(len(out) + 1):
        changed = False
        for tie_id, tie in list(out.items()):
            updates = {}
            for side in ("home", "away"):
                if getattr(tie, f"{side}_name"):
                    continue
                feed = tie.feed(side)
                source = out.get(feed[1]) if feed else None
                if source is None or source.tie_id == tie_id:
                    continue
                name = (source.winner_name if feed[0] == "winner"
                        else source.loser_name)
                if name:
                    updates[f"{side}_name"] = name
            if updates:
                out[tie_id] = replace(tie, **updates)
                changed = True
        if not changed:
            break
    return [out[t.tie_id] for t in ties]


def _brackets_for(nt: NTData, groups: "list[NTGroup]") -> "list[NTBracket]":
    """The knockout bracket for each competition this team has a group in.

    That join — `nt_knockout.competition_name` against
    `nt_competitions.competition_name` — is what attaches a bracket to a page,
    and `validate.check_nt` rejects a bracket that matches no group table, so a
    typo shows up as a build error rather than as a section that never renders.
    """
    names = []
    for g in groups:
        if g.competition_name not in names:
            names.append(g.competition_name)

    out = []
    for name in names:
        ties = [_link_match(t, nt) for t in nt.nt_knockout
                if t.competition_name == name]
        if ties:
            # Results first, then promote winners into the rounds they feed.
            ties = _resolve_feeds(ties)
            ties.sort(key=lambda t: t.sort_key)
            out.append(NTBracket(competition_name=name, ties=ties))
    return out
