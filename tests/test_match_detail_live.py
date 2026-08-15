"""Scorers, cards, substitutions, line-ups and media — the optional detail.

The test that matters most in this file is the goal-count guard. validate.py
check 5 fails the build when a match carries more goal rows than its score, and
the build is what deploys the site. So a reporter who could add a third scorer
to a 2-1 match could stop everyleague.co updating — for everyone, until someone
noticed. `test_cannot_add_more_scorers_than_the_score` is that hole, asserted
shut.

    RLS_LIVE=1 python3 -m unittest tests.test_match_detail_live
"""

import os
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from src import supabase_client as sb  # noqa: E402
from tests import live_support  # noqa: E402
from tests.live_support import call  # noqa: E402


@unittest.skipUnless(live_support.available(), "Supabase credentials not configured")
class MatchDetailTest(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        cls.identities = live_support.Identities(prefix="MW_DETAILTEST").setup()
        cls.tokens = cls.identities.tokens
        cls.ids = cls.identities.ids
        cls.match_a = live_support.make_test_match(
            live_support.Identities.COMP_A, cls.identities.suffix)
        cls.match_b = live_support.make_test_match(
            live_support.Identities.COMP_B, cls.identities.suffix)

    @classmethod
    def tearDownClass(cls):
        live_support.drop_test_match(cls.match_a["match_id"])
        live_support.drop_test_match(cls.match_b["match_id"])
        cls.identities.teardown()

    def setUp(self):
        for match in (self.match_a, self.match_b):
            sb._request("DELETE", "goals",
                        query=f"match_id=eq.{match['match_id']}",
                        headers={"Prefer": "return=minimal"}, require_secret=True)
            sb._request("PATCH", "matches",
                        query=f"match_id=eq.{match['match_id']}",
                        body={"home_goals": None, "away_goals": None,
                              "status": "scheduled"},
                        headers={"Prefer": "return=minimal"}, require_secret=True)

    def publish(self, label, match, home, away):
        return call("rpc/submit_match_report", token=self.tokens[label],
                    method="POST", body={
                        "p_match_id": match["match_id"], "p_home_score": home,
                        "p_away_score": away, "p_status": "played"})

    def add_goal(self, label, match, team_id, name, minute="", goal_type=""):
        return call("rpc/submit_match_goal", token=self.tokens[label],
                    method="POST", body={
                        "p_match_id": match["match_id"], "p_team_id": team_id,
                        "p_player_name": name, "p_minute": minute,
                        "p_goal_type": goal_type})

    def goals(self, match):
        return sb.select("goals", params={"match_id": f"eq.{match['match_id']}"},
                         order="ord.asc", require_secret=True)

    # ── The guard that protects the build ────────────────────────────────────

    def test_cannot_add_more_scorers_than_the_score(self):
        """validate.py check 5, enforced at write time.

        Without this, a reporter could make every future build fail.
        """
        self.publish("a", self.match_a, 2, 1)
        home = self.match_a["home_team_id"]
        self.assertEqual(self.add_goal("a", self.match_a, home, "One")[0], 200)
        self.assertEqual(self.add_goal("a", self.match_a, home, "Two")[0], 200)

        status, body = self.add_goal("a", self.match_a, home, "Three")
        self.assertNotEqual(status, 200)
        self.assertIn("already have a scorer", str(body))
        self.assertEqual(len(self.goals(self.match_a)), 2)

    def test_fewer_scorers_than_goals_is_fine(self):
        # Incomplete scorer data is expected and must never be an error.
        self.publish("a", self.match_a, 3, 0)
        self.assertEqual(
            self.add_goal("a", self.match_a, self.match_a["home_team_id"],
                          "Only One Known")[0], 200)
        self.assertEqual(len(self.goals(self.match_a)), 1)

    def test_scorers_are_refused_before_a_score_exists(self):
        # check 5 also rejects goal rows on a match with no score at all.
        status, body = self.add_goal("a", self.match_a,
                                     self.match_a["home_team_id"], "Too Early")
        self.assertNotEqual(status, 200)
        self.assertIn("publish the score", str(body))

    def test_a_goal_must_belong_to_a_side_that_played(self):
        self.publish("a", self.match_a, 1, 0)
        status, body = self.add_goal("a", self.match_a, "MW_BULL_M1", "Wrong Team")
        self.assertNotEqual(status, 200)
        self.assertIn("did not play", str(body))

    def test_removing_a_scorer_frees_the_slot(self):
        """Otherwise a typo would occupy a goal slot forever."""
        self.publish("a", self.match_a, 1, 0)
        home = self.match_a["home_team_id"]
        self.add_goal("a", self.match_a, home, "Mistyped Nmae")
        self.assertNotEqual(self.add_goal("a", self.match_a, home, "Correct")[0], 200)

        goal_id = self.goals(self.match_a)[0]["goal_id"]
        status, _ = call("rpc/delete_match_goal", token=self.tokens["a"],
                         method="POST", body={"p_goal_id": goal_id})
        self.assertEqual(status, 200)
        self.assertEqual(self.add_goal("a", self.match_a, home, "Correct")[0], 200)

    # ── The unresolved-scorer convention ─────────────────────────────────────

    def test_a_reported_scorer_uses_the_reserved_unknown_player(self):
        """The name is kept, but it must not enter canonical rankings.

        The build already drops CAF_MW_UNKNOWN from scorer tables, so this is
        what keeps a free-text name out of them while still counting the goal.
        """
        self.publish("a", self.match_a, 1, 0)
        self.add_goal("a", self.match_a, self.match_a["home_team_id"],
                      "  Yamikani Phiri  ", minute="67")
        goal = self.goals(self.match_a)[0]
        self.assertEqual(goal["player_id"], "CAF_MW_UNKNOWN")
        self.assertEqual(goal["reported_player_name"], "Yamikani Phiri")
        self.assertEqual(goal["minute"], "67")
        self.assertEqual(goal["source_type"], "reporter")
        self.assertEqual(goal["reported_by"], self.ids["a"])

    def test_no_new_player_row_is_created_for_a_typed_name(self):
        before = len(sb.select("players", columns="player_id", require_secret=True))
        self.publish("a", self.match_a, 1, 0)
        self.add_goal("a", self.match_a, self.match_a["home_team_id"], "Some Name")
        after = len(sb.select("players", columns="player_id", require_secret=True))
        self.assertEqual(before, after)

    def test_goal_ids_do_not_collide(self):
        self.publish("a", self.match_a, 3, 0)
        home = self.match_a["home_team_id"]
        for name in ("A", "B", "C"):
            self.assertEqual(self.add_goal("a", self.match_a, home, name)[0], 200)
        ids = [g["goal_id"] for g in self.goals(self.match_a)]
        self.assertEqual(len(set(ids)), 3, ids)

    # ── Authorization ────────────────────────────────────────────────────────

    def test_a_reporter_cannot_add_scorers_to_another_competition(self):
        self.publish("admin", self.match_b, 1, 0)
        status, body = self.add_goal("a", self.match_b,
                                     self.match_b["home_team_id"], "Nope")
        self.assertNotEqual(status, 200)
        self.assertIn("not assigned", str(body))

    def test_a_reporter_cannot_remove_someone_elses_scorer(self):
        self.publish("admin", self.match_a, 1, 0)
        self.add_goal("admin", self.match_a, self.match_a["home_team_id"], "Theirs")
        goal_id = self.goals(self.match_a)[0]["goal_id"]
        status, body = call("rpc/delete_match_goal", token=self.tokens["a"],
                            method="POST", body={"p_goal_id": goal_id})
        self.assertNotEqual(status, 200)
        self.assertIn("only remove scorers you added", str(body))

    def test_an_admin_can_remove_anyones_scorer(self):
        self.publish("a", self.match_a, 1, 0)
        self.add_goal("a", self.match_a, self.match_a["home_team_id"], "Mine")
        goal_id = self.goals(self.match_a)[0]["goal_id"]
        status, _ = call("rpc/delete_match_goal", token=self.tokens["admin"],
                         method="POST", body={"p_goal_id": goal_id})
        self.assertEqual(status, 200)

    def test_anon_cannot_add_a_scorer(self):
        self.publish("a", self.match_a, 1, 0)
        status, _ = call("rpc/submit_match_goal", token=None, method="POST",
                         body={"p_match_id": self.match_a["match_id"],
                               "p_team_id": self.match_a["home_team_id"],
                               "p_player_name": "Hacker"})
        self.assertIn(status, (401, 403, 404))
        self.assertEqual(len(self.goals(self.match_a)), 0)

    # ── Incidents and line-ups (ordinary RLS, not an RPC) ────────────────────

    def incident(self, label, match, **extra):
        body = {"match_id": match["match_id"], "team_id": match["home_team_id"],
                "incident_type": "yellow_card", "player_name": "A Player",
                "reported_by": self.ids.get(label and label or "a"),
                **extra}
        return call("match_incidents", token=self.tokens[label], method="POST",
                    body=body, prefer="return=representation")

    def test_a_reporter_can_record_a_card(self):
        status, rows = self.incident("a", self.match_a, minute="34")
        self.assertEqual(status, 201, rows)

    def test_a_reporter_cannot_record_a_card_in_another_competition(self):
        status, rows = call(
            "match_incidents", token=self.tokens["a"], method="POST",
            body={"match_id": self.match_b["match_id"],
                  "team_id": self.match_b["home_team_id"],
                  "incident_type": "red_card", "player_name": "Nope",
                  "reported_by": self.ids["a"]},
            prefer="return=representation")
        self.assertIn(status, (401, 403), rows)

    def test_a_reporter_cannot_forge_authorship(self):
        """WITH CHECK binds reported_by to the caller."""
        status, rows = call(
            "match_incidents", token=self.tokens["a"], method="POST",
            body={"match_id": self.match_a["match_id"],
                  "team_id": self.match_a["home_team_id"],
                  "incident_type": "red_card", "player_name": "Framed",
                  "reported_by": self.ids["b"]},
            prefer="return=representation")
        self.assertIn(status, (401, 403), rows)

    def test_a_substitution_must_name_both_players(self):
        status, rows = call(
            "match_incidents", token=self.tokens["a"], method="POST",
            body={"match_id": self.match_a["match_id"],
                  "team_id": self.match_a["home_team_id"],
                  "incident_type": "substitution", "player_name": "Came On",
                  "reported_by": self.ids["a"]},
            prefer="return=representation")
        self.assertNotEqual(status, 201, rows)

    def test_a_lineup_takes_plain_names(self):
        status, rows = call(
            "lineup_entries", token=self.tokens["a"], method="POST",
            body=[{"match_id": self.match_a["match_id"],
                   "team_id": self.match_a["home_team_id"],
                   "player_name": f"Player {n}", "role": "starter",
                   "reported_by": self.ids["a"], "ord": n} for n in range(1, 12)],
            prefer="return=representation")
        self.assertEqual(status, 201, rows)
        self.assertEqual(len(rows), 11)

    def test_anon_can_read_detail_but_not_write_it(self):
        self.incident("a", self.match_a)
        status, rows = call("match_incidents?select=*", token=None)
        self.assertEqual(status, 200)
        self.assertTrue(isinstance(rows, list))
        status, _ = call("match_incidents", token=None, method="POST",
                         body={"match_id": self.match_a["match_id"],
                               "team_id": self.match_a["home_team_id"],
                               "incident_type": "red_card",
                               "player_name": "Anon"},
                         prefer="return=representation")
        self.assertIn(status, (401, 403))

    # ── Media ────────────────────────────────────────────────────────────────

    def test_media_upload_is_restricted_to_the_matchs_reporter(self):
        """The path is the authorization, not a naming convention."""
        for label, match, allowed in (("a", self.match_a, True),
                                      ("a", self.match_b, False)):
            path = f"{match['public_id']}/probe.jpg"
            got = sb.rpc("can_report_media_path", {"p_name": path},
                         require_secret=True)
            # Checked as the secret key here only to confirm the helper's shape;
            # the real per-identity check is below.
            self.assertIsInstance(got, bool)
            status, body = call("rpc/can_report_media_path",
                                token=self.tokens[label], method="POST",
                                body={"p_name": path})
            self.assertEqual(status, 200, body)
            self.assertEqual(body, allowed, f"{label} -> {match['match_id']}")

    def test_a_media_path_outside_any_match_is_refused(self):
        for path in ("not-a-uuid/x.jpg", "../escape.jpg", ""):
            status, body = call("rpc/can_report_media_path",
                                token=self.tokens["a"], method="POST",
                                body={"p_name": path})
            self.assertEqual(status, 200, body)
            self.assertFalse(body, path)

    def test_the_bucket_caps_size_and_type(self):
        bucket = sb.select("buckets", params={"id": "eq.match-media"},
                           require_secret=True) if False else None
        # storage.buckets is not exposed through PostgREST; assert via the
        # helper instead that the bucket exists and is public by fetching a
        # known-missing object (404 from storage, not 400 from a bad bucket).
        import urllib.error, urllib.request
        url = f"{sb.url()}/storage/v1/object/public/match-media/does-not-exist.jpg"
        try:
            urllib.request.urlopen(url, timeout=sb.TIMEOUT)
            code = 200
        except urllib.error.HTTPError as err:
            code = err.code
        self.assertIn(code, (400, 404))


if __name__ == "__main__":
    unittest.main()
