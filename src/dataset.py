"""New-schema data layer: fetch and parse the 13 normalized tabs.

This is the only module that may know the CSV URLs of the new schema —
everything downstream (validator, standings, rendering) works from the parsed
`Dataset`. It replaces the old per-league src/data.py, which remains in place
only until the production build is swapped over.

Schema conventions (as built — do not "fix"):
  * ID separator is underscore, country prefix ``MW_`` (club ``MW_BULL``,
    team ``MW_BULL_M1``). U16 teams use bare IDs where team_id == club_id.
    NEVER derive meaning by parsing an ID — always join through the tabs.
  * ``player_id`` is ``CAF_MW_000123`` plus the reserved ``CAF_MW_UNKNOWN``.
  * The current season comes from ``seasons.status == 'active'``, never from
    the system clock.
  * ``goals.player_name`` is denormalized junk — it is deliberately not even
    parsed here; names resolve via player_id -> players.
  * Dates are strict ``YYYY-MM-DD``; a blank date is allowed only where the
    sheet legitimately has none (unscheduled fixtures, unknown DOBs).
"""

from dataclasses import dataclass, field
from datetime import datetime
import csv
import io
import os
import urllib.request


class DataError(Exception):
    """A problem with the source data that must stop the build loudly."""


# ── Sources ──────────────────────────────────────────────────────────────────

# Single published spreadsheet; each tab is one gid. Override the base with
# env DATASET_BASE_URL, or point DATASET_LOCAL_DIR at a directory of
# {tab}.csv files to build fully offline (tests, drift snapshots).
BASE_URL = (
    "https://docs.google.com/spreadsheets/d/e/"
    "2PACX-1vSF7xMvjTyQLckW3IHBIip7msX2H4qj0MS8Yedatly3LJXDosMvjSz4MbSq42rxzL"
    "-qa3ehnJuaMZP6/pub"
)

TAB_GIDS = {
    "clubs": 1571065713,
    "teams": 1542712062,
    "competitions": 1088082573,
    "seasons": 232948228,
    "competition_seasons": 667630842,
    "entries": 1469327288,
    "venues": 2142346215,
    "matches": 783604265,
    "goals": 247287352,
    "players": 576599713,
    "registrations": 705142832,
    "reporters": 1509513646,
    "aliases": 1570860122,
}

TABS = tuple(TAB_GIDS)

UNKNOWN_PLAYER_ID = "CAF_MW_UNKNOWN"


def tab_url(tab: str) -> str:
    base = os.environ.get("DATASET_BASE_URL", BASE_URL)
    return f"{base}?gid={TAB_GIDS[tab]}&single=true&output=csv"


def fetch_tab(tab: str) -> str:
    """Return the raw CSV text of one tab (network, or DATASET_LOCAL_DIR)."""
    if tab not in TAB_GIDS:
        raise DataError(f"unknown tab {tab!r}")
    local = os.environ.get("DATASET_LOCAL_DIR")
    if local:
        with open(os.path.join(local, f"{tab}.csv"), encoding="utf-8") as fh:
            return fh.read()
    req = urllib.request.Request(tab_url(tab), headers={"User-Agent": "fb-mw-build"})
    with urllib.request.urlopen(req, timeout=60) as resp:
        return resp.read().decode("utf-8")


def fetch_all() -> "dict[str, str]":
    """Fetch every tab; returns {tab_name: csv_text}."""
    return {tab: fetch_tab(tab) for tab in TABS}


# ── Enums (as built) ─────────────────────────────────────────────────────────

MATCH_STATUSES = frozenset(
    {"scheduled", "played", "postponed", "abandoned", "awarded", "cancelled"}
)
SOURCE_TYPES = frozenset(
    {"reporter", "rfa", "fa", "club", "facebook", "newspaper", "whatsapp",
     "backfill", "placeholder", "unknown"}
)
CONFIDENCES = frozenset({"unconfirmed", "confirmed", "official"})
# "" = ordinary goal with no recorded type; the sheet leaves the cell blank.
GOAL_TYPES = frozenset({"", "open_play", "penalty", "free_kick", "header", "own_goal"})
GENDERS = frozenset({"m", "w"})
AGE_GROUPS = frozenset({"senior", "u20", "u19", "u17", "u16", "u15"})
SQUAD_LEVELS = frozenset({1, 2, 3, 4})


# ── Records ──────────────────────────────────────────────────────────────────

@dataclass(frozen=True)
class Club:
    club_id: str
    name: str
    short_name: str
    city: str
    region: str
    founded: str
    crest: str
    status: str
    successor_club_id: str
    notes: str


@dataclass(frozen=True)
class Team:
    team_id: str
    club_id: str
    gender: str          # m | w
    age_group: str       # senior | u20 | u19 | u17 | u16 | u15 (lowercased)
    squad_level: int     # 1-4
    display_name: str
    legacy_code: str     # old per-league sheet code, e.g. SL_BE (logo rename key)
    status: str


@dataclass(frozen=True)
class Competition:
    competition_id: str
    country: str
    name: str
    type: str            # league | cup | ...
    tier: "int | None"
    gender: str
    age_group: str       # lowercased so it joins cleanly against teams
    region: str
    governing_body: str
    logo: str


@dataclass(frozen=True)
class Season:
    season_id: str
    country: str
    label: str           # e.g. "2026/27"
    start_date: str      # YYYY-MM-DD
    end_date: str        # YYYY-MM-DD
    status: str          # active | complete


@dataclass(frozen=True)
class CompetitionSeason:
    competition_id: str
    season_id: str
    sponsor_name: str    # display name override when non-empty
    format: str
    teams_count: "int | None"
    promotion_places: "int | None"
    relegation_places: "int | None"
    points_win: int
    points_draw: int
    status: str


@dataclass(frozen=True)
class Entry:
    entry_id: str
    competition_id: str
    season_id: str
    team_id: str
    group: str
    points_adjustment: int   # 0 when blank; can be negative
    adjustment_reason: str
    status: str              # active (blank normalized) | withdrawn | expelled


@dataclass(frozen=True)
class Venue:
    venue_id: str
    name: str
    city: str
    capacity: str


@dataclass(frozen=True)
class Match:
    match_id: str
    competition_id: str
    season_id: str
    stage: str
    matchday: "int | None"
    date: str            # YYYY-MM-DD or "" (not yet scheduled to a day)
    kickoff: str
    venue_id: str        # may be ""
    home_team_id: str
    away_team_id: str
    home_goals: "int | None"
    away_goals: "int | None"
    status: str
    awarded_note: str
    source_type: str
    source_ref: str
    reported_by: str
    reported_at: str
    confidence: str
    verified_by: str
    verified_at: str

    @property
    def is_placeholder(self) -> bool:
        """Known-fake seed row: parse without error, render nowhere."""
        return self.source_type == "placeholder"

    @property
    def counts_for_table(self) -> bool:
        """played and awarded matches carry a real score into standings."""
        return self.status in ("played", "awarded") and not self.is_placeholder

    @property
    def has_score(self) -> bool:
        return self.home_goals is not None and self.away_goals is not None


@dataclass(frozen=True)
class Goal:
    goal_id: str
    match_id: str
    team_id: str         # team the goal counted FOR (own goals: the beneficiary)
    player_id: str       # who physically scored; CAF_MW_UNKNOWN when unknown
    minute: str          # raw display string, may be ""
    stoppage: str
    period: str
    goal_type: str       # "" | open_play | penalty | free_kick | header | own_goal
    assist_player_id: str
    source_type: str
    source_ref: str
    reported_by: str
    reported_at: str
    confidence: str
    verified_by: str
    verified_at: str

    @property
    def is_own_goal(self) -> bool:
        return self.goal_type == "own_goal"

    @property
    def is_penalty(self) -> bool:
        return self.goal_type == "penalty"

    @property
    def minute_sort(self) -> "tuple[int, int]":
        """Orders injury time correctly: 45 < 45+1 < 46; blanks sort last."""
        base = self.minute.strip()
        added = self.stoppage.strip()
        try:
            b = int(base)
        except ValueError:
            return (10**6, 0)
        try:
            a = int(added) if added else 0
        except ValueError:
            a = 0
        return (b, a)


@dataclass(frozen=True)
class Player:
    player_id: str
    full_name: str
    known_as: str
    dob: str
    position: str
    nationality: str
    status: str

    @property
    def display_name(self) -> str:
        # A handful of rows are reserved ID slots with no name yet; falling
        # back to the ID keeps them renderable if ever referenced.
        return self.known_as or self.full_name or self.player_id


@dataclass(frozen=True)
class Registration:
    player_id: str
    team_id: str
    season_id: str
    shirt_number: str
    from_date: str
    to_date: str


@dataclass(frozen=True)
class Reporter:
    reporter_id: str
    name: str
    email: str
    affiliation: str
    affiliation_id: str
    region: str
    active: str
    public_byline: str


@dataclass(frozen=True)
class Alias:
    alias_text: str
    entity_type: str
    entity_id: str
    context: str


@dataclass
class Dataset:
    """Every tab, parsed and keyed by primary key (insertion order preserved)."""
    clubs: "dict[str, Club]" = field(default_factory=dict)
    teams: "dict[str, Team]" = field(default_factory=dict)
    competitions: "dict[str, Competition]" = field(default_factory=dict)
    seasons: "dict[str, Season]" = field(default_factory=dict)
    # keyed (competition_id, season_id)
    competition_seasons: "dict[tuple[str, str], CompetitionSeason]" = field(default_factory=dict)
    entries: "dict[str, Entry]" = field(default_factory=dict)
    venues: "dict[str, Venue]" = field(default_factory=dict)
    matches: "dict[str, Match]" = field(default_factory=dict)
    goals: "dict[str, Goal]" = field(default_factory=dict)
    players: "dict[str, Player]" = field(default_factory=dict)
    registrations: "list[Registration]" = field(default_factory=list)
    reporters: "dict[str, Reporter]" = field(default_factory=dict)
    aliases: "list[Alias]" = field(default_factory=list)

    def active_season(self) -> Season:
        """The single season with status=active. Never the system clock."""
        active = [s for s in self.seasons.values() if s.status == "active"]
        if len(active) != 1:
            raise DataError(
                f"expected exactly one season with status='active', found "
                f"{len(active)}: {[s.season_id for s in active]}"
            )
        return active[0]

    def league_display_name(self, competition_id: str, season_id: str) -> str:
        """sponsor_name when non-empty, else competitions.name."""
        cs = self.competition_seasons.get((competition_id, season_id))
        if cs and cs.sponsor_name:
            return cs.sponsor_name
        return self.competitions[competition_id].name

    def player_display_name(self, player_id: str) -> str:
        return self.players[player_id].display_name


# ── Parsing helpers ──────────────────────────────────────────────────────────

def _rows(text: str, tab: str, required: "set[str]"):
    """Yield (row_number, {col: stripped_value}) for non-blank rows."""
    reader = csv.DictReader(io.StringIO(text))
    have = {(f or "").strip() for f in (reader.fieldnames or [])}
    missing = required - have
    if missing:
        raise DataError(
            f"{tab}: missing column(s): {', '.join(sorted(missing))}. "
            f"Found: {', '.join(sorted(have)) or '(none)'}"
        )
    for i, raw in enumerate(reader, start=2):
        row = {(k or "").strip(): (v or "").strip() for k, v in raw.items() if k}
        if any(row.values()):
            yield i, row


def _require(row, col, tab, i):
    v = row.get(col, "")
    if not v:
        raise DataError(f"{tab} row {i}: blank {col}")
    return v


def _int(value, label, tab, i):
    try:
        return int(value)
    except ValueError:
        raise DataError(f"{tab} row {i}: {label} {value!r} is not an integer")


def _opt_int(value, label, tab, i):
    return _int(value, label, tab, i) if value else None


def _date(value, label, tab, i, required=False):
    """Strict YYYY-MM-DD; blank allowed unless required. Returns the string."""
    if not value:
        if required:
            raise DataError(f"{tab} row {i}: blank {label}")
        return ""
    try:
        datetime.strptime(value, "%Y-%m-%d")
    except ValueError:
        raise DataError(f"{tab} row {i}: {label} {value!r} is not YYYY-MM-DD")
    return value


def _source_type(value, tab, i):
    """Blank source_type is common in the sheet; it means 'unknown'."""
    return _enum(value or "unknown", SOURCE_TYPES, "source_type", tab, i)


def _enum(value, allowed, label, tab, i):
    v = value.lower()
    if v not in allowed:
        raise DataError(
            f"{tab} row {i}: {label} {value!r} not in "
            f"{{{', '.join(sorted(a for a in allowed if a))}}}"
        )
    return v


def _put(store, key, record, tab, i):
    if key in store:
        raise DataError(f"{tab} row {i}: duplicate primary key {key!r}")
    store[key] = record


# ── Tab parsers ──────────────────────────────────────────────────────────────

def parse_clubs(text: str) -> "dict[str, Club]":
    out: "dict[str, Club]" = {}
    for i, r in _rows(text, "clubs", {"club_id", "name", "status"}):
        cid = _require(r, "club_id", "clubs", i)
        _put(out, cid, Club(
            cid, _require(r, "name", "clubs", i), r.get("short_name", ""),
            r.get("city", ""), r.get("region", ""), r.get("founded", ""),
            r.get("crest", ""), r.get("status", ""),
            r.get("successor_club_id", ""), r.get("notes", ""),
        ), "clubs", i)
    return out


def parse_teams(text: str) -> "dict[str, Team]":
    out: "dict[str, Team]" = {}
    required = {"team_id", "club_id", "gender", "age_group", "squad_level",
                "display_name", "legacy_code", "status"}
    for i, r in _rows(text, "teams", required):
        tid = _require(r, "team_id", "teams", i)
        level = _int(_require(r, "squad_level", "teams", i), "squad_level", "teams", i)
        if level not in SQUAD_LEVELS:
            raise DataError(f"teams row {i}: squad_level {level} not in 1-4")
        _put(out, tid, Team(
            tid, _require(r, "club_id", "teams", i),
            _enum(_require(r, "gender", "teams", i), GENDERS, "gender", "teams", i),
            _enum(_require(r, "age_group", "teams", i), AGE_GROUPS, "age_group", "teams", i),
            level, _require(r, "display_name", "teams", i),
            r.get("legacy_code", ""), r.get("status", ""),
        ), "teams", i)
    return out


def parse_competitions(text: str) -> "dict[str, Competition]":
    out: "dict[str, Competition]" = {}
    required = {"competition_id", "country", "name", "type", "tier", "gender",
                "age_group"}
    for i, r in _rows(text, "competitions", required):
        cid = _require(r, "competition_id", "competitions", i)
        _put(out, cid, Competition(
            cid, r.get("country", "").lower(),
            _require(r, "name", "competitions", i),
            _require(r, "type", "competitions", i),
            _opt_int(r.get("tier", ""), "tier", "competitions", i),
            _enum(_require(r, "gender", "competitions", i), GENDERS,
                  "gender", "competitions", i),
            _enum(_require(r, "age_group", "competitions", i), AGE_GROUPS,
                  "age_group", "competitions", i),
            r.get("region", ""), r.get("governing_body", ""), r.get("logo", ""),
        ), "competitions", i)
    return out


def parse_seasons(text: str) -> "dict[str, Season]":
    out: "dict[str, Season]" = {}
    required = {"season_id", "country", "label", "start_date", "end_date", "status"}
    for i, r in _rows(text, "seasons", required):
        sid = _require(r, "season_id", "seasons", i)
        _put(out, sid, Season(
            sid, r.get("country", ""), _require(r, "label", "seasons", i),
            _date(r.get("start_date", ""), "start_date", "seasons", i, required=True),
            _date(r.get("end_date", ""), "end_date", "seasons", i, required=True),
            _enum(_require(r, "status", "seasons", i), {"active", "complete"},
                  "status", "seasons", i),
        ), "seasons", i)
    return out


def parse_competition_seasons(text: str) -> "dict[tuple[str, str], CompetitionSeason]":
    out: "dict[tuple[str, str], CompetitionSeason]" = {}
    required = {"competition_id", "season_id", "sponsor_name", "points_win",
                "points_draw", "status"}
    for i, r in _rows(text, "competition_seasons", required):
        key = (_require(r, "competition_id", "competition_seasons", i),
               _require(r, "season_id", "competition_seasons", i))
        _put(out, key, CompetitionSeason(
            key[0], key[1], r.get("sponsor_name", ""), r.get("format", ""),
            _opt_int(r.get("teams_count", ""), "teams_count", "competition_seasons", i),
            _opt_int(r.get("promotion_places", ""), "promotion_places",
                     "competition_seasons", i),
            _opt_int(r.get("relegation_places", ""), "relegation_places",
                     "competition_seasons", i),
            _int(_require(r, "points_win", "competition_seasons", i),
                 "points_win", "competition_seasons", i),
            _int(_require(r, "points_draw", "competition_seasons", i),
                 "points_draw", "competition_seasons", i),
            r.get("status", ""),
        ), "competition_seasons", i)
    return out


def parse_entries(text: str) -> "dict[str, Entry]":
    out: "dict[str, Entry]" = {}
    required = {"entry_id", "competition_id", "season_id", "team_id",
                "points_adjustment", "adjustment_reason", "status"}
    for i, r in _rows(text, "entries", required):
        eid = _require(r, "entry_id", "entries", i)
        adj = r.get("points_adjustment", "")
        _put(out, eid, Entry(
            eid, _require(r, "competition_id", "entries", i),
            _require(r, "season_id", "entries", i),
            _require(r, "team_id", "entries", i),
            r.get("group", ""),
            _int(adj, "points_adjustment", "entries", i) if adj else 0,
            r.get("adjustment_reason", ""),
            # Blank status means an ordinary active entry.
            _enum(r.get("status") or "active", {"active", "withdrawn", "expelled"},
                  "status", "entries", i),
        ), "entries", i)
    return out


def parse_venues(text: str) -> "dict[str, Venue]":
    out: "dict[str, Venue]" = {}
    for i, r in _rows(text, "venues", {"venue_id", "name"}):
        vid = _require(r, "venue_id", "venues", i)
        _put(out, vid, Venue(
            vid, _require(r, "name", "venues", i),
            r.get("city", ""), r.get("capacity", ""),
        ), "venues", i)
    return out


def parse_matches(text: str) -> "dict[str, Match]":
    out: "dict[str, Match]" = {}
    required = {"match_id", "competition_id", "season_id", "matchday", "date",
                "venue_id", "home_team_id", "away_team_id", "home_goals",
                "away_goals", "status", "source_type", "confidence"}
    for i, r in _rows(text, "matches", required):
        mid = _require(r, "match_id", "matches", i)
        status = _enum(_require(r, "status", "matches", i), MATCH_STATUSES,
                       "status", "matches", i)
        hg = _opt_int(r.get("home_goals", ""), "home_goals", "matches", i)
        ag = _opt_int(r.get("away_goals", ""), "away_goals", "matches", i)
        for label, g in (("home_goals", hg), ("away_goals", ag)):
            if g is not None and g < 0:
                raise DataError(f"matches row {i}: {label} cannot be negative ({g})")
        _put(out, mid, Match(
            mid, _require(r, "competition_id", "matches", i),
            _require(r, "season_id", "matches", i),
            r.get("stage", ""),
            _opt_int(r.get("matchday", ""), "matchday", "matches", i),
            _date(r.get("date", ""), "date", "matches", i),
            r.get("kickoff", ""), r.get("venue_id", ""),
            _require(r, "home_team_id", "matches", i),
            _require(r, "away_team_id", "matches", i),
            hg, ag, status, r.get("awarded_note", ""),
            _source_type(r.get("source_type", ""), "matches", i),
            r.get("source_ref", ""), r.get("reported_by", ""),
            r.get("reported_at", ""),
            _enum(_require(r, "confidence", "matches", i), CONFIDENCES,
                  "confidence", "matches", i),
            r.get("verified_by", ""), r.get("verified_at", ""),
        ), "matches", i)
    return out


def parse_goals(text: str) -> "dict[str, Goal]":
    out: "dict[str, Goal]" = {}
    # player_name is intentionally absent: the denormalized column is ignored;
    # names come from player_id -> players.
    required = {"goal_id", "match_id", "team_id", "player_id", "minute",
                "goal_type", "source_type", "confidence"}
    for i, r in _rows(text, "goals", required):
        gid = _require(r, "goal_id", "goals", i)
        _put(out, gid, Goal(
            gid, _require(r, "match_id", "goals", i),
            _require(r, "team_id", "goals", i),
            _require(r, "player_id", "goals", i),
            r.get("minute", ""), r.get("stoppage", ""), r.get("period", ""),
            _enum(r.get("goal_type", ""), GOAL_TYPES, "goal_type", "goals", i),
            r.get("assist_player_id", ""),
            _source_type(r.get("source_type", ""), "goals", i),
            r.get("source_ref", ""), r.get("reported_by", ""),
            r.get("reported_at", ""),
            _enum(_require(r, "confidence", "goals", i), CONFIDENCES,
                  "confidence", "goals", i),
            r.get("verified_by", ""), r.get("verified_at", ""),
        ), "goals", i)
    return out


def parse_players(text: str) -> "dict[str, Player]":
    out: "dict[str, Player]" = {}
    required = {"player_id", "full_name", "status"}
    for i, r in _rows(text, "players", required):
        pid = _require(r, "player_id", "players", i)
        _put(out, pid, Player(
            pid, r.get("full_name", ""), r.get("known_as", ""),
            _date(r.get("dob", ""), "dob", "players", i),
            r.get("position", ""), r.get("nationality", ""), r.get("status", ""),
        ), "players", i)
    return out


def parse_registrations(text: str) -> "list[Registration]":
    out: "list[Registration]" = []
    required = {"player_id", "team_id", "season_id"}
    for i, r in _rows(text, "registrations", required):
        out.append(Registration(
            _require(r, "player_id", "registrations", i),
            _require(r, "team_id", "registrations", i),
            _require(r, "season_id", "registrations", i),
            r.get("shirt_number", ""),
            _date(r.get("from_date", ""), "from_date", "registrations", i),
            _date(r.get("to_date", ""), "to_date", "registrations", i),
        ))
    return out


def parse_reporters(text: str) -> "dict[str, Reporter]":
    out: "dict[str, Reporter]" = {}
    required = {"reporter_id", "name"}
    for i, r in _rows(text, "reporters", required):
        rid = _require(r, "reporter_id", "reporters", i)
        _put(out, rid, Reporter(
            rid, _require(r, "name", "reporters", i), r.get("email", ""),
            r.get("affiliation", ""), r.get("affiliation_id", ""),
            r.get("region", ""), r.get("active", ""), r.get("public_byline", ""),
        ), "reporters", i)
    return out


def parse_aliases(text: str) -> "list[Alias]":
    out: "list[Alias]" = []
    required = {"alias_text", "entity_type", "entity_id"}
    for i, r in _rows(text, "aliases", required):
        out.append(Alias(
            _require(r, "alias_text", "aliases", i),
            _require(r, "entity_type", "aliases", i),
            _require(r, "entity_id", "aliases", i),
            r.get("context", ""),
        ))
    return out


_PARSERS = {
    "clubs": parse_clubs,
    "teams": parse_teams,
    "competitions": parse_competitions,
    "seasons": parse_seasons,
    "competition_seasons": parse_competition_seasons,
    "entries": parse_entries,
    "venues": parse_venues,
    "matches": parse_matches,
    "goals": parse_goals,
    "players": parse_players,
    "registrations": parse_registrations,
    "reporters": parse_reporters,
    "aliases": parse_aliases,
}


def parse_all(texts: "dict[str, str]") -> Dataset:
    """Parse {tab: csv_text} (from fetch_all or a snapshot) into a Dataset."""
    missing = set(TABS) - set(texts)
    if missing:
        raise DataError(f"missing tab(s): {', '.join(sorted(missing))}")
    return Dataset(**{tab: _PARSERS[tab](texts[tab]) for tab in TABS})


def load() -> Dataset:
    """Fetch and parse everything in one call."""
    return parse_all(fetch_all())
