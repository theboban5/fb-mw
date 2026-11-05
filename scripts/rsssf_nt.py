#!/usr/bin/env python3
"""The men's national team's every international since 1957, from RSSSF.

The site had nineteen Flames matches, all from 2025 on. RSSSF's "Malawi - List
of International Matches" (Barrie Courtney) has every one it knows of, back to
a 1957 friendly against Northern Rhodesia: date, opponent, host country, score,
half-time, shootout, crowd, competition and Malawi's scorers, newest first.

    python3 scripts/rsssf_nt.py fetch     # once; the page is committed
    python3 scripts/rsssf_nt.py parse     # -> data/backfill/rsssf/nt_mw_m.json
    python3 scripts/rsssf_nt.py apply --dry-run   # the plan, against the live DB
    python3 scripts/rsssf_nt.py apply             # needs migration 0066

The arrangement is scripts/rsssf.py's, and so are its three rules:

FETCHED ONCE, COMMITTED, NEVER READ AT BUILD TIME. data/backfill/rsssf/
malaw-intres.html is the page as downloaded (latin-1, bare-CR line endings).
A re-parse is offline and its diff is reviewable.

THE TABLE IS THE TEST. Under the list RSSSF prints its own record against
every country — played, won, drawn, lost, for, against, the year last met —
plus a competitive summary (World Cup and Nations Cup qualifiers and finals)
and a count of unidentified scorers. `parse` recomputes all three from the
parsed lines and writes nothing unless every figure agrees. Those tables
cover only the matches WITHOUT RSSSF's '*' ("unsure of status / not a full
international"), which is the site's own rule for the record: an unofficial
match is shown, marked, and left out of every total. So the check is also a
check that the unofficial flag was read right.

NAMES ARE A HUMAN DECISION, in data/backfill/rsssf/nt_names.json: what each
printed opponent renders as (the name of the day — Swaziland, Zaire, Southern
Rhodesia — with RSSSF's capitals and abbreviations undone), which country's
head-to-head it belongs to, what each venue code was called on the date, and
what each competition code means. A spelling it does not cover stops the parse.

`parse` writes a REVIEW FILE and touches nothing. `apply` is the load, and it
re-parses the committed page rather than reading the review file, so nothing
that fails the check can reach the database. Against the LIVE nt_matches:

  * a match already there (the 2025 rows a reporter typed, or an archive row
    from an earlier apply) is paired by opponent and score within ten days.
    Its date is corrected where RSSSF's differs — the decision for the 2025
    COSAFA rows, all three entered as 5 June — and the archive-only fields
    (stage, half-time, crowd, shootout score, opponent_country) are filled
    where blank. Its score, status, competition, venue and goals are never
    touched: a person typed them.
  * anything else is inserted as source_type 'backfill', which keeps it off
    /matches/ (as an archived league season is), with its Malawi scorers as
    name-only nt_goals rows — no player_id, so no profile is invented for a
    1970s international from an initial and a surname.
  * ids are the next integers after the largest in the table, because
    next_nt_id (0012) casts every match_id and goal_id to an integer and a
    non-numeric one would break every national-team write in the portal.
  * a neutral ground is stored home_away 'away': RSSSF never says which side
    was the designated home team, the column is required, and the page reads
    "Neutral" either way.

Running it twice changes nothing the second time: every row it inserted pairs
with itself. An archive match whose goals did not arrive (a failed run) gets
them on the next one; a typed match never gets RSSSF's.

RSSSF's terms: "You are free to copy this document in whole or part provided
that proper acknowledgement is given to the author." Every match carries the
page URL in source_ref, and every page built from it must credit the author.
"""

import argparse
import csv
import html
import json
import os
import re
import sys
import urllib.request
from collections import defaultdict
from datetime import date as date_cls

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from src import supabase_client as sb  # noqa: E402

DATA_DIR = os.path.join(ROOT, "data", "backfill", "rsssf")
PAGE = "malaw-intres.html"
URL = "https://www.rsssf.org/tablesm/" + PAGE
NAMES_PATH = os.path.join(DATA_DIR, "nt_names.json")
OUT_PATH = os.path.join(DATA_DIR, "nt_mw_m.json")
EXISTING_PATH = os.path.join(ROOT, "data", "canonical", "nt_matches.csv")

TEAM_CODE = "MW_M"
CREDIT = ("Barrie Courtney for the Rec.Sport.Soccer Statistics Foundation "
          "(RSSSF), “Malawi - List of International Matches”")

# RSSSF's stage suffixes -> a stage code. qf/sf/3p/final are adapt.STAGE_ORDER's
# own; r1/r2 are "Rd 1"/"Rd 2", which RSSSF uses for a group stage AND for a
# first knockout round (COSAFA 2007 went to penalties in its "Rd 1"), so they
# say "first round" and nothing more.
STAGES = {"Rd 1": "r1", "Rd 2": "r2", "Qf": "qf", "Sf": "sf",
          "3-4": "3p", "Final": "final"}

# The day Malawi became independent. RSSSF prints the six matches before it
# under their own heading; every one is also '*'.
INDEPENDENCE = "1964-07-06"


class ParseError(Exception):
    """The page or the mapping cannot be turned into rows. Nothing is written."""


# ── Reading the page ─────────────────────────────────────────────────────────

def page_lines(raw_bytes):
    """The match list's <pre> block as text lines, wrapped lines rejoined."""
    raw = raw_bytes.decode("latin-1")
    found = re.search(r"<pre>(.*?)</pre>", raw, re.S | re.I)
    if not found:
        raise ParseError("no <pre> block on the page")
    text = html.unescape(re.sub(r"<[^>]*>", "", found.group(1)))
    lines = text.replace("\r\n", "\n").replace("\r", "\n").split("\n")
    # A long scorer list wraps onto a line of its own, indented to the
    # SCORERS column (31/05/08 v Djibouti, 8-1). Rejoin it, or seven goals
    # vanish and the Djibouti row of the table says so.
    out = []
    for line in lines:
        if out and re.match(r"^\s{20,}\S", line) and "[" in out[-1] \
                and not out[-1].rstrip().endswith("]"):
            out[-1] = out[-1].rstrip() + line.strip()
        else:
            out.append(line)
    return out


ROW = re.compile(
    r"^(?P<date>[\d-]{2}/[\d-]{2}/\d{2}) (?P<opp>.+?)\s*\[(?P<venue>[^\]]{3})\]"
    r" (?P<res>[WDLwdl]) (?P<gf>\d+)-(?P<ga>\d+)\s*\[(?P<ht>[^\]]*)\](?P<rest>.*)$")
REST = re.compile(
    r"^\s*(?:\[(?P<pen>[^\]]*)\])?\s*(?P<aet>AET)?\s*(?P<crowd>\d+|-)?"
    r"\s+(?P<comp>[^\[]*?)\s*(?:\[(?P<scorers>[^\]]*)\])?\s*$")
STAGE_SUFFIX = re.compile(r"\s+(" + "|".join(re.escape(s) for s in STAGES) + r")$")


def parse_date(text, line):
    """'18/11/25' -> ('2025-11-18', 2025); '--/--/57' -> ('', 1957)."""
    d, m, y = text.split("/")
    year = int(y) + (1900 if int(y) >= 50 else 2000)
    if "-" in d or "-" in m:
        if d != "--" or m != "--":
            raise ParseError(f"half a date: {line!r}")
        return "", year
    try:
        return date_cls(year, int(m), int(d)).isoformat(), year
    except ValueError:
        raise ParseError(f"not a date: {line!r}") from None


# One scorer entry, after splitting on '>'. The forms on the page:
#   R.Mbulu 5 · G.Mhango 10Pen,34Pen · G.Mhango 32,72,90Pen · Z.Kalima 90+1
#   M.Gaolaolwe 80 OG · Y.Osman x3 · Y.Osman x2-1Pen (two, one a penalty)
#   J.Nkhwazi 2H (second half, no minute) · E.Gondwe pen · S.Chibambo
SCORER = re.compile(
    r"^(?P<name>[^\d]+?)\s*"
    r"(?:x(?P<times>\d+)(?:-(?P<times_pen>\d+)Pen)?|(?P<mins>\d[\dHPen+,]*))?"
    r"\s*(?P<og>OG)?\s*(?P<pen>pen)?$")
MINUTE = re.compile(r"^(?P<min>\d+)(?:\+(?P<stop>\d+))?(?P<pen>Pen)?$")


def display_name(printed):
    """'G.Mhango' -> 'G. Mhango' — RSSSF's columns have no room for the space."""
    return re.sub(r"\.(?=\S)", ". ", printed.strip())


def _period(minute, stoppage, extra_time):
    if minute <= 45:
        return "1h"
    if minute <= 90 or not extra_time:
        return "2h"
    return "et"


def parse_scorers(text, extra_time, line):
    """'[G.Mhango 43>G.Phiri 48Pen]' -> goal dicts, in the order printed.

    An empty entry — '[]', or a trailing '>' as in 'K.Phiri x4>' for a 7-1 —
    is RSSSF saying a scorer is not known. Those goals get no row: an
    unidentified goal is the score minus the rows, which is exactly the
    figure RSSSF prints at the bottom of the page.
    """
    goals = []
    if not text or not text.strip():
        return goals
    # '>' separates scorers; once (CECAFA 1992) a comma does, and a comma
    # also separates one scorer's minutes ('43,58'), so only ', <capital>'
    # counts as a new scorer.
    for part in re.split(r">|,\s+(?=[A-Z])", text):
        part = part.strip()
        if not part:
            continue
        m = SCORER.match(part)
        if not m:
            raise ParseError(f"cannot read scorer {part!r} in {line!r}")
        name = display_name(m["name"])
        og = "own goal" if m["og"] else ""
        if m["times"]:
            n, pens = int(m["times"]), int(m["times_pen"] or 0)
            for i in range(n):
                goals.append(_goal(name, "", "", "",
                                   og or ("penalty" if i < pens else "")))
        elif m["mins"]:
            for item in m["mins"].split(","):
                if item in ("1H", "2H"):
                    goals.append(_goal(name, "", "", item.lower(), og))
                    continue
                mm = MINUTE.match(item)
                if not mm:
                    raise ParseError(f"cannot read minute {item!r} in {line!r}")
                minute = int(mm["min"])
                stoppage = mm["stop"] or ""
                goals.append(_goal(
                    name, str(minute), stoppage,
                    _period(minute, stoppage, extra_time),
                    og or ("penalty" if mm["pen"] else "")))
        else:
            goals.append(_goal(name, "", "", "",
                               og or ("penalty" if m["pen"] else "")))
    return goals


def _goal(name, minute, stoppage, period, goal_type):
    return {"player_name": name, "minute": minute, "stoppage": stoppage,
            "period": period, "goal_type": goal_type}


def parse_matches(lines):
    """Every match line -> a raw dict. Fails on any line it cannot read."""
    matches = []
    pre_independence = False
    for line in lines:
        if line.startswith("PRE-INDEPENDENCE RESULTS"):
            pre_independence = True
            continue
        if not re.match(r"^[\d-]{2}/[\d-]{2}/\d{2} ", line):
            continue
        m = ROW.match(line)
        r = REST.match(m["rest"]) if m else None
        if not (m and r):
            raise ParseError(f"cannot read line {line!r}")
        comp = r["comp"].strip()
        unofficial = comp.endswith("*")
        comp = comp.rstrip("*").strip()
        stage = ""
        s = STAGE_SUFFIX.search(comp)
        if s:
            stage, comp = STAGES[s.group(1)], comp[:s.start()].strip()
        iso, year = parse_date(m["date"], line)

        pen = (r["pen"] or "").strip()
        shootout, abandoned = "", None
        if re.fullmatch(r"\d+-\d+", pen):
            shootout = pen
        elif re.fullmatch(r"a\d+|\d+a", pen):
            abandoned = int(pen.strip("a"))
        elif pen:
            raise ParseError(f"unknown [PEN] value {pen!r} in {line!r}")
        ht = m["ht"].strip()
        # An abandonment after 90' (Zimbabwe, 1988, at 107') was in extra
        # time whether or not the line says AET.
        extra_time = bool(r["aet"]) or (abandoned or 0) > 90
        gf, ga = int(m["gf"]), int(m["ga"])
        matches.append({
            "line": line.rstrip(),
            "date": iso, "year": year,
            "printed_opponent": m["opp"].strip(),
            "venue_code": m["venue"],
            "result_letter": m["res"].upper(),
            "team_score": gf, "opponent_score": ga,
            # A half-time score RSSSF only half knows ('1/-') is no score.
            "half_time": ht if re.fullmatch(r"\d+-\d+", ht) else "",
            "extra_time": extra_time,
            "shootout": shootout,
            "abandoned_minute": abandoned,
            "crowd": int(r["crowd"]) if r["crowd"] and r["crowd"] != "-" else None,
            "competition_code": comp,
            "stage": stage,
            "unofficial": unofficial,
            "pre_independence": pre_independence,
            "goals": parse_scorers(r["scorers"], extra_time, line),
        })
    if not matches:
        raise ParseError("no match lines found")
    return matches


# ── RSSSF's own tables ───────────────────────────────────────────────────────

COUNTRY_ROW = re.compile(
    r"^(?P<name>\S.*?)\s+(?P<p>\d+)\s+(?P<w>\d+)\s+(?P<d>\d+)\s+(?P<l>\d+)"
    r"\s+(?P<f>\d+)\s+(?P<a>\d+)\s+(?P<last5>[WDL-]{5})\s+(?P<year>\d{4})\s*\|?$")
TOTAL_ROW = re.compile(
    r"^TOTAL\s+(?P<p>\d+)\s+(?P<w>\d+)\s+(?P<d>\d+)\s+(?P<l>\d+)"
    r"\s+(?P<f>\d+)\s+(?P<a>\d+)")
SUMMARY_ROW = re.compile(
    r"^(?P<name>World Cup Qualifiers|Nations Cup Qualifiers|Nations Cup Finals)"
    r"\s+(?P<p>\d+)\s+(?P<w>\d+)\s+(?P<d>\d+)\s+(?P<l>\d+)\s+(?P<f>\d+)\s+(?P<a>\d+)")
UNIDENTIFIED = re.compile(r"From (\d+) Goals scored, (\d+) goalscorers")

_FIGURES = ("p", "w", "d", "l", "f", "a")


def parse_tables(lines):
    """RSSSF's by-country table, its total, competitive summary and the
    unidentified-scorer count, exactly as printed."""
    countries, total, summary, unidentified = {}, None, {}, None
    in_countries = False
    for line in lines:
        if line.startswith("OVERALL [by Country]"):
            in_countries = True
            continue
        if in_countries:
            t = TOTAL_ROW.match(line)
            if t:
                total = {k: int(t[k]) for k in _FIGURES}
                in_countries = False
                continue
            c = COUNTRY_ROW.match(line)
            if c and c["name"] != "COUNTRY":
                countries[c["name"].strip()] = dict(
                    {k: int(c[k]) for k in _FIGURES}, year=int(c["year"]))
            continue
        s = SUMMARY_ROW.match(line)
        if s:
            summary[s["name"]] = {k: int(s[k]) for k in _FIGURES}
        u = UNIDENTIFIED.search(line)
        if u:
            unidentified = {"goals": int(u.group(1)), "unidentified": int(u.group(2))}
    if not countries or total is None or not summary or unidentified is None:
        raise ParseError("RSSSF's summary tables are missing or changed shape")
    return {"countries": countries, "total": total, "summary": summary,
            "unidentified": unidentified}


# ── Names ────────────────────────────────────────────────────────────────────

def load_names(path=NAMES_PATH):
    with open(path, encoding="utf-8") as fh:
        return json.load(fh)


def venue_country(names, code, iso):
    entry = names["venues"][code]
    if isinstance(entry, str):
        return entry
    for until, earlier in sorted(entry.get("before", {}).items()):
        if iso and iso < until:
            return earlier
    return entry["name"]


def unmapped(raw, names):
    """Every printed name / code the names file does not cover."""
    missing = set()
    for m in raw:
        if m["printed_opponent"].casefold() not in names["opponents"]:
            missing.add(f"opponent {m['printed_opponent']!r}")
        if m["venue_code"] not in names["venues"]:
            missing.add(f"venue code {m['venue_code']!r}")
        if m["competition_code"] not in names["competitions"]:
            missing.add(f"competition code {m['competition_code']!r}")
    return sorted(missing)


def resolve(raw, names):
    """Raw page rows -> rows in nt_matches' vocabulary (plus the new fields)."""
    corrections = {c["line"]: c for c in names.get("corrections", [])}
    out = []
    for m in raw:
        opp = names["opponents"][m["printed_opponent"].casefold()]
        code = m["venue_code"]
        if code == "MWL":
            home_away, neutral = "home", False
        elif code in opp["home"]:
            home_away, neutral = "away", False
        else:
            # Neither side's ground. RSSSF does not say which side was the
            # designated home team, so this says nothing either; the loader
            # decides what nt_matches' required home_away holds for it.
            home_away, neutral = None, True

        gf, ga = m["team_score"], m["opponent_score"]
        shootout_result = ""
        if m["shootout"]:
            ours, theirs = (int(x) for x in m["shootout"].split("-"))
            shootout_result = "win" if ours > theirs else "loss"

        row = {
            "date": m["date"],
            "year": m["year"],
            "opponent": opp["name"],
            "opponent_country": opp["country"],
            "home_away": home_away,
            "neutral": neutral,
            "country": venue_country(names, code, m["date"]),
            "competition": names["competitions"][m["competition_code"]],
            "stage": m["stage"],
            "team_score": gf,
            "opponent_score": ga,
            "status": "abandoned" if m["abandoned_minute"] is not None else "played",
            "abandoned_minute": m["abandoned_minute"],
            "half_time": m["half_time"],
            "extra_time": m["extra_time"],
            "penalty_shootout": bool(m["shootout"]),
            "shootout_score": m["shootout"],
            "extra_time_result": shootout_result,
            "crowd": m["crowd"],
            "unofficial": m["unofficial"],
            "pre_independence": m["pre_independence"],
            "goals": m["goals"],
            "unidentified_goals": gf - len(m["goals"]),
            "rsssf": {"line": m["line"], "competition_code": m["competition_code"],
                      "venue_code": code},
        }
        fix = next((c for k, c in corrections.items() if m["line"].startswith(k)), None)
        if fix:
            row.update(fix.get("set", {}))
            row["unidentified_goals"] = row["team_score"] - len(row["goals"])
            row["rsssf"]["correction"] = fix["reason"]
        out.append(row)
    return out


# ── The check ────────────────────────────────────────────────────────────────

def _outcome(gf, ga):
    return "w" if gf > ga else "l" if gf < ga else "d"


def _tally(rows):
    t = {k: 0 for k in _FIGURES}
    for r in rows:
        t["p"] += 1
        t[_outcome(r["team_score"], r["opponent_score"])] += 1
        t["f"] += r["team_score"]
        t["a"] += r["opponent_score"]
    return t


def check(rows, tables, names):
    """Every way the parse can disagree with RSSSF's own figures. [] = agrees."""
    errors = []
    official = [r for r in rows if not r["unofficial"]]

    # 1. Each line's result letter against its score. RSSSF writes both, so a
    #    disagreement is a typo in one of them, and only the table can say which.
    for r in rows:
        letter = r["rsssf"]["line"].split("]", 1)[1].split()[0].lower()
        if letter != _outcome(r["team_score"], r["opponent_score"]) \
                and "correction" not in r["rsssf"]:
            errors.append(f"result letter {letter!r} contradicts the score: "
                          f"{r['rsssf']['line']}")

    # 2. More scorers than goals is a misread, never a fact.
    for r in rows:
        if r["unidentified_goals"] < 0:
            errors.append(f"{len(r['goals'])} scorers for {r['team_score']} goals: "
                          f"{r['rsssf']['line']}")

    # 3-5. RSSSF's summary tables. Unlike a season's final table these are
    #    kept by hand over decades and do not agree with themselves — the
    #    country rows add up to D199 L249 F790 A838, the TOTAL line under them
    #    says D201 L247 F793 A840 — so a strict match is impossible. Every
    #    difference that remains is instead PINNED in nt_names.json
    #    `table_differences`, with both sides' figures and a reason; a new
    #    difference, or a pinned one whose figures move, still fails. That
    #    keeps the check's real job — catching a misread line, a lost wrap, a
    #    wrong '*' — without pretending RSSSF's arithmetic is the answer key.
    pinned = names.get("table_differences", {})
    seen_pins = set()

    def compare(label, got, want, keys=_FIGURES):
        if all(got[k] == want[k] for k in keys):
            return
        lines_fig = " ".join(str(got[k]) for k in keys)
        rsssf_fig = " ".join(str(want[k]) for k in keys)
        pin = pinned.get(label)
        seen_pins.add(label)
        if pin is None:
            errors.append(f"{label}: lines {lines_fig}, RSSSF {rsssf_fig} "
                          f"({' '.join(k.upper() for k in keys)}) — unexplained")
        elif (pin.get("lines"), pin.get("rsssf")) != (lines_fig, rsssf_fig):
            errors.append(f"{label}: pinned as lines {pin.get('lines')} / RSSSF "
                          f"{pin.get('rsssf')}, now lines {lines_fig} / RSSSF "
                          f"{rsssf_fig}")

    by_country = defaultdict(list)
    for r in official:
        by_country[r["opponent_country"].casefold()].append(r)
    table_names = names.get("table_names", {})
    seen = set()
    for printed, want in tables["countries"].items():
        key = table_names.get(printed, printed).casefold()
        seen.add(key)
        mine = by_country.get(key, [])
        got = _tally(mine)
        got["year"] = max((r["year"] for r in mine), default="-")
        compare(printed, got, want, (*_FIGURES, "year"))
    for key in sorted(set(by_country) - seen):
        errors.append(f"{key}: {len(by_country[key])} official matches, "
                      "no row in RSSSF's table")

    compare("TOTAL", _tally(official), tables["total"])

    competitive = names["competitive"]
    for line_name, want in tables["summary"].items():
        compare(line_name, _tally(
            [r for r in official
             if competitive.get(r["rsssf"]["competition_code"]) == line_name]), want)

    for label in sorted(set(pinned) - seen_pins):
        errors.append(f"{label}: pinned in table_differences but now agrees — "
                      "remove the pin")

    # 6. Unidentified scorers: the scorer lists, read right, leave exactly the
    #    goals RSSSF says it cannot name.
    #    The goals total beside it is the TOTAL line's, checked above.
    want = tables["unidentified"]["unidentified"]
    got_unknown = sum(r["unidentified_goals"] for r in official)
    if got_unknown != want:
        errors.append(f"unidentified scorers: {got_unknown}, RSSSF says {want}")
    return errors


# ── What the database already holds ──────────────────────────────────────────

def snapshot_rows(path=EXISTING_PATH):
    """nt_matches as data/canonical/ last saw it — what `parse` compares with."""
    if not os.path.exists(path):
        return []
    with open(path, newline="", encoding="utf-8") as fh:
        return list(csv.DictReader(fh))


def reconcile(rows, db_rows, window_days=10):
    """Pair each existing MW_M result with its RSSSF line, and say how they differ.

    Paired by opponent and score within `window_days`, not by date alone:
    the date is one of the things that may be wrong (all three 2025 COSAFA
    rows were entered as 5 June). `db_rows` are nt_matches rows, from the
    snapshot or the live table. Returns (pairs, only_db, only_rsssf_since).
    """
    db = [r for r in db_rows
          if r["team_code"] == TEAM_CODE
          and r["status"] in ("played", "awarded", "abandoned")
          and (re.fullmatch(r"\d{4}-\d{2}-\d{2}", str(r.get("date") or ""))
               or str(r.get("date_year") or "").isdigit())]

    def days(a, b):
        return abs((date_cls.fromisoformat(a) - date_cls.fromisoformat(b)).days)

    def when(d):
        """A DB row's date, or Jan 1 of its year: the one undated archive
        match (1957) pairs with itself by year and nothing else."""
        return d.get("date") or f"{int(d['date_year']):04d}-01-01"

    def rsssf_when(r):
        return r["date"] or f"{r['year']:04d}-01-01"

    def same_team(db_name, row):
        return db_name.strip().casefold() in (row["opponent"].casefold(),
                                              row["opponent_country"].casefold())

    pairs, only_db, used = [], [], set()
    for d in sorted(db, key=when):
        options = [(days(when(d), rsssf_when(r)), i) for i, r in enumerate(rows)
                   if i not in used and bool(r["date"]) == bool(d.get("date"))
                   and same_team(d["opponent"], r)
                   and days(when(d), rsssf_when(r)) <= window_days]
        if not options:
            only_db.append(d)
            continue
        # Prefer the line with the same score, then the nearest date.
        options.sort(key=lambda o: (
            (rows[o[1]]["team_score"], rows[o[1]]["opponent_score"])
            != (int(d["team_score"]), int(d["opponent_score"])), o[0]))
        i = options[0][1]
        used.add(i)
        r = rows[i]
        diffs = []
        if (d.get("date") or "") != r["date"]:
            diffs.append(f"date {d['date']} v RSSSF {r['date']}")
        if (int(d["team_score"]), int(d["opponent_score"])) != (r["team_score"],
                                                               r["opponent_score"]):
            diffs.append(f"score {d['team_score']}-{d['opponent_score']} v RSSSF "
                         f"{r['team_score']}-{r['opponent_score']}")
        if d["status"] != r["status"]:
            diffs.append(f"status {d['status']} v RSSSF {r['status']}")
        if d["competition"] != r["competition"]:
            diffs.append(f"competition {d['competition']!r} v {r['competition']!r}")
        r["existing_match_id"] = d["match_id"]
        pairs.append((d, r, diffs))

    earliest = min((d["date"] for d in db if d.get("date")), default="")
    only_rsssf = [r for i, r in enumerate(rows)
                  if i not in used and earliest and r["date"] >= earliest]
    return pairs, only_db, only_rsssf


# ── Commands ─────────────────────────────────────────────────────────────────

def cmd_fetch(_args):
    os.makedirs(DATA_DIR, exist_ok=True)
    req = urllib.request.Request(URL, headers={"User-Agent": "everyleague-backfill"})
    with urllib.request.urlopen(req, timeout=30) as resp:
        body = resp.read()
    with open(os.path.join(DATA_DIR, PAGE), "wb") as fh:
        fh.write(body)
    print(f"  {PAGE}  {len(body)} bytes")
    return 0


def parse_page(raw_bytes, names):
    """(rows, tables, errors) for a page; raises ParseError on an unmapped name."""
    lines = page_lines(raw_bytes)
    raw = parse_matches(lines)
    missing = unmapped(raw, names)
    if missing:
        raise ParseError("not in nt_names.json:\n    " + "\n    ".join(missing))
    rows = resolve(raw, names)
    tables = parse_tables(lines)
    return rows, tables, check(rows, tables, names)


def write_review(rows, path):
    """One match per line: a re-parse diffs a line per changed match, and the
    file is half the size of an indented one. Still a single JSON document."""
    head = json.dumps({
        "_about": "Review file from scripts/rsssf_nt.py parse; not loaded "
                  "anywhere yet. Newest first, as RSSSF prints it. Every match "
                  "is team_code's and its source_ref is source.",
        "source": URL, "credit": CREDIT, "team_code": TEAM_CODE,
    }, ensure_ascii=False)
    body = ",\n".join(json.dumps(r, ensure_ascii=False) for r in rows)
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(head[:-1] + ', "matches": [\n' + body + "\n]}\n")


def cmd_parse(_args):
    with open(os.path.join(DATA_DIR, PAGE), "rb") as fh:
        rows, tables, errors = parse_page(fh.read(), load_names())
    if errors:
        print("The parsed lines do not add up to RSSSF's own tables:")
        for e in errors:
            print(f"    {e}")
        return 1

    pairs, only_db, only_rsssf = reconcile(rows, snapshot_rows())
    official = [r for r in rows if not r["unofficial"]]
    print(f"{len(rows)} matches ({len(official)} official, "
          f"{len(rows) - len(official)} unofficial), "
          f"{sum(len(r['goals']) for r in rows)} scorer rows; "
          f"RSSSF's by-country table, total, competitive summary and "
          f"unidentified-scorer count agree, but for "
          f"{len(load_names().get('table_differences', {}))} pinned differences.")
    print(f"\nAlready in nt_matches ({len(pairs)} paired):")
    for d, r, diffs in pairs:
        print(f"  #{d['match_id']:>3} {d['date']} {d['opponent']:24} "
              + ("agrees" if not diffs else "; ".join(diffs)))
    for d in only_db:
        print(f"  #{d['match_id']:>3} {d['date']} {d['opponent']:24} "
              "NOT ON RSSSF")
    if only_rsssf:
        print(f"\nOn RSSSF, not in nt_matches, since the first stored result:")
        for r in only_rsssf:
            print(f"  {r['date']} {r['opponent']:24} "
                  f"{r['team_score']}-{r['opponent_score']}  {r['competition']}")

    write_review(rows, OUT_PATH)
    print(f"\n-> {os.path.relpath(OUT_PATH, ROOT)}")
    return 0


# The archive-only fields `apply` fills on a paired row where it is blank.
_FILL = ("stage", "half_time", "crowd", "shootout_score", "opponent_country")


def _blank(v):
    return v is None or v == ""


def match_row(r, match_id):
    """A review row -> an nt_matches insert. Every row carries the same keys,
    which PostgREST requires of a bulk insert."""
    return {
        "match_id": match_id, "team_code": TEAM_CODE,
        "date": r["date"], "date_year": None if r["date"] else r["year"],
        "competition": r["competition"], "stage": r["stage"],
        "opponent": r["opponent"],
        # Blank means "the same as opponent" (0066's comment on the column).
        "opponent_country": ("" if r["opponent_country"] == r["opponent"]
                             else r["opponent_country"]),
        "home_away": r["home_away"] or "away", "neutral": r["neutral"],
        "venue": "", "city": "", "country": r["country"], "kickoff": "",
        "team_score": r["team_score"], "opponent_score": r["opponent_score"],
        "status": r["status"], "abandoned_minute": r["abandoned_minute"],
        "half_time": r["half_time"], "crowd": r["crowd"],
        "extra_time": r["extra_time"],
        "penalty_shootout": "TRUE" if r["penalty_shootout"] else "FALSE",
        "shootout_score": r["shootout_score"],
        "extra_time_result": r["extra_time_result"],
        "unofficial": r["unofficial"],
        "coach": "", "source_type": "backfill", "source_ref": URL,
    }


def goal_rows(r, match_id, next_id):
    """Malawi's scorers for one match -> nt_goals inserts. Every one is OURS
    (RSSSF lists no opponent's scorers); an own goal is an opponent's player
    scoring for us, so team_id is still ours, which is the beneficiary rule."""
    out = []
    for g in r["goals"]:
        out.append({
            "goal_id": str(next_id + len(out)), "match_id": match_id,
            "team_id": TEAM_CODE, "player_name": g["player_name"],
            "player_id": "", "minute": g["minute"], "stoppage": g["stoppage"],
            "period": g["period"], "goal_type": g["goal_type"],
            "source_type": "backfill", "source_ref": URL,
        })
    return out


def plan_load(rows, live_matches, live_goals):
    """(inserts, goal_inserts, patches) — what `apply` would write.

    `live_matches` / `live_goals` are every nt_matches / nt_goals row, both
    teams: the next id is after the largest in the whole table.
    """
    pairs, _only_db, _ = reconcile(rows, live_matches)
    paired = {id(r) for _d, r, _diffs in pairs}
    goals_by_match = defaultdict(int)
    for g in live_goals:
        goals_by_match[g["match_id"]] += 1

    next_match = max((int(m["match_id"]) for m in live_matches), default=0) + 1
    next_goal = max((int(g["goal_id"]) for g in live_goals), default=0) + 1

    patches, goal_inserts = [], []
    for d, r, _diffs in pairs:
        patch = {}
        if r["date"] and d.get("date") != r["date"]:
            patch["date"] = r["date"]
        want = match_row(r, d["match_id"])
        for field in _FILL:
            if _blank(d.get(field)) and not _blank(want[field]):
                patch[field] = want[field]
        if patch:
            patches.append((d, patch))
        # Only an archive row of our own making gets RSSSF's scorers, and
        # only when it has none — the half of a failed earlier run.
        if d.get("source_type") == "backfill" and not goals_by_match[d["match_id"]]:
            new = goal_rows(r, d["match_id"], next_goal)
            next_goal += len(new)
            goal_inserts += new

    inserts = []
    # Oldest first, so ids (and `ord`) run with the calendar.
    for r in sorted((r for r in rows if id(r) not in paired),
                    key=lambda r: (r["date"] or f"{r['year']:04d}")):
        mid = str(next_match)
        next_match += 1
        inserts.append(match_row(r, mid))
        new = goal_rows(r, mid, next_goal)
        next_goal += len(new)
        goal_inserts += new
    return inserts, goal_inserts, patches


def _insert(table, rows, chunk=200):
    """A plain INSERT, never an upsert: an id that already exists must fail,
    not overwrite a match someone typed."""
    for start in range(0, len(rows), chunk):
        sb._request("POST", table, body=rows[start:start + chunk],
                    headers={"Prefer": "return=minimal"}, require_secret=True)


def cmd_apply(args):
    with open(os.path.join(DATA_DIR, PAGE), "rb") as fh:
        rows, _tables, errors = parse_page(fh.read(), load_names())
    if errors:
        print("The page no longer passes the check; run `parse` and fix it first.")
        return 1

    sb.load_dotenv(os.path.join(ROOT, ".env"))
    live_matches = sb.select("nt_matches", order="ord.asc", require_secret=True)
    live_goals = sb.select("nt_goals", columns="goal_id,match_id",
                           require_secret=True)
    if live_matches and "unofficial" not in live_matches[0]:
        print("nt_matches has no `unofficial` column: apply migration 0066 first.")
        return 1

    inserts, goal_inserts, patches = plan_load(rows, live_matches, live_goals)
    print(f"Plan: {len(inserts)} matches to insert "
          f"({sum(1 for m in inserts if m['unofficial'])} unofficial, "
          f"{sum(1 for m in inserts if m['status'] == 'abandoned')} abandoned), "
          f"{len(goal_inserts)} scorer rows, {len(patches)} existing matches "
          "to update.")
    if inserts:
        print(f"  match_id {inserts[0]['match_id']}..{inserts[-1]['match_id']}"
              f"  ({inserts[0]['date'] or inserts[0]['date_year']} .. "
              f"{inserts[-1]['date']})")
    if goal_inserts:
        print(f"  goal_id  {goal_inserts[0]['goal_id']}..{goal_inserts[-1]['goal_id']}")
    for d, patch in patches:
        print(f"  #{d['match_id']:>3} {d.get('date') or d.get('date_year')} "
              f"{d['opponent']:24} " + ", ".join(
                  f"{k} {d.get(k)!r} -> {v!r}" if k == "date" else f"{k}={v!r}"
                  for k, v in patch.items()))

    if args.dry_run:
        print("\n(dry run — nothing written. Drop --dry-run to apply.)")
        return 0
    _insert("nt_matches", inserts)
    print(f"  wrote {len(inserts):4} -> nt_matches")
    for d, patch in patches:
        sb._request("PATCH", "nt_matches",
                    query=f"match_id=eq.{d['match_id']}", body=patch,
                    headers={"Prefer": "return=minimal"}, require_secret=True)
    print(f"  updated {len(patches):2} nt_matches")
    _insert("nt_goals", goal_inserts)
    print(f"  wrote {len(goal_inserts):4} -> nt_goals")
    print("\nDone. The next build renders them; nudge one to see it now.")
    return 0


def main(argv=None):
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="command", required=True)
    for name, func, text in (("fetch", cmd_fetch, f"download {PAGE}"),
                             ("parse", cmd_parse, "check it and write the review file"),
                             ("apply", cmd_apply, "load it into nt_matches/nt_goals")):
        p = sub.add_parser(name, help=text)
        p.set_defaults(func=func)
        if name == "apply":
            p.add_argument("--dry-run", action="store_true",
                           help="print the plan and write nothing")
    args = parser.parse_args(argv)
    try:
        return args.func(args)
    except (ParseError, sb.SupabaseError) as err:
        print(f"ERROR: {err}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
