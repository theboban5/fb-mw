#!/usr/bin/env python3
"""Set up a competition: teams, venues, entries and fixtures, in one go.

Adding a division used to mean hand-editing six spreadsheet tabs in the right
order — clubs before teams before entries before matches, minting ids by hand
and hoping the foreign keys line up. The /admin Apps Script did some of that,
but it wrote to the spreadsheet, which is no longer the source of truth.

This replaces it. Describe the division in one JSON file and apply it:

    python3 scripts/season.py template > new-division.json
    $EDITOR new-division.json
    python3 scripts/season.py apply new-division.json --dry-run   # read the plan
    python3 scripts/season.py apply new-division.json
    python3 scripts/season.py logos new-division.json             # where badges go

Everything is optional except the season. Use the same command to add fixtures
to a competition that already exists, to add three teams mid-season, or to set
up a whole new division — leave out the parts you do not need.

Two properties make it safe to run repeatedly:

  * NOTHING IS INVENTED TWICE. A club, team, venue or fixture that already
    matches is reused, not duplicated. Re-running an unchanged file is a no-op.
  * NOTHING IS WRITTEN UNTIL EVERYTHING RESOLVES. The whole plan is built and
    checked first, so a typo in the last fixture cannot leave half a division
    in the database.

Teams and venues are referred to by NAME throughout, including in fixtures.
Ids are minted following the conventions already in the data and printed in the
plan; set them explicitly in the file if you want something else.

Requires SUPABASE_URL and SUPABASE_SECRET_KEY (server-side only).
"""

import argparse
import json
import os
import re
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from src import supabase_client as sb  # noqa: E402


class PlanError(Exception):
    """Something in the file cannot be resolved. Nothing has been written."""


TEMPLATE = {
    "season_id": "MW_2026_27",
    "competition": {
        "competition_id": "MW_NRFA2",
        "name": "NRFA Division Two",
        "type": "league",
        "tier": 4,
        "gender": "m",
        "age_group": "senior",
        "region": "NRFA",
        "governing_body": "NRFA",
        "sponsor_name": "",
        "points_win": 3,
        "points_draw": 1,
        "promotion_places": 1,
        "relegation_places": 0,
    },
    "venues": [
        {"name": "Chitipa Community Ground", "city": "Chitipa"},
    ],
    "teams": [
        {"name": "Chitipa United", "city": "Chitipa"},
        {"name": "Ekwendeni Hammers", "city": "Ekwendeni"},
    ],
    "fixtures": [
        {
            "matchday": 1,
            "date": "2026-09-05",
            "kickoff": "14:30",
            "home": "Chitipa United",
            "away": "Ekwendeni Hammers",
            "venue": "Chitipa Community Ground",
        },
    ],
}

TEMPLATE_NOTES = """\
// Delete anything you do not need — every section is optional except season_id.
//
//   competition  omit to add to a competition that already exists
//   teams        a name that already exists anywhere is REUSED, not duplicated
//   venues       same
//   fixtures     home/away/venue refer to teams and venues by name
//
// Team options:  {"name": ..., "city": ..., "short_name": ...,
//                 "gender": "m"|"w", "age_group": "senior"|"u20"|"u17"|"u16",
//                 "squad_level": 1, "club_id": ..., "team_id": ...,
//                 "legacy_code": ...}
//   club_id / team_id / legacy_code are minted for you unless you set them.
//   legacy_code is the badge filename and the public club-page URL.
//
// Fixture options: {"matchday": 1, "date": "YYYY-MM-DD", "kickoff": "HH:MM",
//                   "home": ..., "away": ..., "venue": ..., "stage": "md_1"}
//   date and kickoff may be left out — an unscheduled fixture is legal.
//   For a cup, set "stage" to r64|r32|r16|qf|sf|final|3p and omit matchday.
"""


# ── Id minting ───────────────────────────────────────────────────────────────
# Following the conventions already in the data. Ids are opaque — nothing
# parses them — so these only have to be stable, readable and unique.

def _slug(name, length=4):
    letters = re.sub(r"[^A-Za-z0-9]", "", name).upper()
    return letters[:length] or "X"


def _initials(name):
    return "".join(w[0] for w in re.findall(r"[A-Za-z0-9]+", name)).upper()[:4]


def _unique(candidate, taken, prefix=""):
    """First free id from candidate, then candidate2, candidate3, ..."""
    value = f"{prefix}{candidate}"
    if value not in taken:
        return value
    for n in range(2, 100):
        alt = f"{prefix}{candidate}{n}"
        if alt not in taken:
            return alt
    raise PlanError(f"could not find a free id near {value!r}")


def season_labels(season):
    """('26/27', '2627') from a season row — the two forms the ids use.

    entries use the slashed form (MW_SL_26/27_BLUE_M1) and matches the compact
    one (MW_SL_2627_001). Both come from seasons.label, never from parsing
    another id.
    """
    label = season["label"]
    a, sep, b = label.partition("/")
    short = f"{a[2:]}/{b}" if sep and len(a) == 4 else label
    return short, short.replace("/", "")


# ── Existing state ───────────────────────────────────────────────────────────

def load_existing():
    get = lambda t, **kw: sb.select(t, require_secret=True, **kw)  # noqa: E731
    return {
        "clubs": get("clubs"),
        "teams": get("teams"),
        "competitions": get("competitions"),
        "seasons": get("seasons"),
        "competition_seasons": get("competition_seasons"),
        "entries": get("entries"),
        "venues": get("venues"),
        "matches": get("matches", columns="match_id,competition_id,season_id,"
                                          "home_team_id,away_team_id,date,matchday"),
    }


def _norm(name):
    return re.sub(r"\s+", " ", (name or "").strip()).lower()


# ── Planning ─────────────────────────────────────────────────────────────────

class Plan:
    def __init__(self):
        self.clubs, self.teams, self.venues = [], [], []
        self.competitions, self.competition_seasons = [], []
        self.entries, self.matches = [], []
        self.notes = []
        # Every team the file names, new or already present, as
        # display name -> team_id. `logos` needs all of them: badge filenames
        # are most wanted AFTER applying, when nothing is new any more.
        self.resolved_teams = {}

    def empty(self):
        return not any([self.clubs, self.teams, self.venues, self.competitions,
                        self.competition_seasons, self.entries, self.matches])


def build_plan(spec, existing):
    plan = Plan()

    season_id = spec.get("season_id")
    if not season_id:
        raise PlanError('"season_id" is required (e.g. "MW_2026_27")')
    season = next((s for s in existing["seasons"] if s["season_id"] == season_id), None)
    if season is None:
        raise PlanError(
            f"season {season_id!r} does not exist. Known: "
            + ", ".join(s["season_id"] for s in existing["seasons"]))
    label_slash, label_compact = season_labels(season)

    # ── Competition ──────────────────────────────────────────────────────────
    comp_spec = spec.get("competition") or {}
    comp_ids = {c["competition_id"] for c in existing["competitions"]}
    if comp_spec:
        competition_id = comp_spec.get("competition_id")
        if not competition_id:
            raise PlanError('"competition" needs a "competition_id"')
        if competition_id not in comp_ids:
            for required in ("name", "type", "gender", "age_group"):
                if not comp_spec.get(required):
                    raise PlanError(f'competition is missing "{required}"')
            plan.competitions.append({
                "competition_id": competition_id,
                "country": comp_spec.get("country", "mw").lower(),
                "name": comp_spec["name"],
                "type": comp_spec["type"].lower(),
                "tier": comp_spec.get("tier"),
                "gender": comp_spec["gender"].lower(),
                "age_group": comp_spec["age_group"].lower(),
                "region": comp_spec.get("region", ""),
                "governing_body": comp_spec.get("governing_body", ""),
                "logo": comp_spec.get("logo", ""),
                "ord": len(existing["competitions"]) + 1,
            })
        have_cs = any(cs["competition_id"] == competition_id
                      and cs["season_id"] == season_id
                      for cs in existing["competition_seasons"])
        if not have_cs:
            plan.competition_seasons.append({
                "competition_id": competition_id,
                "season_id": season_id,
                "sponsor_name": comp_spec.get("sponsor_name", ""),
                "format": comp_spec.get("format", "round_robin_2x"),
                "teams_count": comp_spec.get("teams_count"),
                "promotion_places": comp_spec.get("promotion_places"),
                "relegation_places": comp_spec.get("relegation_places"),
                "points_win": comp_spec.get("points_win", 3),
                "points_draw": comp_spec.get("points_draw", 1),
                "status": comp_spec.get("status", "active"),
                "ord": len(existing["competition_seasons"]) + 1,
            })
        comp_type = comp_spec.get("type", "league").lower()
    else:
        competition_id = spec.get("competition_id")
        if not competition_id:
            raise PlanError(
                'no "competition" block, so "competition_id" is required to say '
                'which existing competition these teams and fixtures belong to')
        if competition_id not in comp_ids:
            raise PlanError(f"competition {competition_id!r} does not exist")
        comp_type = next(c["type"] for c in existing["competitions"]
                         if c["competition_id"] == competition_id)

    # ── Venues ───────────────────────────────────────────────────────────────
    venue_by_name = {_norm(v["name"]): v["venue_id"] for v in existing["venues"]}
    venue_ids = set(venue_by_name.values())
    for item in spec.get("venues", []):
        name = item.get("name")
        if not name:
            raise PlanError("every venue needs a name")
        if _norm(name) in venue_by_name:
            plan.notes.append(f"venue already exists, reusing: {name}")
            continue
        venue_id = item.get("venue_id") or _unique(
            _slug(name), venue_ids, prefix="MW_") + "_G"
        while venue_id in venue_ids:
            venue_id = _unique(_slug(name) + "X", venue_ids, prefix="MW_") + "_G"
        venue_ids.add(venue_id)
        venue_by_name[_norm(name)] = venue_id
        plan.venues.append({
            "venue_id": venue_id, "name": name,
            "city": item.get("city", ""), "capacity": item.get("capacity", ""),
            "ord": len(existing["venues"]) + len(plan.venues) + 1,
        })

    # ── Clubs and teams ──────────────────────────────────────────────────────
    club_by_name = {_norm(c["name"]): c["club_id"] for c in existing["clubs"]}
    club_ids = set(club_by_name.values())
    team_ids = {t["team_id"] for t in existing["teams"]}
    team_by_name = {}
    for t in existing["teams"]:
        team_by_name.setdefault(_norm(t["display_name"]), t["team_id"])

    resolved_team = {}          # name as written -> team_id
    for item in spec.get("teams", []):
        name = item.get("name")
        if not name:
            raise PlanError("every team needs a name")
        key = _norm(name)

        if item.get("team_id") and item["team_id"] in team_ids:
            resolved_team[key] = item["team_id"]
            plan.notes.append(f"team already exists, reusing: {name}")
            continue
        if key in team_by_name and not item.get("team_id"):
            # The common case for a promoted or relegated side: it is already
            # in the data, and only needs an entry in the new competition.
            resolved_team[key] = team_by_name[key]
            plan.notes.append(f"team already exists, reusing: {name}")
            continue

        club_id = item.get("club_id") or club_by_name.get(key)
        if not club_id:
            candidate = _slug(name)
            if f"MW_{candidate}" in club_ids:
                candidate = _initials(name)
            club_id = _unique(candidate, club_ids, prefix="MW_")
            club_ids.add(club_id)
            club_by_name[key] = club_id
            plan.clubs.append({
                "club_id": club_id, "name": name,
                "short_name": item.get("short_name", ""),
                "city": item.get("city", ""),
                "region": item.get("region", ""),
                "status": "active",
                "ord": len(existing["clubs"]) + len(plan.clubs) + 1,
            })

        gender = item.get("gender", "m").lower()
        age_group = item.get("age_group", "senior").lower()
        level = int(item.get("squad_level", 1))
        suffix = f"_{gender.upper()}{level}"
        team_id = item.get("team_id") or _unique(club_id + suffix, team_ids)
        team_ids.add(team_id)
        resolved_team[key] = team_id
        team_by_name.setdefault(key, team_id)
        plan.teams.append({
            "team_id": team_id, "club_id": club_id,
            "gender": gender, "age_group": age_group, "squad_level": level,
            "display_name": name,
            # The badge filename and the public club-page URL key. Left blank
            # means the build falls back to club_id for both.
            "legacy_code": item.get("legacy_code", ""),
            "status": "active",
            "ord": len(existing["teams"]) + len(plan.teams) + 1,
        })

    plan.resolved_teams = dict(resolved_team)

    # ── Entries ──────────────────────────────────────────────────────────────
    entered = {(e["competition_id"], e["season_id"], e["team_id"])
               for e in existing["entries"]}
    entry_ids = {e["entry_id"] for e in existing["entries"]}
    for name, team_id in resolved_team.items():
        if (competition_id, season_id, team_id) in entered:
            continue
        suffix = team_id[3:] if team_id.startswith("MW_") else team_id
        entry_id = _unique(f"{competition_id}_{label_slash}_{suffix}", entry_ids)
        entry_ids.add(entry_id)
        entered.add((competition_id, season_id, team_id))
        plan.entries.append({
            "entry_id": entry_id, "competition_id": competition_id,
            "season_id": season_id, "team_id": team_id,
            "points_adjustment": 0, "status": "active",
            "ord": len(existing["entries"]) + len(plan.entries) + 1,
        })

    # ── Fixtures ─────────────────────────────────────────────────────────────
    def team_for(label):
        key = _norm(label)
        if key in resolved_team:
            return resolved_team[key]
        if key in team_by_name:
            return team_by_name[key]
        raise PlanError(
            f"fixture names a team that is neither in this file nor in the "
            f"data: {label!r}")

    existing_pairs = {(m["competition_id"], m["season_id"], m["home_team_id"],
                       m["away_team_id"], m["date"]) for m in existing["matches"]}
    used_numbers = []
    for m in existing["matches"]:
        got = re.match(rf"^{re.escape(competition_id)}_{label_compact}_(\d+)$",
                       m["match_id"])
        if got:
            used_numbers.append(int(got.group(1)))
    next_number = (max(used_numbers) + 1) if used_numbers else 1
    match_ids = {m["match_id"] for m in existing["matches"]}

    for item in spec.get("fixtures", []):
        home = team_for(item.get("home", ""))
        away = team_for(item.get("away", ""))
        if home == away:
            raise PlanError(f"a team cannot play itself: {item.get('home')!r}")
        for side, team_id in (("home", home), ("away", away)):
            if (competition_id, season_id, team_id) not in entered:
                raise PlanError(
                    f"fixture {item.get('home')} v {item.get('away')}: the "
                    f"{side} team is not entered in {competition_id} for "
                    f"{season_id}. Add it under \"teams\".")
        date = item.get("date", "") or ""
        if date and not re.fullmatch(r"\d{4}-\d{2}-\d{2}", date):
            raise PlanError(f"date {date!r} must be YYYY-MM-DD (or left out)")
        if (competition_id, season_id, home, away, date or None) in existing_pairs:
            plan.notes.append(
                f"fixture already exists, skipping: {item.get('home')} v "
                f"{item.get('away')}")
            continue

        matchday = item.get("matchday")
        stage = item.get("stage") or (f"md_{matchday}" if matchday else "")
        if comp_type == "cup":
            if stage not in ("r64", "r32", "r16", "qf", "sf", "final", "3p"):
                raise PlanError(
                    f"{competition_id} is a cup, so each fixture needs a "
                    f'"stage" from r64|r32|r16|qf|sf|final|3p (got {stage!r})')
            matchday = None

        venue_id = None
        if item.get("venue"):
            venue_id = venue_by_name.get(_norm(item["venue"]))
            if not venue_id:
                raise PlanError(
                    f"fixture names a venue that is neither in this file nor "
                    f"in the data: {item['venue']!r}")

        match_id = _unique(f"{competition_id}_{label_compact}_"
                           f"{next_number:03d}", match_ids)
        match_ids.add(match_id)
        next_number += 1
        plan.matches.append({
            "match_id": match_id, "competition_id": competition_id,
            "season_id": season_id, "stage": stage, "matchday": matchday,
            "date": date or None, "kickoff": item.get("kickoff", ""),
            "venue_id": venue_id, "home_team_id": home, "away_team_id": away,
            "status": "scheduled", "source_type": "fa",
            "confidence": "confirmed",
            "ord": len(existing["matches"]) + len(plan.matches) + 1,
        })

    return plan, competition_id


# ── Output ───────────────────────────────────────────────────────────────────

SECTIONS = [
    ("competitions", "competition", "competitions", "competition_id"),
    ("competition_seasons", "competition season", "competition seasons",
     "competition_id"),
    ("venues", "venue", "venues", "venue_id"),
    ("clubs", "club", "clubs", "club_id"),
    ("teams", "team", "teams", "team_id"),
    ("entries", "entry", "entries", "entry_id"),
    ("matches", "fixture", "fixtures", "match_id"),
]


def describe(plan):
    for attr, singular, plural, key in SECTIONS:
        rows = getattr(plan, attr)
        if not rows:
            continue
        print(f"\n  {len(rows)} new {singular if len(rows) == 1 else plural}:")
        for row in rows[:40]:
            if attr == "matches":
                when = row["date"] or "date TBC"
                extra = f' {row["kickoff"]}' if row["kickoff"] else ""
                print(f"    {row[key]:28} {row['home_team_id']} v "
                      f"{row['away_team_id']}  {when}{extra}")
            elif attr == "teams":
                print(f"    {row[key]:28} {row['display_name']}")
            elif attr in ("clubs", "venues"):
                print(f"    {row[key]:28} {row['name']}")
            else:
                print(f"    {row[key]}")
        if len(rows) > 40:
            print(f"    ... and {len(rows) - 40} more")
    if plan.notes:
        print(f"\n  {len(plan.notes)} thing(s) already present, left alone:")
        for note in plan.notes[:20]:
            print(f"    {note}")
        if len(plan.notes) > 20:
            print(f"    ... and {len(plan.notes) - 20} more")


# Written in foreign-key order: a match cannot exist before the entries that
# prove both its teams belong to the competition (0001's composite FK).
WRITE_ORDER = [
    ("competitions", "competition_id"),
    ("competition_seasons", "competition_id,season_id"),
    ("venues", "venue_id"),
    ("clubs", "club_id"),
    ("teams", "team_id"),
    ("entries", "entry_id"),
    ("matches", "match_id"),
]


def apply_plan(plan):
    for attr, conflict in WRITE_ORDER:
        rows = getattr(plan, attr)
        if rows:
            sb.upsert(attr, rows, on_conflict=conflict)
            print(f"  wrote {len(rows):4} -> {attr}")


# ── Commands ─────────────────────────────────────────────────────────────────

def cmd_template(args):
    print(TEMPLATE_NOTES)
    print(json.dumps(TEMPLATE, indent=2))
    return 0


def _read(path):
    with open(path, encoding="utf-8") as fh:
        text = fh.read()
    # Tolerate // comments so the template's notes can stay in the file.
    text = re.sub(r"^\s*//.*$", "", text, flags=re.M)
    try:
        return json.loads(text)
    except json.JSONDecodeError as err:
        raise PlanError(f"{path} is not valid JSON: {err}") from None


def cmd_apply(args):
    spec = _read(args.file)
    existing = load_existing()
    plan, competition_id = build_plan(spec, existing)

    if plan.empty():
        print("Nothing to do — everything in that file already exists.")
        describe(plan)
        return 0

    print(f"Plan for {competition_id} / {spec['season_id']}:")
    describe(plan)

    if args.dry_run:
        print("\n(dry run — nothing written. Drop --dry-run to apply.)")
        return 0

    print()
    apply_plan(plan)
    print("\nDone. Next:")
    if plan.teams:
        print(f"  * badges:  python3 scripts/season.py logos {args.file}")
    print("  * the site updates on the next build (a reporter publishing a "
          "result triggers one, or run the workflow by hand).")
    return 0


def cmd_logos(args):
    """Where to save each badge, using the key the renderer actually looks up."""
    spec = _read(args.file)
    existing = load_existing()
    plan, competition_id = build_plan(spec, existing)

    print("Save badge files here (PNG or SVG; they are downscaled at build time):\n")
    print(f"  static/logos/competitions/{competition_id}.png"
          f"{' ' * max(1, 26 - len(competition_id))}<- competition logo")

    # Every team the file names, whether or not this run created it — the
    # badges are usually collected after the division is already set up.
    by_id = {t["team_id"]: t for t in existing["teams"]}
    for t in plan.teams:
        by_id[t["team_id"]] = t

    seen = set()
    for name, team_id in sorted(plan.resolved_teams.items()):
        team = by_id.get(team_id)
        if not team:
            continue
        # render.py tries logos/clubs/<legacy_code> first, then <club_id>, so
        # the file has to be named for whichever key this team actually carries.
        key = team.get("legacy_code") or team["club_id"]
        if key in seen:
            continue
        seen.add(key)
        label = team.get("display_name", name)
        print(f"  static/logos/clubs/{key}.png"
              f"{' ' * max(1, 26 - len(key))}<- {label}")
    if not seen:
        print("  (no teams named in this file)")
    print("\nMissing badges are fine — the page renders without one.")
    return 0


def main(argv=None):
    parser = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser("template", help="print a starter file")
    p.set_defaults(func=cmd_template)

    p = sub.add_parser("apply", help="create everything the file describes")
    p.add_argument("file")
    p.add_argument("--dry-run", action="store_true",
                   help="show the plan without writing")
    p.set_defaults(func=cmd_apply)

    p = sub.add_parser("logos", help="print where each badge file should go")
    p.add_argument("file")
    p.set_defaults(func=cmd_logos)

    args = parser.parse_args(argv)
    sb.load_dotenv(os.path.join(ROOT, ".env"))
    try:
        if args.command != "template":
            sb.key(require_secret=True)
        return args.func(args)
    except PlanError as err:
        print(f"\nERROR: {err}\n\nNothing was written.", file=sys.stderr)
        return 1
    except sb.SupabaseError as err:
        print(f"\nERROR: {err}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
