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
                "home_pens", "away_pens",
                # 0023. Free text, as reported, and blank on almost every row.
                # dataset.parse_matches treats an absent header as every cell
                # blank, so an older snapshot keeps parsing without them.
                "referee", "assistant_referee_1", "assistant_referee_2",
                "fourth_official", "home_coach", "away_coach",
                # 0024. Who those six names turned out to be. NULL — the normal
                # state — renders as a blank cell and reads as "not resolved".
                #
                # `notes` (0025) is deliberately NOT here. It is a reporter's
                # working note about people and this snapshot is committed to a
                # public repository; see that migration's header.
                "referee_id", "assistant_referee_1_id",
                "assistant_referee_2_id", "fourth_official_id",
                "home_coach_id", "away_coach_id"),
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
    # 0018. The one tab with no counterpart in the spreadsheet — it postdates
    # it — so dataset.SUPABASE_ONLY_TABS holds the same header for the sheets
    # fallback to emit empty. Keep the two in step.
    # 0028 added motm, next to captain because it is the same kind of thing:
    # a flag on one name on the sheet.
    "lineups": ("match_id", "team_id", "player_name", "player_id",
                "shirt_number", "position", "role", "captain", "motm",
                "minute_on", "minute_off", "replaced_player", "yellow_card",
                "yellow_red_card", "red_card"),
    # 0024. The referee/coach registry, alongside players.
    "officials": ("official_id", "full_name", "known_as", "kind", "status"),
    # 0030. The homepage cards. `created_by` and the timestamps stay out for
    # the same reason matches.notes does: this snapshot is committed to a
    # public repository and neither is football data. `published_at` IS here —
    # it is the archive's own date and answers "what was up that weekend".
    "trending": ("card_id", "status", "eyebrow", "headline", "body",
                 "link_url", "link_label", "image_path", "image_alt",
                 "image_credit", "sort_order", "published_at"),
    "reporters": ("reporter_id", "name", "email", "affiliation",
                  "affiliation_id", "region", "active", "public_byline"),
    "aliases": ("alias_text", "entity_type", "entity_id", "context"),

    "nt_teams": ("team_code", "team_name", "category"),
    "nt_matches": ("match_id", "team_code", "date", "competition", "opponent",
                   "home_away", "neutral", "venue", "kickoff", "city",
                   "country", "team_score", "opponent_score", "status", "coach",
                   "extra_time", "penalty_shootout", "extra_time_result",
                   # 0033, mirroring matches' 0008/0023/0024 columns exactly.
                   # `notes` is deliberately NOT here, for the same reason
                   # matches.notes (0025) is not: a reporter's private note
                   # about people has no business in a public git history.
                   "source_ref", "referee", "assistant_referee_1",
                   "assistant_referee_2", "fourth_official", "opponent_coach",
                   "referee_id", "assistant_referee_1_id",
                   "assistant_referee_2_id", "fourth_official_id", "coach_id",
                   "opponent_coach_id"),
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
                   "shirt_number", "position", "role", "captain", "motm",
                   "minute_on", "minute_off", "replaced_player", "yellow_card",
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


# Tabs with no public read policy. Reading one with the publishable key does
# not fail — RLS *filters*, so the answer is an empty list — which would put an
# empty tab into the snapshot and look exactly like every reporter having been
# deleted. Requiring the secret key turns that silent loss into a loud stop.
SECRET_ONLY = frozenset({"reporters"})


def fetch_tab(tab: str) -> str:
    """One tab, read from Postgres in `ord` order and rendered as CSV."""
    if tab not in COLUMNS:
        raise sb.SupabaseError(f"unknown tab {tab!r}")
    rows = sb.select(tab, order="ord.asc",
                     require_secret=tab in SECRET_ONLY)
    return tab_csv(tab, rows)


def fetch_many(tabs) -> "dict[str, str]":
    return {tab: fetch_tab(tab) for tab in tabs}
