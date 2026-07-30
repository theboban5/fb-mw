#!/usr/bin/env python3
"""Validate the new-schema data; the first build step.

Fetches all 13 tabs (or reads DATASET_LOCAL_DIR), runs every check, and:
  * any ERROR -> prints them all and exits 1; the build must not proceed and
    production stays untouched;
  * success   -> writes the fetched CSVs to data/canonical/ (the drift
    baseline and, committed by CI, the audit log) and exits 0.

Usage:
    python validate.py [--allow-deletions] [--no-snapshot]

--allow-deletions   skip the drift hard-fail when rows were deliberately
                    removed from the sheet (check 8's escape hatch).
--no-snapshot       validate only; do not update data/canonical/.
"""

import csv
import io
import os
import sys

ROOT = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, ROOT)

from src import dataset  # noqa: E402

CANONICAL_DIR = os.path.join(ROOT, "data", "canonical")

# Tab -> primary key column(s). Aliases has no single-column PK; its rows are
# checked for blank key fields instead (a duplicate alias_text is legal).
PRIMARY_KEYS = {
    "clubs": ("club_id",),
    "teams": ("team_id",),
    "competitions": ("competition_id",),
    "seasons": ("season_id",),
    "competition_seasons": ("competition_id", "season_id"),
    "entries": ("entry_id",),
    "venues": ("venue_id",),
    "matches": ("match_id",),
    "goals": ("goal_id",),
    "players": ("player_id",),
    "registrations": ("player_id", "team_id", "season_id"),
    "reporters": ("reporter_id",),
}

# The identifier columns whose disappearance between fetches means someone
# deleted rows from the sheet (check 8).
DRIFT_IDS = {
    "matches": "match_id",
    "teams": "team_id",
    "clubs": "club_id",
    "players": "player_id",
}


def _non_blank_rows(text):
    """(row_number, {col: stripped}) per non-blank CSV row; header row is 1."""
    reader = csv.DictReader(io.StringIO(text))
    for i, raw in enumerate(reader, start=2):
        row = {(k or "").strip(): (v or "").strip() for k, v in raw.items() if k}
        if any(row.values()):
            yield i, row


def check_primary_keys(texts):
    """Check 1: no duplicate and no blank primary keys, in any tab."""
    errors = []
    for tab, pk in PRIMARY_KEYS.items():
        seen = {}
        for i, row in _non_blank_rows(texts[tab]):
            key = tuple(row.get(c, "") for c in pk)
            if not all(key):
                errors.append(f"{tab} row {i}: blank primary key {'+'.join(pk)}")
                continue
            if key in seen:
                errors.append(
                    f"{tab} row {i}: duplicate primary key "
                    f"{'+'.join(key)} (first seen row {seen[key]})"
                )
            else:
                seen[key] = i
    return errors


def check_foreign_keys(ds):
    """Check 2: every FK resolves to a row in its target tab."""
    errors = []

    def fk(label, key, value, table, blank_ok=False):
        if not value:
            if not blank_ok:
                errors.append(f"{label}: blank {key}")
            return
        if value not in table:
            errors.append(f"{label}: {key} {value!r} does not resolve")

    for t in ds.teams.values():
        fk(f"teams {t.team_id}", "club_id", t.club_id, ds.clubs)
    for m in ds.matches.values():
        lbl = f"matches {m.match_id}"
        fk(lbl, "competition_id", m.competition_id, ds.competitions)
        fk(lbl, "season_id", m.season_id, ds.seasons)
        fk(lbl, "home_team_id", m.home_team_id, ds.teams)
        fk(lbl, "away_team_id", m.away_team_id, ds.teams)
        fk(lbl, "venue_id", m.venue_id, ds.venues, blank_ok=True)
    for g in ds.goals.values():
        lbl = f"goals {g.goal_id}"
        fk(lbl, "match_id", g.match_id, ds.matches)
        fk(lbl, "team_id", g.team_id, ds.teams)
        fk(lbl, "player_id", g.player_id, ds.players)
        fk(lbl, "assist_player_id", g.assist_player_id, ds.players, blank_ok=True)
    for e in ds.entries.values():
        lbl = f"entries {e.entry_id}"
        fk(lbl, "competition_id", e.competition_id, ds.competitions)
        fk(lbl, "season_id", e.season_id, ds.seasons)
        fk(lbl, "team_id", e.team_id, ds.teams)
    for cs in ds.competition_seasons.values():
        lbl = f"competition_seasons {cs.competition_id}+{cs.season_id}"
        fk(lbl, "competition_id", cs.competition_id, ds.competitions)
        fk(lbl, "season_id", cs.season_id, ds.seasons)
    return errors


def check_match_entries(ds):
    """Check 3: both participants of every match are entered in that
    competition+season."""
    entered = {(e.competition_id, e.season_id, e.team_id) for e in ds.entries.values()}
    errors = []
    for m in ds.matches.values():
        for side, tid in (("home", m.home_team_id), ("away", m.away_team_id)):
            if tid and (m.competition_id, m.season_id, tid) not in entered:
                errors.append(
                    f"matches {m.match_id}: {side} team {tid!r} has no entries row "
                    f"for {m.competition_id}+{m.season_id}"
                )
    return errors


def check_match_consistency(ds):
    """Check 4: no self-play; score presence agrees with status.

    played and awarded matches must carry a full score (awarded results count
    into standings with their recorded score); scheduled must carry none; a
    one-sided score is always wrong.
    """
    errors = []
    for m in ds.matches.values():
        lbl = f"matches {m.match_id}"
        if m.home_team_id and m.home_team_id == m.away_team_id:
            errors.append(f"{lbl}: home_team_id == away_team_id ({m.home_team_id!r})")
        if (m.home_goals is None) != (m.away_goals is None):
            errors.append(f"{lbl}: only one of home_goals/away_goals present")
        elif m.status in ("played", "awarded") and not m.has_score:
            errors.append(f"{lbl}: status={m.status} but goals are blank")
        elif m.status == "scheduled" and m.has_score:
            errors.append(f"{lbl}: status=scheduled but goals are present")
    return errors


def check_goal_counts(ds):
    """Check 5: goal rows per match+side never exceed that side's score.

    Fewer is fine (incomplete scorer data is expected); more is a hard error.
    Own-goal rows already credit the benefiting team, so counting by team_id
    lines up with the score. Also catches a goal credited to a team that
    did not play in the match.
    """
    per_side = {}
    errors = []
    for g in ds.goals.values():
        m = ds.matches.get(g.match_id)
        if m is None:
            continue  # unresolvable match_id already reported by check 2
        if g.team_id not in (m.home_team_id, m.away_team_id):
            errors.append(
                f"goals {g.goal_id}: team {g.team_id!r} did not play in "
                f"{m.match_id} ({m.home_team_id} vs {m.away_team_id})"
            )
            continue
        per_side[(m.match_id, g.team_id)] = per_side.get((m.match_id, g.team_id), 0) + 1
    for (mid, tid), n in sorted(per_side.items()):
        m = ds.matches[mid]
        if not m.has_score:
            if n:
                errors.append(f"matches {mid}: has {n} goal row(s) but no score")
            continue
        score = m.home_goals if tid == m.home_team_id else m.away_goals
        if n > score:
            errors.append(
                f"matches {mid}: {n} goal rows for {tid} exceed its score of {score}"
            )
    return errors


def check_dates(ds):
    """Check 6: match dates fall inside their season's date range.

    (Format is already enforced at parse time — strict YYYY-MM-DD.)
    """
    errors = []
    for m in ds.matches.values():
        if not m.date:
            continue
        season = ds.seasons.get(m.season_id)
        if season is None:
            continue  # unresolvable season_id already reported by check 2
        if not (season.start_date <= m.date <= season.end_date):
            errors.append(
                f"matches {m.match_id}: date {m.date} outside season "
                f"{season.season_id} ({season.start_date}..{season.end_date})"
            )
    return errors


def check_cup_rules(ds):
    """Check 7: knockout columns and stages are used coherently.

    A shootout exists only to break a level tie: on a single-leg tie the
    match itself must be level; on the deciding leg of a two-legged tie the
    AGGREGATE must be level (the leg itself may be decisive). Pens on a
    non-deciding leg are always wrong, and pens can never themselves be
    level. Shootout kicks are not goals rows (check 5 would reject them as
    exceeding the score), so pens live only in these columns.
    extra_time/pens are meaningless outside a cup. Cup stages must come from
    the knockout vocabulary; league stages stay free-form — the historic
    sheet mixes md_1/matchday_2 and breaking it retroactively helps nobody.
    """
    errors = []

    # Pair mirrored legs per (competition, season, stage) — the same rule
    # adapt.cup_rounds uses: a reversed fixture between the same two teams
    # is a second leg; a repeat with the same home team is a replay.
    pair_of = {}   # match_id -> [leg1, leg2] in leg order
    open_tie = {}
    seq = {mid: n for n, mid in enumerate(ds.matches)}
    for m in sorted(ds.matches.values(), key=lambda m: (m.date, seq[m.match_id])):
        comp = ds.competitions.get(m.competition_id)
        if comp is None or comp.type != "cup" or m.is_placeholder:
            continue
        key = (m.competition_id, m.season_id, m.stage,
               frozenset((m.home_team_id, m.away_team_id)))
        first = open_tie.get(key)
        if first is not None and m.home_team_id == first.away_team_id:
            legs = [first, m]
            pair_of[first.match_id] = legs
            pair_of[m.match_id] = legs
            del open_tie[key]
        else:
            open_tie[key] = m

    def aggregate_level(m, legs):
        """Both legs scored and the tie is level on aggregate."""
        if not all(leg.has_score for leg in legs):
            return False
        first, second = legs
        a = first.home_goals + second.away_goals
        b = first.away_goals + second.home_goals
        return a == b

    for m in ds.matches.values():
        lbl = f"matches {m.match_id}"
        comp = ds.competitions.get(m.competition_id)
        is_cup = comp is not None and comp.type == "cup"
        if (m.home_pens is None) != (m.away_pens is None):
            errors.append(f"{lbl}: only one of home_pens/away_pens present")
        has_pens = m.home_pens is not None and m.away_pens is not None
        if (has_pens or m.home_pens is not None or m.away_pens is not None
                or m.extra_time) and not is_cup:
            errors.append(
                f"{lbl}: extra_time/pens on {m.competition_id!r}, "
                f"which is not a cup"
            )
        if has_pens:
            legs = pair_of.get(m.match_id)
            if legs is not None and m is not legs[-1]:
                errors.append(
                    f"{lbl}: pens on a non-deciding leg (the mirrored leg "
                    f"{legs[-1].match_id} is later)"
                )
            elif not m.has_score:
                errors.append(f"{lbl}: pens present but no score")
            elif m.home_goals != m.away_goals and not (
                    legs is not None and aggregate_level(m, legs)):
                errors.append(
                    f"{lbl}: pens present but score {m.home_goals}:{m.away_goals} "
                    f"is not level"
                    + ("" if legs is None else " and neither is the aggregate")
                )
            if m.home_pens == m.away_pens:
                errors.append(
                    f"{lbl}: home_pens == away_pens ({m.home_pens}) — "
                    f"a shootout has a winner"
                )
        if is_cup and not m.is_placeholder and m.stage not in dataset.KNOCKOUT_STAGES:
            errors.append(
                f"{lbl}: stage {m.stage!r} is not a knockout stage "
                f"({', '.join(sorted(dataset.KNOCKOUT_STAGES))})"
            )
    return errors


def check_drift(texts, canonical_dir):
    """Check 8: every ID present in the previous snapshot must still exist.

    Catches accidental row deletion in the sheet. First run (no snapshot yet)
    passes vacuously.
    """
    errors = []
    for tab, id_col in DRIFT_IDS.items():
        path = os.path.join(canonical_dir, f"{tab}.csv")
        if not os.path.exists(path):
            continue
        with open(path, encoding="utf-8") as fh:
            previous = {row.get(id_col, "") for _i, row in _non_blank_rows(fh.read())}
        current = {row.get(id_col, "") for _i, row in _non_blank_rows(texts[tab])}
        missing = sorted(previous - current - {""})
        for value in missing:
            errors.append(
                f"drift: {tab} {id_col} {value!r} was in the previous snapshot "
                f"but is gone from the sheet (use --allow-deletions if intended)"
            )
    return errors


def validate(texts, canonical_dir=CANONICAL_DIR, allow_deletions=False):
    """Run every check; returns (dataset_or_None, [error strings])."""
    errors = check_primary_keys(texts)
    ds = None
    if not errors:
        # Parsing enforces formats/enums (and would also trip on duplicate
        # PKs, which check 1 has already reported in full — hence the guard).
        try:
            ds = dataset.parse_all(texts)
        except dataset.DataError as err:
            errors.append(str(err))
    if ds is not None:
        errors += check_foreign_keys(ds)
        errors += check_match_entries(ds)
        errors += check_match_consistency(ds)
        errors += check_goal_counts(ds)
        errors += check_dates(ds)
        errors += check_cup_rules(ds)
        try:
            ds.active_season()
        except dataset.DataError as err:
            errors.append(str(err))
    # Drift works on the raw texts, so it runs even when parsing failed.
    if not allow_deletions:
        errors += check_drift(texts, canonical_dir)
    return ds, errors


def write_snapshot(texts, canonical_dir=CANONICAL_DIR):
    """Persist the validated fetch as the new drift baseline / audit log."""
    os.makedirs(canonical_dir, exist_ok=True)
    for tab in dataset.TABS:
        with open(os.path.join(canonical_dir, f"{tab}.csv"), "w",
                  encoding="utf-8", newline="") as fh:
            fh.write(texts[tab])


def main(argv):
    allow_deletions = "--allow-deletions" in argv
    snapshot = "--no-snapshot" not in argv

    try:
        texts = dataset.fetch_all()
    except OSError as err:
        print(f"ERROR: could not fetch data: {err}", file=sys.stderr)
        return 1

    _ds, errors = validate(texts, allow_deletions=allow_deletions)
    if errors:
        print(f"VALIDATION FAILED — {len(errors)} error(s):", file=sys.stderr)
        for e in errors:
            print(f"  ERROR: {e}", file=sys.stderr)
        return 1

    if snapshot:
        write_snapshot(texts)
        print(f"Validation OK — snapshot written to {CANONICAL_DIR}/")
    else:
        print("Validation OK")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
