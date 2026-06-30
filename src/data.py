"""Data layer: fetch, parse and validate the raw CSVs.

Kept deliberately free of any rendering or standings logic so that supporting
more leagues later is just a matter of loading more data, not a rewrite.
"""

from dataclasses import dataclass
from datetime import datetime
import csv
import io
import urllib.request


class DataError(Exception):
    """A problem with the source data that must stop the build loudly."""


@dataclass(frozen=True)
class Team:
    code: str
    name: str
    location: str = ""


@dataclass
class Match:
    row: int           # 1-based row number in the matches sheet (incl. header)
    matchday: int
    date: str          # ISO YYYY-MM-DD
    home_code: str
    away_code: str
    home_goals: "int | None"
    away_goals: "int | None"
    stadium: str = ""  # optional venue name
    match_id: "int | None" = None  # optional join key (only the SL sheet has it)

    @property
    def played(self) -> bool:
        return self.home_goals is not None and self.away_goals is not None


def fetch(source: str) -> str:
    """Return CSV text from an http(s) URL or a local file path."""
    if source.startswith(("http://", "https://")):
        req = urllib.request.Request(source, headers={"User-Agent": "fb-mw-build"})
        with urllib.request.urlopen(req, timeout=30) as resp:
            return resp.read().decode("utf-8")
    with open(source, encoding="utf-8") as fh:
        return fh.read()


def _check_columns(fieldnames, required, sheet):
    have = {(f or "").strip() for f in (fieldnames or [])}
    missing = required - have
    if missing:
        raise DataError(
            f"{sheet} sheet is missing column(s): {', '.join(sorted(missing))}. "
            f"Found: {', '.join(sorted(have)) or '(none)'}"
        )


def parse_teams(text: str) -> "dict[str, Team]":
    reader = csv.DictReader(io.StringIO(text))
    _check_columns(reader.fieldnames, {"code", "name", "location"}, "teams")
    teams: "dict[str, Team]" = {}
    for i, row in enumerate(reader, start=2):
        code = (row.get("code") or "").strip()
        name = (row.get("name") or "").strip()
        location = (row.get("location") or "").strip()
        if not code and not name:
            continue  # blank line
        if not code:
            raise DataError(f"teams row {i}: empty team code")
        if not name:
            raise DataError(f"teams row {i}: team {code!r} has no name")
        if code in teams:
            raise DataError(f"teams row {i}: duplicate team code {code!r}")
        teams[code] = Team(code, name, location)
    if not teams:
        raise DataError("teams sheet has no team rows")
    return teams


def _parse_int(value, label, row):
    text = (value or "").strip()
    try:
        return int(text)
    except ValueError:
        raise DataError(f"matches row {row}: {label} {text!r} is not an integer")


def _parse_goals(value, label, row):
    text = (value or "").strip()
    if text == "":
        return None
    try:
        n = int(text)
    except ValueError:
        raise DataError(f"matches row {row}: {label} {text!r} is not an integer")
    if n < 0:
        raise DataError(f"matches row {row}: {label} cannot be negative ({n})")
    return n


def _validate_date(value, row):
    try:
        datetime.strptime(value, "%Y-%m-%d")
    except ValueError:
        raise DataError(f"matches row {row}: date {value!r} is not ISO YYYY-MM-DD")


def parse_matches(text: str) -> "list[Match]":
    reader = csv.DictReader(io.StringIO(text))
    _check_columns(
        reader.fieldnames,
        {"matchday", "date", "home_code", "away_code", "home_goals", "away_goals"},
        "matches",
    )
    matches: "list[Match]" = []
    for i, row in enumerate(reader, start=2):
        if not any((v or "").strip() for v in row.values()):
            continue  # blank line
        matchday = _parse_int(row.get("matchday"), "matchday", i)
        date = (row.get("date") or "").strip()
        _validate_date(date, i)
        home = (row.get("home_code") or "").strip()
        away = (row.get("away_code") or "").strip()
        if not home or not away:
            raise DataError(f"matches row {i}: missing home_code/away_code")
        if home == away:
            raise DataError(f"matches row {i}: a team cannot play itself ({home!r})")
        hg = _parse_goals(row.get("home_goals"), "home_goals", i)
        ag = _parse_goals(row.get("away_goals"), "away_goals", i)
        if (hg is None) != (ag is None):
            raise DataError(
                f"matches row {i}: only one goal value present "
                f"(home_goals={row.get('home_goals')!r}, away_goals={row.get('away_goals')!r}); "
                f"enter both or leave both blank"
            )
        stadium = (row.get("stadium") or "").strip()
        # match_id is optional: only the Super League sheet carries it (it is the
        # join key for the goals data). Other leagues have no such column, so it
        # stays None and nothing downstream changes for them.
        mid_raw = (row.get("match_id") or "").strip()
        match_id = _parse_int(mid_raw, "match_id", i) if mid_raw else None
        matches.append(Match(i, matchday, date, home, away, hg, ag, stadium, match_id))
    return matches


def validate_match_codes(matches, teams) -> None:
    """The core correctness check: every code in matches must exist in teams.

    Fails loudly, listing every offending row, so a typo'd code can never
    silently produce a wrong table.
    """
    problems = []
    for m in matches:
        for field, code in (("home_code", m.home_code), ("away_code", m.away_code)):
            if code not in teams:
                problems.append((m.row, field, code))
    if not problems:
        return
    lines = [
        "ABORTING: unknown team code(s) in the matches sheet.",
        "These do not match any code in the teams sheet — fix the sheet and rebuild:",
        "",
    ]
    for row, field, code in problems:
        lines.append(f"  matches row {row}: {field} = {code!r}")
    lines += ["", f"Known team codes: {', '.join(sorted(teams))}"]
    raise DataError("\n".join(lines))


# ── Goals (Super League only) ───────────────────────────────────────────────

@dataclass(frozen=True)
class Goal:
    match_id: int
    team_code: str       # the team this goal counted FOR (own goals: the beneficiary)
    player_name: str     # who physically scored (even for own goals)
    minute: str          # raw, e.g. "45" or "45+2" — kept verbatim for display
    goal_type: str = ""  # "" normal, "penalty", or "own goal"

    @property
    def is_own_goal(self) -> bool:
        return self.goal_type == "own goal"

    @property
    def is_penalty(self) -> bool:
        return self.goal_type == "penalty"

    @property
    def minute_sort(self) -> "tuple[int, int]":
        """Sort key that orders injury time correctly: 45 < 45+1 < 45+2 < 46.

        Splits "base+added" into (base, added); a plain minute sorts as added=0.
        Unparseable minutes sort last rather than crashing the build.
        """
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
        """Display label: "Name 45'", "Name 45' (P)" or "Name 45' (OG)".

        The minute is omitted when blank (scorer known, minute not), giving just
        "Name", "Name (P)" or "Name (OG)".
        """
        label = f"{self.player_name} {self.minute}'" if self.minute else self.player_name
        if self.is_penalty:
            label += " (P)"
        elif self.is_own_goal:
            label += " (OG)"
        return label


def parse_goals(text: str) -> "list[Goal]":
    """Parse the goals CSV into Goal records.

    Columns: match_id, team_code, player_name, minute, goal_type. Blank lines are
    skipped; goal_type is normalised (trimmed/lowercased) so stray whitespace in
    the sheet doesn't split "penalty" off into its own bucket.
    """
    reader = csv.DictReader(io.StringIO(text))
    _check_columns(
        reader.fieldnames,
        {"match_id", "team_code", "player_name", "minute", "goal_type"},
        "goals",
    )
    goals: "list[Goal]" = []
    for i, row in enumerate(reader, start=2):
        if not any((v or "").strip() for v in row.values()):
            continue  # blank line
        match_id = _parse_int(row.get("match_id"), "match_id", i)
        team = (row.get("team_code") or "").strip()
        player = (row.get("player_name") or "").strip()
        minute = (row.get("minute") or "").strip()
        gtype = (row.get("goal_type") or "").strip().lower()
        if not team:
            raise DataError(f"goals row {i}: empty team_code")
        # A "scorer not yet found" placeholder: the goal is recorded (match_id +
        # team_code filled in) but the player is still blank in the sheet. We skip
        # it so nothing renders for that goal — the result still shows, just
        # without a scorer line — while the row stays in the sheet as a to-do.
        if not player:
            continue
        # minute may be blank (some scorers are known but not the minute); the
        # annotation simply omits the minute in that case.
        if gtype not in ("", "penalty", "own goal"):
            raise DataError(
                f"goals row {i}: unknown goal_type {gtype!r} "
                f"(expected '', 'penalty' or 'own goal')"
            )
        goals.append(Goal(match_id, team, player, minute, gtype))
    return goals


def validate_goal_links(goals, matches, teams) -> None:
    """Every goal must point at a real match and a team that played in it.

    Fails loudly (listing each bad row) so a typo can never silently drop or
    misattribute a goal.
    """
    by_id = {m.match_id: m for m in matches if m.match_id is not None}
    problems = []
    for g in goals:
        if g.team_code not in teams:
            problems.append(f"  goals: match_id {g.match_id}: unknown team_code {g.team_code!r}")
            continue
        m = by_id.get(g.match_id)
        if m is None:
            problems.append(f"  goals: match_id {g.match_id} matches no row in the matches sheet")
        elif g.team_code not in (m.home_code, m.away_code):
            problems.append(
                f"  goals: match_id {g.match_id}: team {g.team_code!r} did not play "
                f"in that match ({m.home_code} vs {m.away_code})"
            )
    if problems:
        raise DataError(
            "ABORTING: goals data does not line up with matches/teams:\n"
            + "\n".join(problems)
        )
