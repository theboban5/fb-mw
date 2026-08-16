"""National-team reporting: the rules that protect the build (migration 0012).

validate.py check 9 is what makes these worth asserting. A national-team row
that breaks one of its cross-row rules fails the build, and a failed build
deploys nothing — so a reporter who could write one could stop everyleague.co
updating for everyone. The single-row rules (score as a pair, played implies a
score, slot uniqueness) are CHECK constraints in 0001 and are the database's
job; everything here is the half a constraint cannot see.

Every id this file mints is thrown away in tearDownClass, and every name it
invents is prefixed ZZTEST so a leak is obvious in a snapshot diff.

    RLS_LIVE=1 python3 -m unittest tests.test_nt_live
"""

import json
import os
import sys
import unittest
import urllib.error
import urllib.parse
import urllib.request

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from src import supabase_client as sb  # noqa: E402
from tests import live_support  # noqa: E402

COMPETITION = "ZZTEST Invitational"
TEAM = "MW_W"


def rpc(name, body, token):
    """Call an rpc as a signed-in user. Returns (status, parsed body)."""
    req = urllib.request.Request(
        f"{sb.url()}/rest/v1/rpc/{name}",
        data=json.dumps(body).encode("utf-8"), method="POST")
    req.add_header("apikey", os.environ["SUPABASE_PUBLISHABLE_KEY"])
    req.add_header("Authorization", f"Bearer {token}")
    req.add_header("Content-Type", "application/json")
    try:
        with urllib.request.urlopen(req, timeout=sb.TIMEOUT) as resp:
            raw = resp.read().decode("utf-8")
            return resp.status, json.loads(raw) if raw.strip() else None
    except urllib.error.HTTPError as err:
        raw = err.read().decode("utf-8", "replace")
        try:
            return err.code, json.loads(raw)
        except ValueError:
            return err.code, raw


def wipe(table, column, value):
    sb._request("DELETE", table,
                query=f"{column}=eq.{urllib.parse.quote(str(value))}",
                headers={"Prefer": "return=minimal"}, require_secret=True)


@unittest.skipUnless(live_support.available(), "Supabase credentials not configured")
class NTReportingTest(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        cls.identities = live_support.Identities(prefix="MW_NTLIVE").setup()
        cls.admin = cls.identities.tokens["admin"]
        cls.reporter = cls.identities.tokens["a"]
        cls.match_ids = []

    @classmethod
    def tearDownClass(cls):
        # Ordered by dependency, and deliberately NOT inside a try that could
        # itself throw before the identities are released — an earlier version
        # of this cleanup crashed on an unencoded space and leaked four
        # reporters into production.
        for mid in cls.match_ids:
            for table in ("nt_goals", "nt_lineups"):
                wipe(table, "match_id", mid)
            wipe("nt_knockout", "nt_match_id", mid)
            wipe("nt_matches", "match_id", mid)
        for table, column in (("nt_squads", "competition"),
                              ("nt_knockout", "competition_name"),
                              ("nt_competitions", "competition_name")):
            wipe(table, column, COMPETITION)
        cls.identities.teardown()

    def new_match(self, **extra):
        body = {"p_team_code": TEAM, "p_competition": COMPETITION,
                "p_opponent": "ZZ Rivals", "p_date": "2026-08-16", **extra}
        status, rows = rpc("create_nt_match", body, self.admin)
        self.assertEqual(status, 200, rows)
        mid = rows[0]["match_id"]
        type(self).match_ids.append(mid)
        return mid

    def played(self, mid, ours, theirs):
        return rpc("submit_nt_result", {
            "p_match_id": mid, "p_team_score": ours,
            "p_opponent_score": theirs, "p_status": "played"}, self.admin)

    # ── Authorization ────────────────────────────────────────────────────────

    def test_an_admin_may_edit_every_national_team(self):
        self.assertEqual(rpc("can_edit_nt", {"p_team_code": TEAM}, self.admin),
                         (200, True))

    def test_an_unassigned_reporter_may_not(self):
        self.assertEqual(rpc("can_edit_nt", {"p_team_code": TEAM}, self.reporter),
                         (200, False))
        status, _ = rpc("create_nt_match", {
            "p_team_code": TEAM, "p_competition": COMPETITION,
            "p_opponent": "ZZ Rivals"}, self.reporter)
        self.assertIn(status, (401, 403, 404))

    def test_anon_may_not_write_anything(self):
        for name, body in (("create_nt_match",
                            {"p_team_code": TEAM, "p_competition": COMPETITION,
                             "p_opponent": "X"}),
                           ("submit_nt_result",
                            {"p_match_id": "1", "p_team_score": 9,
                             "p_opponent_score": 0}),
                           ("save_nt_lineup",
                            {"p_match_id": "1", "p_team_id": TEAM, "p_rows": []})):
            req = urllib.request.Request(
                f"{sb.url()}/rest/v1/rpc/{name}",
                data=json.dumps(body).encode(), method="POST")
            req.add_header("apikey", os.environ["SUPABASE_PUBLISHABLE_KEY"])
            req.add_header("Content-Type", "application/json")
            try:
                with urllib.request.urlopen(req, timeout=sb.TIMEOUT) as resp:
                    self.fail(f"{name} allowed anon: {resp.status}")
            except urllib.error.HTTPError as err:
                self.assertIn(err.code, (401, 403, 404), name)

    # ── Scores and scorers: check 9's counting rules ─────────────────────────

    def test_a_scorer_needs_a_score_to_belong_to(self):
        mid = self.new_match()
        status, body = rpc("submit_nt_goal", {
            "p_match_id": mid, "p_team_id": TEAM,
            "p_player_name": "ZZ Early"}, self.admin)
        self.assertNotEqual(status, 200)
        self.assertIn("publish the score", str(body))

    def test_goal_rows_never_exceed_the_score(self):
        """The rule that would otherwise break every future build."""
        mid = self.new_match()
        self.assertEqual(self.played(mid, 2, 1)[0], 200)
        for name in ("ZZ One", "ZZ Two"):
            self.assertEqual(rpc("submit_nt_goal", {
                "p_match_id": mid, "p_team_id": TEAM,
                "p_player_name": name}, self.admin)[0], 200)
        status, body = rpc("submit_nt_goal", {
            "p_match_id": mid, "p_team_id": TEAM,
            "p_player_name": "ZZ Three"}, self.admin)
        self.assertNotEqual(status, 200)
        self.assertIn("already have a scorer", str(body))

    def test_the_opponent_is_counted_separately(self):
        """team_id is not a foreign key: anything that is not our code is
        theirs, and their goals count against their score."""
        mid = self.new_match()
        self.played(mid, 2, 1)
        self.assertEqual(rpc("submit_nt_goal", {
            "p_match_id": mid, "p_team_id": "ZZ_RIVALS",
            "p_player_name": "ZZ Theirs"}, self.admin)[0], 200)
        status, body = rpc("submit_nt_goal", {
            "p_match_id": mid, "p_team_id": "ZZ_RIVALS",
            "p_player_name": "ZZ Theirs Again"}, self.admin)
        self.assertNotEqual(status, 200, body)

    def test_a_score_cannot_drop_below_the_scorers_recorded(self):
        """Otherwise the next build fails on rows already written."""
        mid = self.new_match()
        self.played(mid, 2, 0)
        for name in ("ZZ One", "ZZ Two"):
            rpc("submit_nt_goal", {"p_match_id": mid, "p_team_id": TEAM,
                                   "p_player_name": name}, self.admin)
        status, body = self.played(mid, 1, 0)
        self.assertNotEqual(status, 200)
        self.assertIn("more scorers than that", str(body))

    def test_unplaying_a_match_with_scorers_is_refused(self):
        mid = self.new_match()
        self.played(mid, 1, 0)
        rpc("submit_nt_goal", {"p_match_id": mid, "p_team_id": TEAM,
                               "p_player_name": "ZZ One"}, self.admin)
        status, body = rpc("submit_nt_result", {
            "p_match_id": mid, "p_team_score": None, "p_opponent_score": None,
            "p_status": "scheduled"}, self.admin)
        self.assertNotEqual(status, 200)
        self.assertIn("remove the scorers", str(body))

    def test_a_result_can_be_retracted_once_its_scorers_are_gone(self):
        mid = self.new_match()
        self.played(mid, 1, 0)
        status, rows = rpc("submit_nt_goal", {
            "p_match_id": mid, "p_team_id": TEAM,
            "p_player_name": "ZZ One"}, self.admin)
        rpc("delete_nt_goal", {"p_goal_id": rows[0]["goal_id"]}, self.admin)
        status, rows = rpc("submit_nt_result", {
            "p_match_id": mid, "p_team_score": None, "p_opponent_score": None,
            "p_status": "scheduled"}, self.admin)
        self.assertEqual(status, 200, rows)
        self.assertIsNone(rows[0]["team_score"])

    def test_the_fixture_editor_cannot_touch_the_score(self):
        """Same split as the club side: fixing a venue is not permission to
        change a result."""
        mid = self.new_match()
        self.played(mid, 3, 0)
        status, rows = rpc("update_nt_fixture", {
            "p_match_id": mid, "p_competition": COMPETITION,
            "p_opponent": "ZZ Rivals", "p_venue": "ZZ Stadium"}, self.admin)
        self.assertEqual(status, 200, rows)
        self.assertEqual(rows[0]["venue"], "ZZ Stadium")
        self.assertEqual(rows[0]["team_score"], 3)

    # ── Line-ups, which carry the cards and the substitutions ────────────────

    def lineup(self, mid, rows):
        return rpc("save_nt_lineup",
                   {"p_match_id": mid, "p_team_id": TEAM, "p_rows": rows},
                   self.admin)

    @staticmethod
    def xi(n=11):
        return [{"player_name": f"ZZ P{i}", "role": "starting",
                 "shirt_number": str(i), "position": "MF"}
                for i in range(1, n + 1)]

    def test_a_starting_xi_is_eleven(self):
        mid = self.new_match()
        status, body = self.lineup(mid, self.xi(12))
        self.assertNotEqual(status, 200)
        self.assertIn("eleven", str(body))

    def test_a_substitute_who_came_on_needs_a_minute(self):
        mid = self.new_match()
        status, body = self.lineup(
            mid, self.xi() + [{"player_name": "ZZ Sub", "role": "sub_on"}])
        self.assertNotEqual(status, 200)
        self.assertIn("needs the minute", str(body))

    def test_a_replaced_player_must_be_in_the_line_up(self):
        mid = self.new_match()
        status, body = self.lineup(mid, self.xi() + [{
            "player_name": "ZZ Sub", "role": "sub_on", "minute_on": "60",
            "replaced_player": "ZZ Ghost"}])
        self.assertNotEqual(status, 200)
        self.assertIn("not in this line-up", str(body))

    def test_the_same_player_cannot_be_listed_twice(self):
        mid = self.new_match()
        status, body = self.lineup(mid, self.xi() + [
            {"player_name": "ZZ P1", "role": "unused_sub"}])
        self.assertNotEqual(status, 200)
        self.assertIn("twice", str(body))

    def test_a_valid_team_sheet_saves_with_cards_and_changes(self):
        mid = self.new_match()
        rows = self.xi()
        rows[0]["captain"] = True
        rows[2]["yellow_card"] = True
        rows[3]["red_card"] = True
        rows.append({"player_name": "ZZ Sub", "role": "sub_on",
                     "minute_on": "60", "replaced_player": "ZZ P3"})
        rows.append({"player_name": "ZZ Bench", "role": "unused_sub"})
        status, saved = self.lineup(mid, rows)
        self.assertEqual(status, 200, saved)
        self.assertEqual(len(saved), 13)
        by_name = {r["player_name"]: r for r in saved}
        self.assertTrue(by_name["ZZ P1"]["captain"])
        self.assertTrue(by_name["ZZ P3"]["yellow_card"])
        self.assertTrue(by_name["ZZ P4"]["red_card"])
        self.assertEqual(by_name["ZZ Sub"]["replaced_player"], "ZZ P3")

    def test_a_replaced_starter_is_marked_off_at_the_substitution(self):
        """nt_page draws "↓ 63'" beside a starter from minute_off, and nothing
        set it — so the first sheet entered through /report rendered a starting
        XI with no substitution arrows while the list below it read correctly.
        The arrival already says it, so the departure is derived rather than
        asked for twice."""
        mid = self.new_match()
        rows = self.xi()
        rows.append({"player_name": "ZZ Sub", "role": "sub_on",
                     "minute_on": "63", "replaced_player": "ZZ P7"})
        status, saved = self.lineup(mid, rows)
        self.assertEqual(status, 200, saved)
        by_name = {r["player_name"]: r for r in saved}
        self.assertEqual(by_name["ZZ P7"]["minute_off"], "63")
        # Everyone who stayed on is untouched.
        self.assertEqual(by_name["ZZ P8"]["minute_off"], "")

    def test_an_explicit_minute_off_is_not_overwritten(self):
        """A player sent off, or withdrawn with nobody replacing them, leaves
        at a minute no substitution records. That has to stay sayable."""
        mid = self.new_match()
        rows = self.xi()
        rows[6]["minute_off"] = "30"
        rows[6]["red_card"] = True
        rows.append({"player_name": "ZZ Sub", "role": "sub_on",
                     "minute_on": "63", "replaced_player": "ZZ P7"})
        status, saved = self.lineup(mid, rows)
        self.assertEqual(status, 200, saved)
        by_name = {r["player_name"]: r for r in saved}
        self.assertEqual(by_name["ZZ P7"]["minute_off"], "30")
        self.assertTrue(by_name["ZZ P7"]["red_card"])

    def test_saving_a_line_up_replaces_the_previous_one(self):
        """A team sheet is a set, not a patch — re-saving is how a mistake is
        corrected, so the old rows must not survive alongside the new."""
        mid = self.new_match()
        self.lineup(mid, self.xi())
        status, saved = self.lineup(mid, self.xi(7))
        self.assertEqual(status, 200, saved)
        self.assertEqual(len(saved), 7)

    # ── Competitions, groups and brackets ────────────────────────────────────

    def test_a_bracket_needs_a_group_table_to_hang_off(self):
        """check 9 rejects a bracket whose competition matches no group row —
        it would render on no page and vanish silently."""
        status, body = rpc("upsert_nt_tie", {
            "p_competition_name": "ZZTEST Orphan", "p_stage": "final",
            "p_slot": 1}, self.admin)
        self.assertNotEqual(status, 200)
        self.assertIn("no group table", str(body))

    def test_a_rival_row_must_carry_its_own_name(self):
        rpc("create_nt_competition", {
            "p_competition_name": COMPETITION, "p_team_code": TEAM,
            "p_group_name": "Group A"}, self.admin)
        status, body = rpc("upsert_nt_group_row", {
            "p_competition_name": COMPETITION, "p_group_name": "Group A",
            "p_team_code": "ZZ_RIVAL"}, self.admin)
        self.assertNotEqual(status, 200)
        self.assertIn("needs a name", str(body))

    def test_removing_our_only_team_would_orphan_the_group(self):
        rpc("create_nt_competition", {
            "p_competition_name": COMPETITION, "p_team_code": TEAM,
            "p_group_name": "Group A"}, self.admin)
        status, body = rpc("delete_nt_group_row", {
            "p_competition_name": COMPETITION, "p_team_code": TEAM}, self.admin)
        self.assertNotEqual(status, 200)
        self.assertIn("only one of our teams", str(body))

    def test_a_tie_feeding_another_cannot_be_deleted(self):
        """A slot fed by a tie that no longer exists renders a blank forever."""
        rpc("create_nt_competition", {
            "p_competition_name": COMPETITION, "p_team_code": TEAM,
            "p_group_name": "Group A"}, self.admin)
        status, sf = rpc("upsert_nt_tie", {
            "p_competition_name": COMPETITION, "p_stage": "sf", "p_slot": 1,
            "p_home_name": "ZZ A", "p_away_name": "ZZ B"}, self.admin)
        self.assertEqual(status, 200, sf)
        tie_id = sf[0]["tie_id"]
        rpc("upsert_nt_tie", {
            "p_competition_name": COMPETITION, "p_stage": "final", "p_slot": 1,
            "p_home_from": f"winner:{tie_id}"}, self.admin)
        status, body = rpc("delete_nt_tie", {"p_tie_id": tie_id}, self.admin)
        self.assertNotEqual(status, 200)
        self.assertIn("waiting on this one", str(body))

    def test_a_linked_tie_keeps_no_score_of_its_own(self):
        """nt_matches owns the result for our own ties (see nt._link_match), so
        a copy here could contradict it with nothing to say which is right."""
        rpc("create_nt_competition", {
            "p_competition_name": COMPETITION, "p_team_code": TEAM,
            "p_group_name": "Group A"}, self.admin)
        mid = self.new_match()
        self.played(mid, 2, 1)
        status, tie = rpc("upsert_nt_tie", {
            "p_competition_name": COMPETITION, "p_stage": "3p", "p_slot": 1,
            "p_home_name": "ZZ Rivals", "p_away_name": "Malawi",
            "p_home_score": 9, "p_away_score": 9, "p_venue": "ZZ Contradiction",
            "p_nt_match_id": mid}, self.admin)
        self.assertEqual(status, 200, tie)
        self.assertIsNone(tie[0]["home_score"])
        self.assertEqual(tie[0]["venue"], "")
        self.assertEqual(tie[0]["nt_match_id"], mid)

    # ── Squads ───────────────────────────────────────────────────────────────

    def test_a_squad_saves_whole_and_replaces_itself(self):
        status, first = rpc("save_nt_squad", {
            "p_team_id": TEAM, "p_competition": COMPETITION,
            "p_coach": "ZZ Coach",
            "p_players": [{"player_name": "ZZ One", "position": "GK",
                           "shirt_number": "1"},
                          {"player_name": "ZZ Two", "position": "FW"}]},
            self.admin)
        self.assertEqual(status, 200, first)
        self.assertEqual(len(first), 2)
        squad_id = first[0]["squad_id"]

        status, again = rpc("save_nt_squad", {
            "p_team_id": TEAM, "p_competition": COMPETITION,
            "p_squad_id": squad_id, "p_coach": "ZZ Coach",
            "p_players": [{"player_name": "ZZ One", "position": "GK"}]},
            self.admin)
        self.assertEqual(status, 200, again)
        self.assertEqual(len(again), 1)

    def test_a_squad_needs_at_least_one_player(self):
        status, body = rpc("save_nt_squad", {
            "p_team_id": TEAM, "p_competition": COMPETITION,
            "p_players": []}, self.admin)
        self.assertNotEqual(status, 200)
        self.assertIn("at least one player", str(body))


if __name__ == "__main__":
    unittest.main()
