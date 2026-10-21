"""amend_match_goals (0054): filling in what a goal row is missing.

The property that matters most is the one in the migration's name for it —
FILL BLANKS, NEVER OVERWRITE. An import enriches a goal somebody else typed;
it must not be able to change what they saw. `test_a_filled_assist_is_never_
overwritten` is that rule asserted, and `test_an_existing_goal_cannot_become_
an_own_goal` is the one amendment that would silently move a scorer to the
other team.

    RLS_LIVE=1 python3 -m unittest tests.test_amend_goals_live
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
class AmendGoalsTest(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        cls.identities = live_support.Identities(prefix="MW_AMENDTEST").setup()
        cls.tokens = cls.identities.tokens
        cls.match = live_support.make_test_match(
            live_support.Identities.COMP_A, cls.identities.suffix)
        cls.home = cls.match["home_team_id"]
        cls.players = []
        for name in ("Zzzamend Scorer", "Zzzamend Assister", "Zzzamend Other"):
            status, body = call("rpc/create_player", token=cls.tokens["a"],
                                method="POST",
                                body={"p_full_name": name, "p_force": True})
            assert status == 200, body
            cls.players.append(body[0]["player_id"])

    @classmethod
    def tearDownClass(cls):
        live_support.drop_test_match(cls.match["match_id"])   # goals cascade
        for player_id in cls.players:
            sb._request("DELETE", "players", query=f"player_id=eq.{player_id}",
                        headers={"Prefer": "return=minimal"}, require_secret=True)
        cls.identities.teardown()

    def setUp(self):
        sb._request("DELETE", "goals",
                    query=f"match_id=eq.{self.match['match_id']}",
                    headers={"Prefer": "return=minimal"}, require_secret=True)
        status, body = call("rpc/submit_match_report", token=self.tokens["a"],
                            method="POST", body={
                                "p_match_id": self.match["match_id"],
                                "p_home_score": 2, "p_away_score": 0,
                                "p_status": "played"})
        self.assertEqual(status, 200, body)
        status, body = call("rpc/submit_match_goal", token=self.tokens["a"],
                            method="POST", body={
                                "p_match_id": self.match["match_id"],
                                "p_team_id": self.home,
                                "p_player_name": "Zzzamend Scorer",
                                "p_player_id": self.players[0]})
        self.assertEqual(status, 200, body)
        self.goal_id = body[0]["goal_id"]

    def amend(self, rows, label="a"):
        return call("rpc/amend_match_goals", token=self.tokens.get(label),
                    method="POST", body={"p_match_id": self.match["match_id"],
                                         "p_amendments": rows})

    def goal(self):
        return sb.select("goals", params={"goal_id": f"eq.{self.goal_id}"},
                         require_secret=True)[0]

    def test_a_blank_assist_minute_and_type_are_filled(self):
        status, body = self.amend([{
            "goal_id": self.goal_id, "assist_player_id": self.players[1],
            "minute": "7", "goal_type": "header"}])
        self.assertEqual(status, 200, body)
        self.assertTrue(body[0]["ok"], body)
        goal = self.goal()
        self.assertEqual(goal["assist_player_id"], self.players[1])
        self.assertEqual(goal["minute"], "7")
        self.assertEqual(goal["goal_type"], "header")
        # Who typed the goal is still who typed the goal.
        self.assertEqual(goal["reported_by"], self.identities.ids["a"])

    def test_a_filled_assist_is_never_overwritten(self):
        self.assertTrue(self.amend([{"goal_id": self.goal_id,
                                     "assist_player_id": self.players[1]}])[1][0]["ok"])
        status, body = self.amend([{
            "goal_id": self.goal_id, "assist_player_id": self.players[2],
            "minute": "9"}])
        self.assertEqual(status, 200, body)
        self.assertFalse(body[0]["ok"])
        self.assertIn("already has an assist", body[0]["message"])
        self.assertIn("Zzzamend Assister", body[0]["message"],
                      "the sentence names what is there")
        goal = self.goal()
        self.assertEqual(goal["assist_player_id"], self.players[1])
        self.assertEqual(goal["minute"], "", "a refused row changes nothing")

    def test_the_same_value_again_is_a_no_op(self):
        row = {"goal_id": self.goal_id, "assist_player_id": self.players[1]}
        self.assertTrue(self.amend([row])[1][0]["ok"])
        self.assertTrue(self.amend([row])[1][0]["ok"])

    def test_an_existing_goal_cannot_become_an_own_goal(self):
        status, body = self.amend([{"goal_id": self.goal_id,
                                    "goal_type": "own_goal"}])
        self.assertEqual(status, 200, body)
        self.assertFalse(body[0]["ok"])
        self.assertEqual(self.goal()["goal_type"], "")

    def test_an_own_goal_takes_no_assist(self):
        sb._request("PATCH", "goals", query=f"goal_id=eq.{self.goal_id}",
                    body={"goal_type": "own_goal"},
                    headers={"Prefer": "return=minimal"}, require_secret=True)
        status, body = self.amend([{"goal_id": self.goal_id,
                                    "assist_player_id": self.players[1]}])
        self.assertEqual(status, 200, body)
        self.assertFalse(body[0]["ok"])
        self.assertIn("own goal has no assist", body[0]["message"])

    def test_the_assist_rules_are_the_insert_rules(self):
        cases = [
            (self.players[0], "cannot assist their own goal"),
            ("CAF_MW_UNKNOWN", "leave the assist blank"),
            ("CAF_MW_999999", "not in the database"),
        ]
        for assist, message in cases:
            status, body = self.amend([{"goal_id": self.goal_id,
                                        "assist_player_id": assist}])
            self.assertEqual(status, 200, body)
            self.assertFalse(body[0]["ok"], assist)
            self.assertIn(message, body[0]["message"])
        self.assertIsNone(self.goal()["assist_player_id"])

    def test_a_bad_row_does_not_stop_a_good_one(self):
        status, body = self.amend([
            {"goal_id": "MW_NOT_A_GOAL", "minute": "3"},
            {"goal_id": self.goal_id, "minute": "12"},
        ])
        self.assertEqual(status, 200, body)
        self.assertEqual([r["ok"] for r in body], [False, True])
        self.assertIn("not in this match", body[0]["message"])
        self.assertEqual(self.goal()["minute"], "12")

    def test_a_reporter_cannot_amend_another_competitions_goal(self):
        status, body = self.amend([{"goal_id": self.goal_id, "minute": "5"}],
                                  label="b")
        self.assertNotEqual(status, 200)
        self.assertEqual(self.goal()["minute"], "")

    def test_anon_is_refused_outright(self):
        # Not merely "no rows": 0048's sweep expects a 401-class refusal from
        # anything a visitor's key should not be able to reach.
        status, _ = self.amend([{"goal_id": self.goal_id, "minute": "5"}],
                               label=None)
        self.assertIn(status, (401, 403))
        self.assertEqual(self.goal()["minute"], "")


if __name__ == "__main__":
    unittest.main()
