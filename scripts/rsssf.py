#!/usr/bin/env python3
"""Past Super League seasons from RSSSF, as files season.py can apply.

The site knew one Super League season: the one being played. Every season
before it existed only in other people's records, and the best of those is the
Rec.Sport.Soccer Statistics Foundation's Malawi pages, which carry the full
fixture list with results under date headers and the final table, for every
season since 2006. This turns those pages into rows.

    python3 scripts/rsssf.py fetch 2025            # once; the page is committed
    python3 scripts/rsssf.py parse 2025            # -> data/backfill/rsssf/sl_2025.json
    python3 scripts/rsssf.py parse --all
    python3 scripts/season.py apply data/backfill/rsssf/sl_2025.json --dry-run

THERE IS NO API AND NO SCRAPING AT BUILD TIME. RSSSF is hand-maintained text
that changes rarely, so each page is fetched once and committed under
data/backfill/rsssf/. Parsing is then repeatable and offline, the diff of every
re-parse is reviewable, and the database holds the result. Nothing the site
builds ever talks to rsssf.org.

THE TABLE IS THE TEST. Every RSSSF page prints the final table it believes in,
and the results under it are supposed to add up to it. So `parse` recomputes
the table from the parsed results with src/standings.py — the function the site
itself uses — and refuses to write anything unless every team's P W D L GF GA
Pts agrees with RSSSF's. A missed line, a misread score, a wrong name mapping
and a missed points deduction all show up there, by team. That one check is
worth more than any number of parser unit tests, because it is about the data
and not about the parser.

NAMES ARE A HUMAN DECISION. RSSSF prints "Bullets" in results and "Nyasa Big
Bullets FC (Blantyre)" in the table, and calls the same club "Be Forward
Wanderers" in 2017 and "Mighty Mukuru Wanderers" in 2023. Which of the site's
teams each spelling means is in data/backfill/rsssf/names.json, written by a
person; a spelling it does not cover stops the parse and is named. Nothing here
guesses an identity — a wrong guess would quietly file a decade of results
against the wrong club.

RSSSF's terms: "You are free to copy this document in whole or part provided
that proper acknowledgement is given to the authors." The archive pages credit
them (see build.py), and every match row carries the page URL in source_ref.
"""

import argparse
import html
import json
import os
import re
import sys
import urllib.request
from types import SimpleNamespace

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from src import standings  # noqa: E402

DATA_DIR = os.path.join(ROOT, "data", "backfill", "rsssf")
NAMES_PATH = os.path.join(DATA_DIR, "names.json")
BASE_URL = "https://www.rsssf.org/tablesm/"

# Season label -> RSSSF page. A dict, not a pattern: RSSSF's filenames are
# not the season. malaw2015.html is the 2014 season, malaw2015a.html is 2015,
# and malaw2020.html is the whole of 2020/21 (there is no malaw2021.html).
PAGES = {
    "2016": "malaw2016.html",
    "2017": "malaw2017.html",
    "2018": "malaw2018.html",
    "2019": "malaw2019.html",
    "2020/21": "malaw2020.html",
    "2022": "malaw2022.html",
    "2023": "malaw2023.html",
    "2024": "malaw2024.html",
    "2025": "malaw2025.html",
}

# competition_seasons.sponsor_name, which the site shows as the league's name.
# Not on the RSSSF pages, which say "Super League" throughout. TNM's title
# sponsorship is named for 2016 and 2017; every later season is blank, so it
# renders as the competition itself — "Super League of Malawi"
# (dataset.league_display_name). A sponsor name nobody has confirmed is a
# false statement on every page of that season.
SPONSORS = {"2016": "TNM Super League", "2017": "TNM Super League"}

MONTHS = {m: i for i, m in enumerate(
    "jan feb mar apr may jun jul aug sep oct nov dec".split(), start=1)}


class ParseError(Exception):
    """The page or the mapping cannot be turned into rows. Nothing is written."""


# ── Season identity ──────────────────────────────────────────────────────────

def season_id_for(label):
    """'2025' -> 'MW_2025'; '2020/21' -> 'MW_2020_21' (seasons.season_id)."""
    a, sep, b = label.partition("/")
    return f"MW_{a}_{b}" if sep else f"MW_{a}"


def slug_for(label):
    """URL-safe form of a label: '2020/21' -> '2020-21'."""
    return label.replace("/", "-")


# ── Reading a page ───────────────────────────────────────────────────────────

def page_text(raw_bytes):
    """The Super League section of a page, as plain text lines.

    RSSSF pages are latin-1, not UTF-8 (Schöggl), some with bare-CR line
    endings. The section is the <pre> after the `sulom` anchor; every season
    page in PAGES has one.
    """
    raw = raw_bytes.decode("latin-1")
    found = re.search(r'name="sulom".*?<pre>(.*?)</pre>', raw, re.S | re.I)
    if not found:
        raise ParseError('no Super League section (the <a name="sulom"> block)')
    text = html.unescape(re.sub(r"<[^>]*>", "", found.group(1)))
    return text.replace("\r\n", "\n").replace("\r", "\n").split("\n")


TABLE_ROW = re.compile(
    r"^\s*(\d+)\.(.+?)\s+(\d+)\s+(\d+)\s+(\d+)\s+(\d+)\s+(\d+)-\s*(\d+)\s+(-?\d+)(.*)$")

# Home and away are separated from the score by two or more spaces, and the
# away name from any trailing comment the same way. Names themselves only ever
# contain single spaces, which is what makes the columns recoverable.
RESULT_ROW = re.compile(
    r"^(\S(?:.*?\S)?)\s{2,}(\d+-\d+|awd|abd)\s+(\S(?:.*?\S)?)(?:\s{2,}(.*))?$")

DATE_HEAD = re.compile(r"^(?:(?:Week|Round)\s+\d+\s*)?\[([A-Za-z]{3})\s+(\d{1,2})\]")
ROUND_HEAD = re.compile(r"^(?:Week|Round)\s+(\d+)\b")


def parse_table(lines):
    """The first 'Final Table:' block: [{pos, name, p, w, d, l, gf, ga, pts,
    adjustment, relegated}]."""
    try:
        start = next(i for i, l in enumerate(lines) if l.strip().startswith("Final Table"))
    except StopIteration:
        raise ParseError("no 'Final Table:' in the Super League section") from None
    rows = []
    for line in lines[start + 1:]:
        if not line.strip():
            if rows:
                break
            continue
        if set(line.strip()) == {"-"}:
            continue                      # the relegation line
        got = TABLE_ROW.match(line)
        if not got:
            if rows:
                break
            continue
        tail = got.group(10)
        deduction = re.search(r"\[-(\d+)\]", tail)
        rows.append({
            "pos": int(got.group(1)),
            "name": got.group(2).strip(),
            "p": int(got.group(3)), "w": int(got.group(4)),
            "d": int(got.group(5)), "l": int(got.group(6)),
            "gf": int(got.group(7)), "ga": int(got.group(8)),
            "pts": int(got.group(9)),
            "adjustment": -int(deduction.group(1)) if deduction else 0,
            "relegated": "Relegated" in tail,
        })
    if not rows:
        raise ParseError("the Final Table has no rows")
    return rows


def parse_results(lines, label):
    """Every result line in the section, in page order.

    Returns (results, warnings). A result is {date, home, away, home_goals,
    away_goals, status, awarded_note}.

    * The year comes from the season. A season across two years (2020/21)
      takes the start year for months from its first header's month onwards
      and the next year before it — a rule about the calendar, not about page
      order, because "Moved Matches" blocks print earlier months late.
    * NO MATCHDAY IS READ, though some pages print `Week N` or `Round N`.
      They are not the same thing and not reliable as either: 2022 has 33
      "weeks" for a 30-round season (a week is a calendar slot, postponements
      included), 2023 prints rounds for the first half only and runs the rest
      on under "Round 15", and 2020/21 files its rearranged games under "Moved
      Matches". A wrong matchday is worse than none — it reorders the form
      column and draws a position chart of rounds that never happened — so it
      is left blank, and the archive groups a season by month instead.
    * `awd` is an awarded match; its score is in the comment ("awarded 2-0").
    * `abd` with no score is an abandoned match. It is DROPPED — if it was
      replayed the replay is its own line, and if it was not, it has no result
      to record. A score line whose comment says "abandoned … result stood" is
      an ordinary played match and stays.
    """
    start_year = int(label[:4])
    split_season = "/" in label
    first_month = None
    date = None
    results, warnings = [], []

    for raw in lines:
        line = raw.rstrip()
        stripped = line.strip()
        if not stripped or TABLE_ROW.match(line):
            continue
        if stripped.startswith("Moved Matches"):
            continue
        head = ROUND_HEAD.match(stripped)
        when = DATE_HEAD.match(stripped)
        if when:
            month = MONTHS.get(when.group(1).lower())
            if month is None:
                raise ParseError(f"unreadable date header: {stripped!r}")
            if first_month is None:
                first_month = month
            year = start_year
            if split_season and month < first_month:
                year += 1
            date = f"{year:04d}-{month:02d}-{int(when.group(2)):02d}"
            continue
        if head or line[:1].isspace():
            continue                      # a header alone, or a comment's tail
        got = RESULT_ROW.match(line)
        if not got:
            continue
        home, token, away, comment = got.group(1), got.group(2), got.group(3), got.group(4) or ""
        comment = comment.strip()
        if date is None:
            raise ParseError(f"result before any date header: {stripped!r}")
        if token == "abd":
            warnings.append(f"{date} {home} v {away}: abandoned, no result — "
                            f"left out ({comment.strip('[]') or 'no note'})")
            continue
        if token == "awd":
            score = re.search(r"awarded (\d+)-(\d+)", comment)
            if not score:
                raise ParseError(f"awarded match with no awarded score: {stripped!r}")
            hg, ag = int(score.group(1)), int(score.group(2))
            status = "awarded"
            note = comment.lstrip("[").rstrip("]").strip()
            note = note[:1].upper() + note[1:]
        else:
            hg, ag = (int(x) for x in token.split("-"))
            status, note = "played", ""
        results.append({
            "date": date, "home": home, "away": away,
            "home_goals": hg, "away_goals": ag, "status": status,
            "awarded_note": note,
        })
    return results, warnings


def parse_page(raw_bytes, label):
    lines = page_text(raw_bytes)
    table = parse_table(lines)
    results, warnings = parse_results(lines, label)
    return {"table": table, "results": results, "warnings": warnings}


# ── Names ────────────────────────────────────────────────────────────────────

def load_names(path=NAMES_PATH):
    with open(path, encoding="utf-8") as fh:
        return json.load(fh)


def resolver(names, label):
    """A function spelling -> team key, raising on a spelling names.json
    does not cover. A per-season entry beats the global one."""
    table = dict(names.get("spellings", {}))
    table.update(names.get("seasons", {}).get(label, {}))

    def resolve(spelling):
        key = table.get(spelling)
        if key is None:
            raise KeyError(spelling)
        return key
    return resolve


def unmapped(parsed, names, label):
    """Every spelling on the page that names.json does not cover."""
    resolve = resolver(names, label)
    seen = [r["name"] for r in parsed["table"]]
    for r in parsed["results"]:
        seen += [r["home"], r["away"]]
    missing = []
    for s in dict.fromkeys(seen):
        try:
            resolve(s)
        except KeyError:
            missing.append(s)
    return missing


# ── The check ────────────────────────────────────────────────────────────────

def check(parsed, names, label):
    """Recompute the table from the results and compare it with RSSSF's.

    Returns (errors, warnings). Errors are disagreements in any team's
    P W D L GF GA Pts — the results do not add up to the table, so something
    was misread or mismapped. Warnings are ORDER differences between teams
    level on points, goal difference and goals scored: the site's tiebreak
    stops there (src/standings.py) and RSSSF's may have gone on to head to
    head, so that is a decision about rendering, not an error in the data.
    """
    resolve = resolver(names, label)
    errors, warnings = [], []

    table_keys = {}
    for row in parsed["table"]:
        key = resolve(row["name"])
        if key in table_keys:
            errors.append(f"two table rows map to {key}: "
                          f"{table_keys[key]['name']!r} and {row['name']!r}")
        table_keys[key] = row

    teams = {k: SimpleNamespace(name=k) for k in table_keys}
    matches = []
    for r in parsed["results"]:
        home, away = resolve(r["home"]), resolve(r["away"])
        for side in (home, away):
            if side not in teams:
                errors.append(f"{r['date']} {r['home']} v {r['away']}: "
                              f"{side} is not in the final table")
                teams[side] = SimpleNamespace(name=side)
        matches.append(SimpleNamespace(
            played=True, home_code=home, away_code=away,
            home_goals=r["home_goals"], away_goals=r["away_goals"]))

    adjustments = {k: row["adjustment"] for k, row in table_keys.items()
                   if row["adjustment"]}
    rows = standings.compute_standings(matches, teams, adjustments=adjustments)
    for s in rows:
        want = table_keys.get(s.code)
        if want is None:
            continue
        got = {"p": s.played, "w": s.won, "d": s.drawn, "l": s.lost,
               "gf": s.gf, "ga": s.ga, "pts": s.points}
        diff = [f"{k} {got[k]} (RSSSF {want[k]})" for k in got if got[k] != want[k]]
        if diff:
            errors.append(f"{want['name']}: " + ", ".join(diff))

    if not errors:
        for s in rows:
            want = table_keys[s.code]
            if s.position != want["pos"]:
                warnings.append(
                    f"{want['name']}: the site would rank it {s.position}, "
                    f"RSSSF ranks it {want['pos']} (level on points, GD and "
                    f"goals; RSSSF's tiebreak goes further than the site's)")
    return errors, warnings


# ── Output ───────────────────────────────────────────────────────────────────

def _month_end(year, month):
    import calendar
    return calendar.monthrange(year, month)[1]


def season_row(label, results):
    """The seasons row. A calendar season is the calendar year; a split one
    runs from the first of its first match's month to the end of its last
    match's month. Either way every match date is inside it (check 6)."""
    if "/" not in label:
        y = int(label)
        start, end = f"{y}-01-01", f"{y}-12-31"
    else:
        dates = sorted(r["date"] for r in results)
        y0, m0 = int(dates[0][:4]), int(dates[0][5:7])
        y1, m1 = int(dates[-1][:4]), int(dates[-1][5:7])
        start = f"{y0:04d}-{m0:02d}-01"
        end = f"{y1:04d}-{m1:02d}-{_month_end(y1, m1):02d}"
    return {"season_id": season_id_for(label), "country": "MW", "label": label,
            "start_date": start, "end_date": end, "status": "complete"}


def to_spec(parsed, names, label):
    """A season.py file: the season, MW_SL's row for it, its sixteen entries
    and every result. Teams are written under their RSSSF table name with the
    team_id (or the new-team spec) names.json gave them; fixtures name the
    same strings, so season.py resolves them without matching anything."""
    resolve = resolver(names, label)
    new_teams = names.get("new_teams", {})
    url = BASE_URL + PAGES[label]

    by_key = {}
    for row in parsed["table"]:
        key = resolve(row["name"])
        if key in new_teams:
            team = dict(new_teams[key])
            team.setdefault("name", key)
        else:
            team = {"name": key, "team_id": key}
        if row["adjustment"]:
            team["points_adjustment"] = row["adjustment"]
            team["adjustment_reason"] = (
                f"{-row['adjustment']} points deducted (RSSSF)")
        by_key[key] = team

    fixtures = []
    for r in parsed["results"]:
        home, away = resolve(r["home"]), resolve(r["away"])
        item = {
            "date": r["date"],
            "home": by_key[home]["name"], "away": by_key[away]["name"],
            "home_goals": r["home_goals"], "away_goals": r["away_goals"],
            "status": r["status"],
            "source_type": "backfill", "source_ref": url,
            "confidence": "confirmed",
        }
        if r["awarded_note"]:
            item["awarded_note"] = r["awarded_note"]
        fixtures.append(item)

    relegated = sum(1 for row in parsed["table"] if row["relegated"])
    return {
        "season_id": season_id_for(label),
        "season": season_row(label, parsed["results"]),
        "competition": {
            "competition_id": "MW_SL",
            "sponsor_name": SPONSORS.get(label, ""),
            "format": "round_robin_2x",
            "teams_count": len(parsed["table"]),
            "promotion_places": 0,
            "relegation_places": relegated,
            "points_win": 3, "points_draw": 1,
            "status": "complete",
        },
        "teams": list(by_key.values()),
        "fixtures": fixtures,
    }


# ── Commands ─────────────────────────────────────────────────────────────────

def _labels(args):
    if args.all:
        return list(PAGES)
    bad = [l for l in args.seasons if l not in PAGES]
    if bad:
        raise ParseError(f"unknown season(s) {bad}; known: {', '.join(PAGES)}")
    if not args.seasons:
        raise ParseError("name a season (e.g. 2025 or 2020/21) or pass --all")
    return args.seasons


def cmd_fetch(args):
    os.makedirs(DATA_DIR, exist_ok=True)
    for label in _labels(args):
        page = PAGES[label]
        req = urllib.request.Request(BASE_URL + page,
                                     headers={"User-Agent": "everyleague-backfill"})
        with urllib.request.urlopen(req, timeout=30) as resp:
            body = resp.read()
        with open(os.path.join(DATA_DIR, page), "wb") as fh:
            fh.write(body)
        print(f"  {label:8} {page}  {len(body):6} bytes")
    return 0


def cmd_parse(args):
    names = load_names()
    failed = False
    for label in _labels(args):
        with open(os.path.join(DATA_DIR, PAGES[label]), "rb") as fh:
            parsed = parse_page(fh.read(), label)
        missing = unmapped(parsed, names, label)
        if missing:
            print(f"{label}: {len(missing)} spelling(s) not in names.json:")
            for s in missing:
                print(f"    {s!r}")
            failed = True
            continue
        errors, warnings = check(parsed, names, label)
        for w in parsed["warnings"] + warnings:
            print(f"{label}: warning: {w}")
        if errors:
            print(f"{label}: the results do not add up to RSSSF's table:")
            for e in errors:
                print(f"    {e}")
            failed = True
            continue
        spec = to_spec(parsed, names, label)
        out = os.path.join(DATA_DIR, f"sl_{slug_for(label)}.json")
        with open(out, "w", encoding="utf-8") as fh:
            json.dump(spec, fh, indent=1, ensure_ascii=False)
            fh.write("\n")
        print(f"{label}: {len(spec['fixtures'])} results, {len(spec['teams'])} "
              f"teams, table agrees -> {os.path.relpath(out, ROOT)}")
    return 1 if failed else 0


def main(argv=None):
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="command", required=True)
    for name, func, text in (("fetch", cmd_fetch, "download pages into data/backfill/rsssf/"),
                             ("parse", cmd_parse, "check and write season.py files")):
        p = sub.add_parser(name, help=text)
        p.add_argument("seasons", nargs="*")
        p.add_argument("--all", action="store_true")
        p.set_defaults(func=func)
    args = parser.parse_args(argv)
    try:
        return args.func(args)
    except ParseError as err:
        print(f"ERROR: {err}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
