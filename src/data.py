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
        matches.append(Match(i, matchday, date, home, away, hg, ag, stadium))
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
