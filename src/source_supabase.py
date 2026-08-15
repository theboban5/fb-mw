"""Supabase -> the same ``{tab: csv_text}`` dict the spreadsheet produced.

The whole migration hinges on this being a *source swap* and nothing more.
Everything downstream of ``dataset.fetch_all()`` — the validator, the parsers,
the snapshot writer, ``search.index_version`` — consumes raw CSV text, so
rebuilding that text from Postgres leaves validate.py, adapt.py, standings.py,
render.py, hubs.py, matches_page.py, nt.py and nt_page.py untouched. The site
cannot change appearance, because nothing that draws it can tell the
difference.

Two rules make the reconstruction faithful:

  * **Row order comes from ``ord``**, never from whatever order Postgres feels
    like returning. src/adapt.py numbers MatchView.row by position and
    standings.py sorts by it, so a reshuffle here would silently reorder
    fixtures on the rendered pages.

  * **NULL renders as an empty cell.** The Dataset reads a blank cell as "",
    which is a meaningful value throughout (no venue, no kickoff, unscheduled
    fixture). Only FK columns are nullable in Postgres, so this is a narrow,
    explicit conversion rather than a guess.

Column lists are declared here rather than read from data/canonical/, so that
nothing in the build path depends on the old snapshot once the source is
switched.
"""

import csv
import io

from . import supabase_client as sb

# Exactly the tab headers the spreadsheet published, in the same order, with
# two deliberate additions marked below. Order matters only for the bytes of
# the snapshot and the search-index hash — the parsers key off names — but
# holding it steady keeps a snapshot diff readable.
COLUMNS = {
    "clubs": ("club_id", "name", "short_name", "city", "region", "founded",
              "crest", "status", "successor_club_id", "notes"),
    "teams": ("team_id", "club_id", "gender", "age_group", "squad_level",
              "display_name", "legacy_code", "status"),
    "competitions": ("competition_id", "country", "name", "type", "tier",
                     "gender", "age_group", "region", "governing_body", "logo"),
    "seasons": ("season_id", "country", "label", "start_date", "end_date",
                "status"),
    "competition_seasons": ("competition_id", "season_id", "sponsor_name",
                            "format", "teams_count", "promotion_places",
                            "relegation_places", "points_win", "points_draw",
                            "status"),
    "entries": ("entry_id", "competition_id", "season_id", "team_id", "group",
                "points_adjustment", "adjustment_reason", "status"),
    "venues": ("venue_id", "name", "city", "capacity"),
    "matches": ("match_id", "competition_id", "season_id", "stage", "matchday",
                "date", "kickoff", "venue_id", "home_team_id", "away_team_id",
                "home_goals", "away_goals", "status", "awarded_note",
                "source_type", "source_ref", "reported_by", "reported_at",
                "confidence", "verified_by", "verified_at", "extra_time",
                "home_pens", "away_pens"),
    # reported_player_name is new: what a reporter typed when the scorer has no
    # canonical player_id yet. dataset.parse_goals ignores unknown columns, so
    # emitting it now is inert until the match page starts showing it.
    "goals": ("goal_id", "match_id", "team_id", "player_name",
              "reported_player_name", "player_id", "minute", "stoppage",
              "period", "goal_type", "assist_player_id", "source_type",
              "source_ref", "reported_by", "reported_at", "confidence",
              "verified_by", "verified_at"),
    "players": ("player_id", "full_name", "known_as", "dob", "position",
                "nationality", "status"),
    "registrations": ("player_id", "team_id", "season_id", "shirt_number",
                      "from_date", "to_date"),
    "reporters": ("reporter_id", "name", "email", "affiliation",
                  "affiliation_id", "region", "active", "public_byline"),
    "aliases": ("alias_text", "entity_type", "entity_id", "context"),

    "nt_teams": ("team_code", "team_name", "category"),
    "nt_matches": ("match_id", "team_code", "date", "competition", "opponent",
                   "home_away", "neutral", "venue", "kickoff", "city",
                   "country", "team_score", "opponent_score", "status", "coach",
                   "extra_time", "penalty_shootout", "extra_time_result"),
    "nt_goals": ("goal_id", "match_id", "team_id", "player_name", "player_id",
                 "minute", "stoppage", "period", "goal_type",
                 "assist_player_id", "source_type", "source_ref", "reported_by",
                 "reported_at", "confidence", "verified_by", "verified_at"),
    "nt_squads": ("squad_id", "competition", "match_id_range", "team_id",
                  "announcement_date", "competition_context", "player_name",
                  "player_id", "position", "shirt_number", "club",
                  "domestic_team_id", "club_country", "notes", "coach",
                  "source_type", "source_ref", "reported_by", "reported_at",
                  "confidence", "verified_by", "verified_at"),
    "nt_competitions": ("team_code", "competition_name", "group_name",
                        "position", "played", "won", "drawn", "lost", "points",
                        "last_update", "wikipedia_url", "team_name",
                        "goals_for", "goals_against"),
    "nt_lineups": ("match_id", "team_id", "player_name", "player_id",
                   "shirt_number", "position", "role", "captain", "minute_on",
                   "minute_off", "replaced_player", "yellow_card",
                   "yellow_red_card", "red_card"),
    # home_from/away_from post-date the last snapshot but src/nt.py already
    # parses them (they feed a bracket slot from an earlier tie).
    "nt_knockout": ("tie_id", "competition_name", "stage", "slot", "home_name",
                    "away_name", "home_from", "away_from", "home_score",
                    "away_score", "home_pens", "away_pens", "extra_time",
                    "date", "kickoff", "venue", "city", "status",
                    "nt_match_id"),
}

# Booleans render as the sheet's own checkbox vocabulary: "1" for set, blank
# for unset. dataset._flag and the matches parser accept blank | 0/1 |
# TRUE/FALSE, so either would parse — this is the form the columns already
# mostly held.
_TRUE, _FALSE = "1", ""

# Columns emitted as blank regardless of what the database holds.
#
# The snapshot written from these texts lands in data/canonical/ and is
# committed to a public repository — that is the whole point of it, git history
# as the data audit log. Reporter email addresses must not go there. Nothing in
# the build reads the column (Reporter.email is parsed and never displayed);
# the address lives in Postgres and in auth.users, which is where it belongs.
#
# The column itself is kept so the tab's shape does not change.
REDACTED = {("reporters", "email")}


def _cell(value) -> str:
    """One Postgres value as the spreadsheet would have published it."""
    if value is None:
        return ""
    if value is True:
        return _TRUE
    if value is False:
        return _FALSE
    return str(value)


def tab_csv(tab: str, rows: "list[dict]") -> str:
    """Render fetched rows as the CSV text of one tab."""
    columns = COLUMNS[tab]
    buf = io.StringIO()
    # The published CSVs use CRLF, which is what Python's csv writer emits by
    # default. Nothing parses the line ending, but matching it keeps a diff
    # against an older snapshot from showing every line as changed.
    writer = csv.writer(buf)
    writer.writerow(columns)
    for row in rows:
        writer.writerow(["" if (tab, c) in REDACTED else _cell(row.get(c))
                         for c in columns])
    return buf.getvalue()


def fetch_tab(tab: str) -> str:
    """One tab, read from Postgres in `ord` order and rendered as CSV."""
    if tab not in COLUMNS:
        raise sb.SupabaseError(f"unknown tab {tab!r}")
    # `reporters` has no public read policy, so a build that needs it must be
    # holding the secret key. Everything else is readable with the publishable
    # key, which is what makes a local read-only build possible.
    rows = sb.select(tab, order="ord.asc")
    return tab_csv(tab, rows)


def fetch_many(tabs) -> "dict[str, str]":
    return {tab: fetch_tab(tab) for tab in tabs}
