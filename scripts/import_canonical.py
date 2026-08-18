#!/usr/bin/env python3
"""One-time import of data/canonical/ into Supabase. Idempotent — re-runnable.

The canonical snapshot is the cleanest representation of the data in the repo:
it is the last fetch that passed every validator check, and git history of it
is the existing audit log. So it, not the live spreadsheet, is what seeds
Postgres.

What this does NOT do: invent ids, reshape the model, or "clean up" values.
Every row keeps its canonical id and its column values, with three kinds of
change only, each one something dataset.py already does at parse time:

  * typing        — blank -> NULL for integer/date/timestamp columns, and
                    0/1/TRUE/FALSE -> boolean;
  * normalization — the lowercasing and blank-defaulting the parsers apply
                    (source_type '' -> 'unknown', entries.status '' -> 'active',
                    stage 'matchday_1' -> 'md_1');
  * ordering      — each row's position in the CSV is written to `ord`, which
                    is what lets the site rebuild in the same order the sheet
                    produced. See the header of 0001_core_schema.sql.

Because the transform is only what the parser already did, a round trip
through Postgres must yield an identical Dataset — which scripts/parity.py
then proves on the rendered HTML.

Usage:
    python3 scripts/import_canonical.py [--dir data/canonical] [--dry-run]
                                        [--only TABLE[,TABLE...]]

Requires SUPABASE_URL and SUPABASE_SECRET_KEY (read from .env if present).
The secret key bypasses RLS and must never leave a server.
"""

import argparse
import csv
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from src import dataset, supabase_client as sb  # noqa: E402


class ImportError_(Exception):
    pass


# ── Column typing ────────────────────────────────────────────────────────────
# Anything not named here is text, and a blank cell stays '' — the Dataset
# reads a blank cell as "", so '' and NULL must not both be able to mean it.
# `nullable` is therefore only for FK columns, where NULL is the honest way to
# say "no venue", "no assist", "not reported by anyone".

class Spec:
    def __init__(self, table, pk, *, ints=(), dates=(), stamps=(), bools=(),
                 nullable=(), lower=(), defaults=None, zero_default=()):
        self.table = table
        self.pk = pk                    # on_conflict target
        self.ints = set(ints)
        self.dates = set(dates)
        self.stamps = set(stamps)       # timestamptz
        self.bools = set(bools)
        self.nullable = set(nullable)   # '' -> NULL (FK columns)
        self.lower = set(lower)
        self.defaults = defaults or {}  # column -> value when the cell is blank
        self.zero_default = set(zero_default)  # blank integer -> 0, not NULL


# Import order is FK order. matches comes after entries because of the
# composite FK that enforces "both participants are entered in this
# competition+season" (validate.py check 3).
SPECS = [
    Spec("clubs", "club_id", nullable=("successor_club_id",)),
    Spec("teams", "team_id", ints=("squad_level",),
         lower=("gender", "age_group")),
    Spec("competitions", "competition_id", ints=("tier",),
         lower=("country", "type", "gender", "age_group")),
    Spec("seasons", "season_id", dates=("start_date", "end_date"),
         lower=("status",)),
    Spec("competition_seasons", "competition_id,season_id",
         ints=("teams_count", "promotion_places", "relegation_places",
               "points_win", "points_draw")),
    Spec("entries", "entry_id", ints=("points_adjustment",),
         zero_default=("points_adjustment",), lower=("status",),
         defaults={"status": "active"}),
    Spec("venues", "venue_id"),
    Spec("players", "player_id", dates=("dob",)),
    Spec("reporters", "reporter_id"),
    Spec("matches", "match_id",
         ints=("matchday", "home_goals", "away_goals", "home_pens", "away_pens"),
         dates=("date",), stamps=("reported_at", "verified_at"),
         bools=("extra_time",),
         nullable=("venue_id", "reported_by", "verified_by"),
         lower=("status", "source_type", "confidence"),
         defaults={"source_type": "unknown", "confidence": "unconfirmed"}),
    Spec("goals", "goal_id", stamps=("reported_at", "verified_at"),
         nullable=("assist_player_id", "reported_by", "verified_by"),
         lower=("goal_type", "source_type", "confidence"),
         defaults={"source_type": "unknown", "confidence": "unconfirmed"}),
    Spec("registrations", "player_id,team_id,season_id",
         dates=("from_date", "to_date")),
    # After matches, teams and players: it references all three. player_id is
    # nullable because blank means "nobody has identified them yet" — the same
    # state goals.assist_player_id sits in.
    Spec("lineups", "match_id,team_id,player_name",
         bools=("captain", "yellow_card", "yellow_red_card", "red_card"),
         nullable=("player_id", "reported_by"), lower=("role",)),
    # No natural key: a duplicate alias_text is legal, so rows are replaced
    # wholesale rather than upserted (see _load).
    Spec("aliases", None),

    # ── National teams: a separate schema, imported the same way ────────────
    Spec("nt_teams", "team_code"),
    Spec("nt_matches", "match_id",
         ints=("team_score", "opponent_score"),
         bools=("neutral", "extra_time"), lower=("status",)),
    Spec("nt_goals", "goal_id", lower=("goal_type",)),
    Spec("nt_squads", "squad_id,player_name"),
    Spec("nt_competitions", "team_code,competition_name"),
    Spec("nt_lineups", "match_id,player_name",
         bools=("captain", "yellow_card", "yellow_red_card", "red_card"),
         lower=("role",)),
    Spec("nt_knockout", "tie_id",
         ints=("slot", "home_score", "away_score", "home_pens", "away_pens"),
         zero_default=("slot",), bools=("extra_time",),
         nullable=("nt_match_id",), lower=("stage", "status"),
         defaults={"status": "scheduled"}),
]

SPEC_BY_TABLE = {s.table: s for s in SPECS}

TRUE_VALUES = ("1", "true")
BOOL_VALUES = ("", "0", "1", "false", "true")


def _cell(spec, column, raw, where, artifacts):
    """One CSV cell -> the JSON value PostgREST should receive."""
    value = (raw or "").strip()
    # A cell still holding a spreadsheet formula is an editing artifact, not
    # data: an =AI(...) prompt got dragged down a column and published
    # unevaluated. Nothing in this dataset legitimately starts with '=', and
    # nothing in the build ever read the affected column, so it has sat there
    # invisibly. Import it as blank and report every one — the original text
    # stays in git under data/canonical/ if it is ever wanted.
    if value.startswith("="):
        artifacts.append((where, column, value))
        value = ""
    if column in spec.lower:
        value = value.lower()
    if not value and column in spec.defaults:
        value = spec.defaults[column]

    if column in spec.bools:
        if value.lower() not in BOOL_VALUES:
            raise ImportError_(
                f"{where}: {column} {raw!r} must be blank, 0/1 or TRUE/FALSE")
        return value.lower() in TRUE_VALUES
    if column in spec.ints:
        if not value:
            return 0 if column in spec.zero_default else None
        try:
            return int(value)
        except ValueError:
            raise ImportError_(
                f"{where}: {column} {raw!r} is not an integer") from None
    if column in spec.dates or column in spec.stamps:
        # Format is the parser's business (strict YYYY-MM-DD); Postgres will
        # reject anything else loudly, which is the behaviour we want.
        return value or None
    if column in spec.nullable:
        return value or None
    return value


def read_rows(path, spec, artifacts):
    """Non-blank CSV rows as PostgREST payloads, `ord` set from row position.

    Blank rows are skipped exactly as dataset._rows skips them: the sheet
    carries hundreds of empty trailing rows (865 in matches.csv), which are a
    Sheets artifact and not data.
    """
    if not os.path.exists(path):
        raise ImportError_(f"missing {path}")
    with open(path, encoding="utf-8") as fh:
        reader = csv.DictReader(fh)
        columns = [(c or "").strip() for c in (reader.fieldnames or []) if (c or "").strip()]
        out = []
        for line, raw in enumerate(reader, start=2):
            row = {(k or "").strip(): (v or "").strip()
                   for k, v in raw.items() if k}
            if not any(row.values()):
                continue
            where = f"{spec.table} line {line}"
            payload = {c: _cell(spec, c, row.get(c, ""), where, artifacts)
                       for c in columns}
            # matches.stage mixes md_1 / matchday_2 / case in the historic
            # sheet; dataset._stage collapses it and the cup rules key off the
            # collapsed form, so the database must hold that form too.
            if spec.table == "matches":
                payload["stage"] = dataset._stage(payload.get("stage", ""))
            payload["ord"] = len(out) + 1
            out.append(payload)
    return out


def _load(spec, rows, *, dry_run):
    if dry_run:
        return len(rows)
    if spec.pk is None:
        # aliases has no natural key. Replacing the table wholesale keeps the
        # import idempotent without inventing one; it is 30 rows.
        sb._request("DELETE", spec.table, query="id=gt.0",
                    headers={"Prefer": "return=minimal"}, require_secret=True)
        return sb.upsert(spec.table, rows)
    return sb.upsert(spec.table, rows, on_conflict=spec.pk)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dir", default=os.path.join(ROOT, "data", "canonical"))
    parser.add_argument("--dry-run", action="store_true",
                        help="parse and report counts without writing")
    parser.add_argument("--only", default="",
                        help="comma-separated table names to import")
    args = parser.parse_args(argv)

    sb.load_dotenv(os.path.join(ROOT, ".env"))
    if not args.dry_run:
        sb.key(require_secret=True)   # fail now, not after parsing everything

    only = {t.strip() for t in args.only.split(",") if t.strip()}
    unknown = only - set(SPEC_BY_TABLE)
    if unknown:
        print(f"ERROR: unknown table(s): {', '.join(sorted(unknown))}",
              file=sys.stderr)
        return 1
    specs = [s for s in SPECS if not only or s.table in only]

    # Parse everything before writing anything: a typing error in the last
    # table should not leave the database half-loaded.
    parsed = []
    artifacts = []
    try:
        for spec in specs:
            rows = read_rows(os.path.join(args.dir, f"{spec.table}.csv"), spec,
                             artifacts)
            parsed.append((spec, rows))
    except ImportError_ as err:
        print(f"ERROR: {err}", file=sys.stderr)
        return 1

    if artifacts:
        print(f"NOTE: dropped {len(artifacts)} spreadsheet formula(s) left in "
              f"data cells; imported as blank:", file=sys.stderr)
        for where, column, value in artifacts:
            print(f"  {where}: {column} = {value[:60]}...", file=sys.stderr)

    total = 0
    for spec, rows in parsed:
        try:
            n = _load(spec, rows, dry_run=args.dry_run)
        except sb.SupabaseError as err:
            print(f"ERROR: {spec.table}: {err}", file=sys.stderr)
            return 1
        total += n
        print(f"  {spec.table:20} {n:5} rows")

    verb = "would import" if args.dry_run else "imported"
    print(f"{verb} {total} rows across {len(parsed)} tables")
    return 0


if __name__ == "__main__":
    sys.exit(main())
