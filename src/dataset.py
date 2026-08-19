"""Data layer: fetch and parse the 13 normalized tabs.

This is the only module that may know where the data comes from — everything
downstream (validator, standings, rendering) works from the parsed `Dataset`.

**Supabase/Postgres is the source of truth** (DATASET_SOURCE=supabase, the
default in CI). The Google Spreadsheet path below is DEPRECATED and kept only
as an emergency fallback and for historical reference; reporters write to
Supabase through /report, and nothing writes to the sheet any more. Do not add
features to the sheet path — it will be deleted once the Supabase-backed build
has run unattended for a season.

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
import concurrent.futures
import csv
import io
import os
import socket
import time
import urllib.error
import urllib.request

from . import lineups


class DataError(Exception):
    """A problem with the source data that must stop the build loudly."""


# ── Sources ──────────────────────────────────────────────────────────────────

# DEPRECATED — the published Google Spreadsheet. Supabase replaced it as the
# source of truth; this remains as a fallback (DATASET_SOURCE=sheets) and is
# no longer written to by anyone. Each tab is one gid. Override the base with
# env DATASET_BASE_URL, or point DATASET_LOCAL_DIR at a directory of
# {tab}.csv files to build fully offline (tests, parity checks).
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

# Tabs with no gid above: they postdate the spreadsheet and exist only in
# Postgres. Under DATASET_SOURCE=sheets — the deprecated emergency fallback —
# they resolve to a header-only CSV rather than a failed fetch, so the fallback
# still builds a whole site, minus the data it never had.
SUPABASE_ONLY_TABS = {
    "lineups": ("match_id", "team_id", "player_name", "player_id",
                "shirt_number", "position", "role", "captain", "motm",
                "minute_on", "minute_off", "replaced_player", "yellow_card",
                "yellow_red_card", "red_card"),
    # 0024. Referees and coaches as identities. Empty under the sheets
    # fallback, which is exactly right: every match then renders the names it
    # already holds as plain text, and nothing links anywhere.
    "officials": ("official_id", "full_name", "known_as", "kind", "status"),
    # 0030. The homepage carousel. Empty here means the landing page falls back
    # to the hand-written featured card build.py has always carried, which is
    # the same answer it gives when nobody has published a card yet.
    "trending": ("card_id", "status", "eyebrow", "headline", "body",
                 "link_url", "link_label", "image_path", "image_alt",
                 "sort_order", "published_at"),
}

TABS = tuple(TAB_GIDS) + tuple(SUPABASE_ONLY_TABS)

# The national-team tabs, same spreadsheet. They are a separate schema with
# their own ids (nt_teams.team_code, not teams.team_id) and are parsed by
# src/nt.py, so they stay out of TABS/Dataset — nothing in the league pipeline
# sees them. This module remains the only place that knows any CSV URL.
NT_TAB_GIDS = {
    "nt_teams": 1346178487,
    "nt_matches": 933880169,
    "nt_goals": 1559520207,
    "nt_squads": 591651148,
    "nt_competitions": 966765466,
    "nt_lineups": 1873964167,
    "nt_knockout": 319721285,
}

NT_TABS = tuple(NT_TAB_GIDS)

_ALL_GIDS = {**TAB_GIDS, **NT_TAB_GIDS}

UNKNOWN_PLAYER_ID = "CAF_MW_UNKNOWN"

# Every kickoff in the sheet — league tabs and nt_* alike — is entered in
# Malawi's clock, so a reader here never has to convert one. Malawi keeps CAT
# (UTC+2) year round, no DST. Both schemas label a kickoff with this.
KICKOFF_TZ = "CAT"


def tab_url(tab: str) -> str:
    base = os.environ.get("DATASET_BASE_URL", BASE_URL)
    return f"{base}?gid={_ALL_GIDS[tab]}&single=true&output=csv"


# Google's published-CSV endpoint answers a healthy request in about a second
# — the largest tab measured 85KB in 1.0s — but stalls outright on roughly a
# third of them: the connection opens, the request goes out, and no response
# line ever comes back. The stalls are random draws rather than rate-limiting,
# so pacing the requests does not reduce them and a retry is as likely to work
# as the attempt before it.
#
# The timeout is therefore a stall detector, not a patience setting: anything
# past a few seconds is never going to answer. Keeping it tight is what makes
# a stall cheap, and the attempt count is what keeps a run of them from losing
# the build (at a ~34% stall rate, eight attempts is ~1 failed tab in 5000).
# The old 60s x 3 was the opposite trade — every stall cost a full minute and
# three in a row still failed.
FETCH_TIMEOUT = 8           # seconds per attempt
FETCH_ATTEMPTS = 8
FETCH_RETRY_PAUSE = 0.5     # seconds


def empty_csv(tab: str) -> str:
    """A header-only CSV for a tab that has no rows to offer from this source."""
    return ",".join(SUPABASE_ONLY_TABS[tab]) + "\n"


def fetch_tab(tab: str) -> str:
    """Return the raw CSV text of one tab (network, or DATASET_LOCAL_DIR)."""
    if tab not in _ALL_GIDS and tab not in SUPABASE_ONLY_TABS:
        raise DataError(f"unknown tab {tab!r}")
    local = os.environ.get("DATASET_LOCAL_DIR")
    if local:
        path = os.path.join(local, f"{tab}.csv")
        # A snapshot taken before a Supabase-only tab existed simply has no
        # file for it. That is an empty tab, not a broken build — the same
        # answer the sheets fallback gives.
        if tab in SUPABASE_ONLY_TABS and not os.path.exists(path):
            return empty_csv(tab)
        with open(path, encoding="utf-8") as fh:
            return fh.read()
    if tab in SUPABASE_ONLY_TABS:
        return empty_csv(tab)
    req = urllib.request.Request(tab_url(tab), headers={"User-Agent": "fb-mw-build"})
    for attempt in range(1, FETCH_ATTEMPTS + 1):
        try:
            with urllib.request.urlopen(req, timeout=FETCH_TIMEOUT) as resp:
                return resp.read().decode("utf-8")
        except (socket.timeout, urllib.error.URLError) as err:
            timed_out = isinstance(err, socket.timeout) or isinstance(
                getattr(err, "reason", None), socket.timeout)
            if not timed_out or attempt == FETCH_ATTEMPTS:
                raise
            # A stall is random, not congestion: the endpoint either answers in
            # a second or never answers at all. Backing off would just add dead
            # time to a retry that is about as likely to work immediately.
            time.sleep(FETCH_RETRY_PAUSE)


# Tabs are fetched concurrently, which is what actually pays for the stalls
# above: they are independent draws, so overlapping them turns a queue of
# them into one wait. Measured over the 19 tabs, sequential 120s -> 21s.
# Small on purpose — the point is to overlap the dead time, not to hammer
# Google, and nothing here gets faster with a bigger pool.
FETCH_WORKERS = 6


# ── Source selection ─────────────────────────────────────────────────────────
# Where the tabs come from. Everything downstream consumes {tab: csv_text} and
# cannot tell the difference, which is what makes the Supabase migration a
# source swap rather than a rewrite.
#
#   sheets    the published Google Spreadsheet (the original, still the default
#             until the Supabase cutover is signed off)
#   supabase  the Postgres tables, rebuilt into the identical CSV text by
#             src/source_supabase.py
#
# DATASET_LOCAL_DIR outranks both: a directory of {tab}.csv files builds fully
# offline, which is how the tests and the parity check run.
SOURCE_SHEETS = "sheets"
SOURCE_SUPABASE = "supabase"
SOURCES = (SOURCE_SHEETS, SOURCE_SUPABASE)


def source() -> str:
    value = os.environ.get("DATASET_SOURCE", SOURCE_SHEETS).strip().lower()
    if value not in SOURCES:
        raise DataError(
            f"DATASET_SOURCE {value!r} is not one of {', '.join(SOURCES)}")
    return value


def _fetch_many(tabs: "tuple[str, ...]") -> "dict[str, str]":
    """Fetch tabs, in the order given. First failure propagates."""
    if not os.environ.get("DATASET_LOCAL_DIR") and source() == SOURCE_SUPABASE:
        # Imported here so the default path keeps its import graph, and so a
        # sheets build never touches the Supabase credentials.
        from . import source_supabase
        return source_supabase.fetch_many(tabs)
    # Concurrency is for the network source only: it exists to overlap Google's
    # random stalls (see FETCH_TIMEOUT above), which Postgres does not have.
    with concurrent.futures.ThreadPoolExecutor(FETCH_WORKERS) as pool:
        return dict(zip(tabs, pool.map(fetch_tab, tabs)))


def read_snapshot(directory, tabs=None) -> "dict[str, str] | None":
    """A snapshot directory as {tab: csv_text}, or None if a tab is missing.

    The same rule fetch_tab applies to DATASET_LOCAL_DIR, exposed for callers
    that read a directory directly (the tests, and anything comparing two
    snapshots): a Supabase-only tab absent from a snapshot taken before it
    existed is an EMPTY tab, not a missing one.
    """
    out = {}
    for tab in (tabs or TABS):
        path = os.path.join(directory, f"{tab}.csv")
        if not os.path.exists(path):
            if tab in SUPABASE_ONLY_TABS:
                out[tab] = empty_csv(tab)
                continue
            return None
        with open(path, encoding="utf-8") as fh:
            out[tab] = fh.read()
    return out


def fetch_all() -> "dict[str, str]":
    """Fetch every tab; returns {tab_name: csv_text}."""
    return _fetch_many(TABS)


def fetch_nt_all() -> "dict[str, str]":
    """Fetch the six national-team tabs; returns {tab_name: csv_text}."""
    return _fetch_many(NT_TABS)


# ── Enums (as built) ─────────────────────────────────────────────────────────

MATCH_STATUSES = frozenset(
    {"scheduled", "played", "postponed", "abandoned", "awarded", "cancelled"}
)
SOURCE_TYPES = frozenset(
    {"reporter", "rfa", "fa", "club", "facebook", "newspaper", "whatsapp",
     "backfill", "placeholder", "unknown"}
)
CONFIDENCES = frozenset({"unconfirmed", "confirmed", "official"})
# officials.kind (0024). One pool for all four match-official roles, because
# the same person referees one match and runs the line at the next; `coach` is
# the other kind of person on a team-sheet graphic.
OFFICIAL_KINDS = frozenset({"referee", "coach"})
# trending.status (0030). Only `live` renders; `archived` is off the site and
# still findable in the portal, which is the whole reason it is not a delete.
TRENDING_STATUSES = frozenset({"draft", "live", "archived"})
# matches.stage vocabulary for knockout (type=cup) competitions. League rows
# use free-form md_<n> stages instead; presentation order lives in adapt.
# Two-legged ties carry no leg column: a reversed fixture between the same
# two teams within one stage IS the second leg (see adapt.TieView).
KNOCKOUT_STAGES = frozenset({"r64", "r32", "r16", "qf", "sf", "final", "3p"})
# "" = ordinary goal with no recorded type; the sheet leaves the cell blank.
GOAL_TYPES = frozenset({"", "open_play", "penalty", "free_kick", "header", "own_goal"})
GENDERS = frozenset({"m", "w"})
AGE_GROUPS = frozenset({
    "senior", "u20", "u19", "u17", "u16", "u15", "u14", "u13", "u12", "u11", "u10",
})
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
    # Knockout-only columns (validate.py rejects them on league rows). The
    # goals columns are the full-time-of-record score — after extra time when
    # extra_time is set; the shootout lives only in the pens columns and its
    # kicks are never rows in the goals tab.
    extra_time: bool = False
    home_pens: "int | None" = None
    away_pens: "int | None" = None
    # Officials (0023). Free text as reported, blank on almost every row, and
    # rendered only where present — this site does not track referees or
    # coaches as entities, so there is nothing to resolve them against. The
    # coach is on the MATCH rather than on the team: clubs change coach, and a
    # column on teams would rewrite last season's team sheet when they do.
    referee: str = ""
    assistant_referee_1: str = ""
    assistant_referee_2: str = ""
    fourth_official: str = ""
    home_coach: str = ""
    away_coach: str = ""
    # Who those names turned out to be (0024). Blank is the normal state and
    # means "nobody has resolved that name yet" — exactly what a blank
    # lineups.player_id means, and rendered the same way: plain text, no link,
    # no page. 0023's comment above said this site does not track referees as
    # entities; it does now, and these are how.
    referee_id: str = ""
    assistant_referee_1_id: str = ""
    assistant_referee_2_id: str = ""
    fourth_official_id: str = ""
    home_coach_id: str = ""
    away_coach_id: str = ""

    @property
    def has_officials(self) -> bool:
        """Is there anything to render? Blank is the answer on most matches."""
        return any((self.referee, self.assistant_referee_1,
                    self.assistant_referee_2, self.fourth_official,
                    self.home_coach, self.away_coach))

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
    # What a reporter typed, when EveryLeague had no canonical player to point
    # at. Paired with player_id == UNKNOWN_PLAYER_ID it is the ONLY name this
    # goal has, and adapt.py shows it on the match line rather than dropping
    # the scorer entirely. Kept after reconciliation as provenance, so a
    # non-blank value here does not imply an unidentified scorer — read
    # player_id for that. Defaulted because the historic rows predate the
    # column and the national-team tabs never had it.
    reported_player_name: str = ""

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


@dataclass(frozen=True)
class LineupRow:
    """One named player on one side's team sheet for one match.

    Deliberately the same shape as nt.NTLineupRow, so src/lineups.py can fold
    and render both. Two columns differ in meaning rather than in name:
    `team_id` here is always a real teams row (a league match has two), and
    `player_id` is a canonical players id or "" when nobody has identified them
    yet — the same state an unresolved scorer sits in.
    """
    match_id: str
    team_id: str
    player_name: str      # as reported
    player_id: str        # "" when not identified
    shirt_number: str
    position: str         # GK | DF | MF | FW, or ""
    role: str             # starting | sub_on | unused_sub
    captain: bool
    minute_on: str
    minute_off: str
    replaced_player: str  # a NAME — a team sheet is written and read by name
    yellow_card: bool
    yellow_red_card: bool
    red_card: bool
    # 0028. One per MATCH, not one per side — the armband above it is the
    # other way round. Defaulted rather than positional because every row
    # written before 0028 has no such column and reads as false, which is
    # exactly what "nobody recorded one" should look like.
    motm: bool = False
    # What this player did in THIS match, joined on by src/adapt.py from the
    # goals tab (see lineups.with_goals). Not a column on the tab and never
    # parsed from one: a goal is a row in `goals` and this is a count of them,
    # cached on the sheet's row so the markup can put a ball beside the name
    # without the renderer holding the whole goals tab. Zero is the answer for
    # everyone on almost every sheet.
    goals: int = 0
    own_goals: int = 0
    assists: int = 0

    @property
    def shirt_sort(self) -> "tuple[int, int, str]":
        return lineups.id_sort(self.shirt_number)


@dataclass(frozen=True)
class Official:
    """A referee or a coach, as a person rather than as a string (0024).

    One registry for both, because every operation on them is the same; `kind`
    (referee | coach) is what keeps a coach out of a referee picker. The four
    match-official roles share the `referee` kind — the same person referees
    one match and runs the line at the next.
    """
    official_id: str
    full_name: str
    known_as: str
    kind: str             # referee | coach
    status: str

    @property
    def display_name(self) -> str:
        return self.known_as or self.full_name or self.official_id


@dataclass(frozen=True)
class TrendingCard:
    """One homepage carousel card (0030) — the lite CMS behind the front page.

    Everything on it is optional except the headline, and every omission
    degrades rather than breaks: no image renders text-only, no link renders a
    card that is not a link, no eyebrow renders no label. That is the same rule
    the rest of the site follows, applied to editorial copy.

    `status` is what the build reads and nothing else: only `live` renders.
    Drafts and the archive are parsed and carried so the snapshot is a complete
    record of what has ever been on the homepage.
    """
    card_id: str
    status: str           # draft | live | archived
    eyebrow: str
    headline: str
    body: str
    link_url: str
    link_label: str
    image_path: str       # object name in the trending-media bucket, or ""
    image_alt: str
    sort_order: int
    published_at: str

    @property
    def is_live(self) -> bool:
        return self.status == "live"

    @property
    def sort_key(self) -> "tuple[int, str]":
        """Carousel order. card_id breaks a tie so the site is deterministic —
        two cards sharing a sort_order must not reorder between builds."""
        return (self.sort_order, self.card_id)


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
    lineups: "list[LineupRow]" = field(default_factory=list)
    officials: "dict[str, Official]" = field(default_factory=dict)
    trending: "dict[str, TrendingCard]" = field(default_factory=dict)

    def live_trending(self) -> "list[TrendingCard]":
        """The homepage carousel, in the order it renders. Empty is normal —
        the landing page then falls back to its hand-written feature card."""
        return sorted((c for c in self.trending.values() if c.is_live),
                      key=lambda c: c.sort_key)

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

    def registry_name(self, player_id: str) -> str:
        """The canonical name for an id, or "" when it resolves to nobody.

        The tolerant counterpart of `player_display_name`, and the callback
        `lineups.with_canonical_names` takes. Three things resolve to nobody
        and all three are ordinary: a blank id (nobody has identified that name
        yet), CAF_MW_UNKNOWN (the reserved row, which has no name to give), and
        an id from another namespace — an opponent's national-team id belongs
        to no registry and never will. Each keeps whatever name was reported.
        """
        if not player_id or player_id == UNKNOWN_PLAYER_ID:
            return ""
        player = self.players.get(player_id)
        return player.display_name if player else ""

    def official_name(self, official_id: str) -> str:
        """The registry name for an official id, or "" when it resolves to nobody.

        The `registry_name` of 0024, and it exists for the same reason: the
        name on a match is the name AS REPORTED ("H. Nkhoma") and the id is who
        that turned out to be, so one rename has to move every page. An id that
        resolves to nothing is ordinary — almost every match names a referee
        nobody has tapped onto a registry row — and keeps its reported name.
        """
        if not official_id:
            return ""
        official = self.officials.get(official_id)
        return official.display_name if official else ""


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


def _stage(value: str) -> str:
    """Normalize matches.stage: the sheet mixes md_1 / matchday_1 / case."""
    v = value.strip().lower()
    if v.startswith("matchday_"):
        v = "md_" + v[len("matchday_"):]
    return v


def _source_type(value, tab, i):
    """Blank source_type is common in the sheet; it means 'unknown'."""
    return _enum(value or "unknown", SOURCE_TYPES, "source_type", tab, i)


def _flag(value: str, label: str, tab: str, i: int) -> bool:
    """A Sheets checkbox column: blank | 0/1 | FALSE/TRUE."""
    v = value.strip().lower()
    if v not in ("", "0", "1", "false", "true"):
        raise DataError(
            f"{tab} row {i}: {label} {value!r} must be blank, 0/1 or TRUE/FALSE")
    return v in ("1", "true")


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
        # extra_time / home_pens / away_pens are newer optional columns: the
        # header may be absent entirely (older snapshots), which reads the
        # same as every cell blank. TRUE/FALSE is how a Sheets checkbox
        # column exports, so it is accepted alongside 0/1.
        et = r.get("extra_time", "").lower()
        if et not in ("", "0", "1", "false", "true"):
            raise DataError(
                f"matches row {i}: extra_time {et!r} must be blank, 0/1 or "
                f"TRUE/FALSE")
        hp = _opt_int(r.get("home_pens", ""), "home_pens", "matches", i)
        ap = _opt_int(r.get("away_pens", ""), "away_pens", "matches", i)
        for label, p in (("home_pens", hp), ("away_pens", ap)):
            if p is not None and p < 0:
                raise DataError(f"matches row {i}: {label} cannot be negative ({p})")
        _put(out, mid, Match(
            mid, _require(r, "competition_id", "matches", i),
            _require(r, "season_id", "matches", i),
            _stage(r.get("stage", "")),
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
            extra_time=(et in ("1", "true")), home_pens=hp, away_pens=ap,
            # Officials, all optional and all newer than the last snapshot —
            # same rule as extra_time/pens above: an absent header reads as
            # every cell blank rather than as a parse error.
            referee=r.get("referee", ""),
            assistant_referee_1=r.get("assistant_referee_1", ""),
            assistant_referee_2=r.get("assistant_referee_2", ""),
            fourth_official=r.get("fourth_official", ""),
            home_coach=r.get("home_coach", ""),
            away_coach=r.get("away_coach", ""),
            referee_id=r.get("referee_id", ""),
            assistant_referee_1_id=r.get("assistant_referee_1_id", ""),
            assistant_referee_2_id=r.get("assistant_referee_2_id", ""),
            fourth_official_id=r.get("fourth_official_id", ""),
            home_coach_id=r.get("home_coach_id", ""),
            away_coach_id=r.get("away_coach_id", ""),
        ), "matches", i)
    return out


def parse_goals(text: str) -> "dict[str, Goal]":
    out: "dict[str, Goal]" = {}
    # player_name is intentionally absent: the denormalized column is ignored;
    # names come from player_id -> players. reported_player_name is NOT the
    # same column and is read below — it is what a reporter typed for a scorer
    # with no canonical id, and the only name such a goal has. It stays out of
    # `required` so a snapshot taken before the column existed still parses.
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
            r.get("reported_player_name", ""),
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


def parse_officials(text: str) -> "dict[str, Official]":
    """The officials registry (0024). Absent or empty on an older snapshot."""
    out: "dict[str, Official]" = {}
    required = {"official_id", "full_name", "kind"}
    for i, r in _rows(text, "officials", required):
        oid = _require(r, "official_id", "officials", i)
        _put(out, oid, Official(
            oid, r.get("full_name", ""), r.get("known_as", ""),
            _enum(_require(r, "kind", "officials", i), OFFICIAL_KINDS,
                  "kind", "officials", i),
            r.get("status", ""),
        ), "officials", i)
    return out


def parse_trending(text: str) -> "dict[str, TrendingCard]":
    """The homepage carousel tab (0030). Absent or empty on an older snapshot.

    Only `card_id`, `status` and `headline` are required — a card is mostly
    optional parts, and the renderer drops each missing one rather than
    substituting anything for it.
    """
    out: "dict[str, TrendingCard]" = {}
    required = {"card_id", "status", "headline"}
    for i, r in _rows(text, "trending", required):
        cid = _require(r, "card_id", "trending", i)
        _put(out, cid, TrendingCard(
            cid,
            _enum(_require(r, "status", "trending", i), TRENDING_STATUSES,
                  "status", "trending", i),
            r.get("eyebrow", ""),
            _require(r, "headline", "trending", i),
            r.get("body", ""), r.get("link_url", ""), r.get("link_label", ""),
            r.get("image_path", ""), r.get("image_alt", ""),
            # Blank sorts first, which is where a card written before this
            # column meant anything belongs.
            _int(r.get("sort_order", "") or "0", "sort_order", "trending", i),
            r.get("published_at", ""),
        ), "trending", i)
    return out


def parse_lineups(text: str) -> "list[LineupRow]":
    """The league team-sheet tab. A list, not a dict: the key is three columns
    and every reader wants the rows grouped rather than looked up one by one."""
    out: "list[LineupRow]" = []
    required = {"match_id", "team_id", "player_name", "role"}
    for i, r in _rows(text, "lineups", required):
        out.append(LineupRow(
            _require(r, "match_id", "lineups", i),
            _require(r, "team_id", "lineups", i),
            _require(r, "player_name", "lineups", i),
            r.get("player_id", ""), r.get("shirt_number", ""),
            # Position is optional here where it is required on the NT tab: a
            # league sheet often arrives as eleven names off a Facebook photo
            # with no positions at all, and half a team sheet still reads.
            _enum(r.get("position", ""), lineups.POSITION_SET | {""},
                  "position", "lineups", i).upper(),
            _enum(_require(r, "role", "lineups", i), lineups.ROLES,
                  "role", "lineups", i),
            _flag(r.get("captain", ""), "captain", "lineups", i),
            r.get("minute_on", ""), r.get("minute_off", ""),
            r.get("replaced_player", ""),
            _flag(r.get("yellow_card", ""), "yellow_card", "lineups", i),
            _flag(r.get("yellow_red_card", ""), "yellow_red_card", "lineups", i),
            _flag(r.get("red_card", ""), "red_card", "lineups", i),
            motm=_flag(r.get("motm", ""), "motm", "lineups", i),
        ))
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
    "lineups": parse_lineups,
    "officials": parse_officials,
    "trending": parse_trending,
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
