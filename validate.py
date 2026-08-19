#!/usr/bin/env python3
"""Validate the new-schema data; the first build step.

Fetches every league tab plus the seven national-team tabs (or reads
DATASET_LOCAL_DIR), runs every check, and:
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
import re
import sys

ROOT = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, ROOT)

from src import dataset, nt  # noqa: E402

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
    # One row per named player per side, so the key is the sheet plus the
    # player. team_id is in it because a league match has two real teams and
    # two clubs may field players who share a name.
    "lineups": ("match_id", "team_id", "player_name"),
    "trending": ("card_id",),
    "reporters": ("reporter_id",),
}

# The national-team tabs (src/nt.py). nt_squads and nt_lineups hold one row per
# player, so their key is the group plus the player; nt_competitions is one
# hand-maintained row per team per competition — ours plus one per rival in the
# same group. group_name stays out of the key: a team plays in one group of a
# competition, and a competition without groups leaves the column blank.
NT_PRIMARY_KEYS = {
    "nt_teams": ("team_code",),
    "nt_matches": ("match_id",),
    "nt_goals": ("goal_id",),
    "nt_squads": ("squad_id", "player_name"),
    "nt_competitions": ("team_code", "competition_name"),
    "nt_lineups": ("match_id", "player_name"),
    # One row per tie, so the tie's own id is the key; (stage, slot) uniqueness
    # within a competition is a separate check in check_nt.
    "nt_knockout": ("tie_id",),
}

# The identifier columns whose disappearance between fetches means someone
# deleted rows from the sheet (check 8).
DRIFT_IDS = {
    "matches": "match_id",
    "teams": "team_id",
    "clubs": "club_id",
    "players": "player_id",
    "nt_matches": "match_id",
    "nt_knockout": "tie_id",
}


def _non_blank_rows(text):
    """(row_number, {col: stripped}) per non-blank CSV row; header row is 1."""
    reader = csv.DictReader(io.StringIO(text))
    for i, raw in enumerate(reader, start=2):
        row = {(k or "").strip(): (v or "").strip() for k, v in raw.items() if k}
        if any(row.values()):
            yield i, row


def check_primary_keys(texts, keys=None):
    """Check 1: no duplicate and no blank primary keys, in any tab."""
    errors = []
    for tab, pk in (keys or PRIMARY_KEYS).items():
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
    for r in ds.lineups:
        lbl = f"lineups {r.match_id}/{r.team_id}/{r.player_name}"
        fk(lbl, "match_id", r.match_id, ds.matches)
        fk(lbl, "team_id", r.team_id, ds.teams)
        # Blank is the "nobody has identified them yet" state a reported
        # scorer already has, so it is allowed here for the same reason.
        fk(lbl, "player_id", r.player_id, ds.players, blank_ok=True)
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


def check_lineups(ds):
    """Check 10: a team sheet that a build can render.

    The single-row rules — the role vocabulary, a sub_on carrying a minute, the
    card states — are the database's job and 0018 does them with CHECK
    constraints. Everything here is the CROSS-ROW half a constraint cannot see,
    and it is the same list check_nt applies to nt_lineups plus the two rules
    that tab cannot state: a league sheet belongs to one of the two teams that
    actually played, and its player_id is a real players row (checked in check
    2 above).

    Why this is an ERROR and not a warning: src/lineups.py pairs a substitute
    to the starter they replaced BY NAME. A replaced_player naming nobody
    renders a substitution with a dangling name, and a twelfth starter renders
    a starting XI that is not one. Both are wrong on the page rather than
    merely untidy in the table.
    """
    errors = []
    by_side = {}
    # Man of the match is the one flag on this tab that spans both sides, so
    # it is counted per MATCH rather than per sheet — a unique index says the
    # same thing in Postgres (0028), and an imported row could still arrive
    # with two. Two stars in one match is not a rendering failure, which is
    # why this is here rather than in the renderer: it is a claim that two
    # different people won the same award.
    motm = {}
    for r in ds.lineups:
        m = ds.matches.get(r.match_id)
        if m is None:
            continue                       # already reported by check 2
        if r.team_id not in (m.home_team_id, m.away_team_id):
            errors.append(
                f"lineups {r.match_id}/{r.player_name}: team_id {r.team_id!r} "
                f"did not play in this match")
            continue
        by_side.setdefault((r.match_id, r.team_id), []).append(r)
        if r.motm:
            motm.setdefault(r.match_id, []).append(r.player_name)

    for mid, names in sorted(motm.items()):
        if len(names) > 1:
            errors.append(
                f"lineups {mid}: {len(names)} players marked man of the match "
                f"({', '.join(sorted(names))}) — the award is one per match")

    for (mid, tid), rows in sorted(by_side.items()):
        names = {r.player_name for r in rows}
        starting = [r for r in rows if r.role == "starting"]
        if len(starting) > 11:
            errors.append(
                f"lineups {mid}/{tid}: {len(starting)} starting rows "
                f"(a starting XI is 11)")
        for r in rows:
            # A second yellow IS a red. 0018 refuses the pair at write time;
            # an imported row could still carry it, and would render two card
            # markers for one dismissal.
            if r.yellow_red_card and r.red_card:
                errors.append(
                    f"lineups {mid}/{r.player_name}: yellow_red_card and "
                    f"red_card are both set (a second yellow IS a red)")
            if r.role != "sub_on":
                continue
            if not r.minute_on:
                errors.append(
                    f"lineups {mid}/{r.player_name}: role=sub_on but "
                    f"minute_on is blank")
            if r.replaced_player and r.replaced_player not in names:
                errors.append(
                    f"lineups {mid}/{r.player_name}: replaced_player "
                    f"{r.replaced_player!r} is not in this side's line-up")
    return errors


# A homepage card's link is the one value in this whole dataset that goes
# straight into an href on the site's most-visited page. Two forms are legal:
# a path on this site, or an https URL. `//host` and `/\host` are excluded on
# purpose — a browser reads both as protocol-relative and leaves the site,
# and both would pass a naive "starts with a slash" test.
# Written in the same character-class form as 0030's CHECK constraint, rather
# than the lookahead Python would allow, so the two copies of this rule can be
# compared character for character (tests/test_trending.py does exactly that).
_SAFE_LINK = re.compile(r"^(/([^/\\\s][^\s]*)?|https://[^\s]+)$")


def check_trending(ds):
    """Check 11: a homepage card that is safe to render (0030).

    Deliberately short, and deliberately about LIVE cards only.

    Short, because a card is an escaped string in a template: there is no
    cross-row arithmetic to get wrong, and every optional part already renders
    as nothing when it is missing. The lengths, the status vocabulary and the
    link scheme are all constrained in Postgres by 0030 — this is the same
    belt-and-braces re-check check 10 gives the man-of-the-match index, for
    the one field where being wrong is worse than being ugly.

    Live only, because of the rule this file exists to serve: an ERROR here
    stops every deploy for everyone. A draft is not on the site, so refusing to
    build the whole of everyleague.co over a link somebody typed into a card
    they have not published would be the validator causing exactly the outage
    it is meant to prevent. A draft's link is checked the moment it matters —
    the tap that publishes it goes through set_trending_status, and the row it
    publishes went through save_trending_card's identical test.
    """
    errors = []
    for card in sorted(ds.trending.values(), key=lambda c: c.card_id):
        if not card.is_live:
            continue
        if card.link_url and not _SAFE_LINK.match(card.link_url):
            errors.append(
                f"trending {card.card_id}: link_url {card.link_url!r} must be "
                f"a path on this site (/matches/) or an https:// URL")
    return errors


def check_nt(ntd):
    """Check 9: the national-team tabs are internally coherent.

    Deliberately narrow, because this schema records one team's perspective:
    `nt_matches.opponent` is a display name with no code to resolve, so the
    only checkable side is ours. Everything here mirrors a league check —
    FK resolution, score/status agreement, goal rows never exceeding the
    score — plus the line-up rules the page relies on.
    """
    errors = []
    for m in ntd.nt_matches.values():
        lbl = f"nt_matches {m.match_id}"
        if m.team_code not in ntd.nt_teams:
            errors.append(f"{lbl}: team_code {m.team_code!r} does not resolve")
        if (m.team_score is None) != (m.opponent_score is None):
            errors.append(f"{lbl}: only one of team_score/opponent_score present")
        elif m.status in ("played", "awarded") and not m.has_score:
            errors.append(f"{lbl}: status={m.status} but the score is blank")
        elif m.status == "scheduled" and m.has_score:
            errors.append(f"{lbl}: status=scheduled but a score is present")

    # Goal rows per match+side never exceed that side's score (fewer is fine —
    # incomplete scorer data is expected). Own-goal rows already credit the
    # benefiting side, so counting by team_id lines up with the score.
    per_side = {}
    for g in ntd.nt_goals.values():
        m = ntd.nt_matches.get(g.match_id)
        if m is None:
            errors.append(
                f"nt_goals {g.goal_id}: match_id {g.match_id!r} does not resolve")
            continue
        ours = g.team_id == m.team_code
        per_side[(m.match_id, ours)] = per_side.get((m.match_id, ours), 0) + 1
    for (mid, ours), n in sorted(per_side.items()):
        m = ntd.nt_matches[mid]
        side = m.team_code if ours else f"the opponent ({m.opponent})"
        if not m.has_score:
            errors.append(f"nt_matches {mid}: has {n} goal row(s) but no score")
            continue
        score = m.team_score if ours else m.opponent_score
        if n > score:
            errors.append(
                f"nt_matches {mid}: {n} goal rows for {side} exceed its "
                f"score of {score}"
            )

    # A group table holds our row plus one per rival. Ours resolves to nt_teams;
    # a rival's code is a local label (NIGERIA_W) with no row to resolve to, so
    # it must carry its own team_name instead — otherwise it would render
    # nameless. A group with no row of ours at all is orphaned: nothing links it
    # to a page, so it would silently never appear.
    groups = {}
    for c in ntd.nt_competitions:
        lbl = f"nt_competitions {c.team_code!r}/{c.competition_name!r}"
        ours = c.team_code in ntd.nt_teams
        groups.setdefault(c.group_key, []).append(ours)
        if not ours and not c.team_name:
            errors.append(
                f"{lbl}: team_code does not resolve, so the row needs a "
                f"team_name (rival rows in a group table have no nt_teams row)")
    for (competition_name, group_name), flags_ in sorted(groups.items()):
        if not any(flags_):
            where = f"{competition_name!r}"
            if group_name:
                where += f" {group_name!r}"
            errors.append(
                f"nt_competitions {where}: no row for a team in nt_teams, so "
                f"this group table belongs to no page")

    # A knockout bracket hangs off the competition its group table names, so a
    # competition_name that matches none is orphaned exactly as a group with no
    # team of ours is — it would render nowhere, silently.
    comp_names = {c.competition_name for c in ntd.nt_competitions}
    ko_by_id = {t.tie_id: t for t in ntd.nt_knockout}
    slots = {}
    for t in ntd.nt_knockout:
        lbl = f"nt_knockout {t.tie_id}"
        if t.competition_name not in comp_names:
            errors.append(
                f"{lbl}: competition_name {t.competition_name!r} matches no "
                f"nt_competitions row, so this bracket belongs to no page")
        slots.setdefault((t.competition_name, t.stage, t.slot), []).append(t.tie_id)

        # A slot fed by a tie that does not exist would render a blank forever,
        # and one fed by itself would never resolve.
        for side in ("home", "away"):
            feed = t.feed(side)
            if feed is None:
                continue
            source = ko_by_id.get(feed[1])
            if source is None:
                errors.append(f"{lbl}: {side}_from names tie {feed[1]!r}, "
                              f"which does not exist")
            elif source.tie_id == t.tie_id:
                errors.append(f"{lbl}: {side}_from feeds the tie from itself")
            elif source.competition_name != t.competition_name:
                errors.append(
                    f"{lbl}: {side}_from names tie {feed[1]!r} in a different "
                    f"competition ({source.competition_name!r})")

        if (t.home_score is None) != (t.away_score is None):
            errors.append(f"{lbl}: only one of home_score/away_score present")
        elif t.status in ("played", "awarded") and not (t.has_score or t.nt_match_id):
            errors.append(f"{lbl}: status={t.status} but the score is blank")
        elif t.status == "scheduled" and t.has_score:
            errors.append(f"{lbl}: status=scheduled but a score is present")

        # Shootouts, on the same terms cups get (see check_cup_rules): in
        # pairs, never level, and only when 90/120 minutes could not separate.
        if (t.home_pens is None) != (t.away_pens is None):
            errors.append(f"{lbl}: only one of home_pens/away_pens present")
        elif t.home_pens is not None:
            if t.home_pens == t.away_pens:
                errors.append(f"{lbl}: a shootout cannot end level "
                              f"({t.home_pens}-{t.away_pens})")
            if t.has_score and t.home_score != t.away_score:
                errors.append(
                    f"{lbl}: pens recorded but the score {t.home_score}-"
                    f"{t.away_score} is not level")

        # Our own ties live in both tabs. nt_matches is the authority on what
        # happened, so the copies here must stay empty or the two can disagree
        # with nothing to say which is right.
        if t.nt_match_id:
            m = ntd.nt_matches.get(t.nt_match_id)
            if m is None:
                errors.append(f"{lbl}: nt_match_id {t.nt_match_id!r} does not "
                              f"resolve")
            elif nt._norm(m.opponent) not in (nt._norm(t.home_name),
                                              nt._norm(t.away_name)):
                errors.append(
                    f"{lbl}: neither side names the opponent of nt_matches "
                    f"{t.nt_match_id} ({m.opponent!r}), so the tie cannot be "
                    f"matched up to it")
            # The result itself must be entered once, in nt_matches: a score
            # here would show in the bracket and nowhere else.
            owned = [c for c, v in (("home_score", t.home_score),
                                    ("away_score", t.away_score),
                                    ("home_pens", t.home_pens),
                                    ("away_pens", t.away_pens))
                     if v is not None]
            if owned:
                errors.append(
                    f"{lbl}: nt_match_id is set, so nt_matches {t.nt_match_id} "
                    f"owns the result — leave {', '.join(owned)} blank here")

            # Fixture details are fine to repeat — a bracket is a natural place
            # to write the venue down — as long as they agree. Only a
            # contradiction is an error, because then nothing says which is
            # right (nt_matches wins, so the sheet would be lying to itself).
            if m is not None:
                for col, here, there in (("date", t.date, m.date),
                                         ("kickoff", t.kickoff, m.kickoff),
                                         ("venue", t.venue, m.venue),
                                         ("city", t.city, m.city)):
                    if here and there and here != there:
                        errors.append(
                            f"{lbl}: {col} {here!r} contradicts nt_matches "
                            f"{t.nt_match_id} ({there!r}), which wins")
    for key, ids in sorted(slots.items()):
        if len(ids) > 1:
            competition_name, stage, slot = key
            errors.append(
                f"nt_knockout {', '.join(sorted(ids))}: {competition_name!r} "
                f"has {len(ids)} ties in {stage} slot {slot} — they would "
                f"overlay in the bracket")

    for s in ntd.nt_squads:
        if s.team_id not in ntd.nt_teams:
            errors.append(
                f"nt_squads {s.squad_id}/{s.player_name}: team_id "
                f"{s.team_id!r} does not resolve")

    # Line-ups: one XI per match+side, substitutions that name a real player.
    # team_id is deliberately NOT resolved: like nt_goals, a line-up row may
    # belong to the opponent (NIGERIA_W), which has no nt_teams row to hit.
    by_match = {}
    nt_motm = {}
    for r in ntd.nt_lineups:
        if r.match_id not in ntd.nt_matches:
            errors.append(
                f"nt_lineups {r.match_id}/{r.player_name}: match_id "
                f"{r.match_id!r} does not resolve")
            continue
        by_match.setdefault((r.match_id, r.team_id), []).append(r)
        if r.motm:
            nt_motm.setdefault(r.match_id, []).append(r.player_name)

    # One star per match, the same rule check_lineups applies to the league tab.
    for mid, names in sorted(nt_motm.items()):
        if len(names) > 1:
            errors.append(
                f"nt_lineups {mid}: {len(names)} players marked man of the "
                f"match ({', '.join(sorted(names))}) — the award is one per match")

    for (mid, tid), rows in sorted(by_match.items()):
        names = {r.player_name for r in rows}
        starting = [r for r in rows if r.role == "starting"]
        if len(starting) > 11:
            errors.append(
                f"nt_lineups {mid}/{tid}: {len(starting)} starting rows "
                f"(a starting XI is 11)")
        for r in rows:
            if r.role != "sub_on":
                continue
            if not r.minute_on:
                errors.append(
                    f"nt_lineups {mid}/{r.player_name}: role=sub_on but "
                    f"minute_on is blank")
            if r.replaced_player and r.replaced_player not in names:
                errors.append(
                    f"nt_lineups {mid}/{r.player_name}: replaced_player "
                    f"{r.replaced_player!r} is not in this match's line-up")
    return errors


def check_drift(texts, canonical_dir):
    """Check 8: every ID present in the previous snapshot must still exist.

    Catches accidental row deletion in the sheet. First run (no snapshot yet)
    passes vacuously.
    """
    errors = []
    for tab, id_col in DRIFT_IDS.items():
        path = os.path.join(canonical_dir, f"{tab}.csv")
        if tab not in texts or not os.path.exists(path):
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


def validate(texts, canonical_dir=CANONICAL_DIR, allow_deletions=False,
             nt_texts=None):
    """Run every check; returns (dataset_or_None, [error strings]).

    `nt_texts` (the six nt_* tabs) is optional: when given, the national-team
    checks run too and its rows join the drift baseline. The two schemas are
    validated independently — they share no ids — but a failure in either
    aborts the build, so a broken sheet can never deploy a partial site.
    """
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
        errors += check_lineups(ds)
        errors += check_trending(ds)
        try:
            ds.active_season()
        except dataset.DataError as err:
            errors.append(str(err))

    if nt_texts is not None:
        errors += check_primary_keys(nt_texts, keys=NT_PRIMARY_KEYS)
        try:
            errors += check_nt(nt.parse_all(nt_texts))
        except dataset.DataError as err:
            errors.append(str(err))

    # Drift works on the raw texts, so it runs even when parsing failed.
    if not allow_deletions:
        errors += check_drift({**texts, **(nt_texts or {})}, canonical_dir)
    return ds, errors


def write_snapshot(texts, canonical_dir=CANONICAL_DIR, nt_texts=None):
    """Persist the validated fetch as the new drift baseline / audit log."""
    os.makedirs(canonical_dir, exist_ok=True)
    tabs = list(dataset.TABS) + (list(dataset.NT_TABS) if nt_texts else [])
    both = {**texts, **(nt_texts or {})}
    for tab in tabs:
        with open(os.path.join(canonical_dir, f"{tab}.csv"), "w",
                  encoding="utf-8", newline="") as fh:
            fh.write(both[tab])


def main(argv):
    allow_deletions = "--allow-deletions" in argv
    snapshot = "--no-snapshot" not in argv

    try:
        texts = dataset.fetch_all()
        nt_texts = dataset.fetch_nt_all()
    except OSError as err:
        print(f"ERROR: could not fetch data: {err}", file=sys.stderr)
        return 1

    _ds, errors = validate(texts, allow_deletions=allow_deletions,
                           nt_texts=nt_texts)
    if errors:
        print(f"VALIDATION FAILED — {len(errors)} error(s):", file=sys.stderr)
        for e in errors:
            print(f"  ERROR: {e}", file=sys.stderr)
        return 1

    if snapshot:
        write_snapshot(texts, nt_texts=nt_texts)
        print(f"Validation OK — snapshot written to {CANONICAL_DIR}/")
    else:
        print("Validation OK")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
