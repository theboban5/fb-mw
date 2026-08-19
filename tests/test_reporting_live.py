"""submit_match_report: the only write path a reporter has.

The RPC is the security boundary, so these tests attack it directly rather
than through the UI. They check three separate things:

  * who may call it (assignment, active, admin);
  * what it is allowed to change — the narrow-update guarantee, which is the
    whole reason a generic UPDATE policy was refused;
  * that every change is attributable afterwards.

Nothing here touches a real result. Each run creates its own fixture rows in a
real competition and deletes them again, because proving a policy works is not
a good enough reason to rewrite a genuine scoreline.

    RLS_LIVE=1 python3 -m unittest tests.test_reporting_live
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
class SubmitMatchReportTest(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        cls.identities = live_support.Identities(prefix="MW_RPCTEST").setup()
        cls.tokens = cls.identities.tokens
        cls.ids = cls.identities.ids
        suffix = cls.identities.suffix
        # One throwaway fixture in each competition, so "A may report this and
        # not that" is asked about matches nobody is relying on.
        cls.match_a = live_support.make_test_match(
            live_support.Identities.COMP_A, suffix)
        cls.match_b = live_support.make_test_match(
            live_support.Identities.COMP_B, suffix)

    @classmethod
    def tearDownClass(cls):
        live_support.drop_test_match(cls.match_a["match_id"])
        live_support.drop_test_match(cls.match_b["match_id"])
        cls.identities.teardown()

    def setUp(self):
        # Each test starts from an unreported fixture.
        for match in (self.match_a, self.match_b):
            sb._request("PATCH", "matches",
                        query=f"match_id=eq.{match['match_id']}",
                        body={"home_goals": None, "away_goals": None,
                              "status": "scheduled", "reported_by": None,
                              "reported_at": None, "confidence": "unconfirmed",
                              "verified_by": None, "verified_at": None},
                        headers={"Prefer": "return=minimal"},
                        require_secret=True)
        sb._request("DELETE", "match_change_log",
                    query=f"match_id=eq.{self.match_a['match_id']}",
                    headers={"Prefer": "return=minimal"}, require_secret=True)

    def submit(self, label, match, home, away, status):
        return call("rpc/submit_match_report", token=self.tokens[label],
                    method="POST", body={
                        "p_match_id": match["match_id"],
                        "p_home_score": home, "p_away_score": away,
                        "p_status": status})

    def row(self, match):
        return sb.select("matches",
                         params={"match_id": f"eq.{match['match_id']}"},
                         require_secret=True)[0]

    def log(self, match):
        return sb.select("match_change_log",
                         params={"match_id": f"eq.{match['match_id']}"},
                         order="changed_at.asc", require_secret=True)

    # ── The happy path ───────────────────────────────────────────────────────

    def test_an_assigned_reporter_publishes_a_result(self):
        status, body = self.submit("a", self.match_a, 2, 1, "played")
        self.assertEqual(status, 200, body)
        row = self.row(self.match_a)
        self.assertEqual((row["home_goals"], row["away_goals"], row["status"]),
                         (2, 1, "played"))

    def test_publishing_records_provenance(self):
        self.submit("a", self.match_a, 2, 1, "played")
        row = self.row(self.match_a)
        self.assertEqual(row["source_type"], "reporter")
        self.assertEqual(row["reported_by"], self.ids["a"])
        self.assertIsNotNone(row["reported_at"])

    def test_a_reporters_result_is_confirmed_and_verified(self):
        """Since 0029 the asterisk is not for reporters.

        It used to be: a reporter's result published as `unconfirmed` until an
        admin re-typed the same score. But a reporter is assigned to the
        competition by an admin and was at the ground — their word is the best
        evidence this site has, and the gate is now who gets assigned, not what
        happens after they submit.
        """
        self.submit("a", self.match_a, 2, 1, "played")
        row = self.row(self.match_a)
        self.assertEqual(row["confidence"], "confirmed")
        self.assertEqual(row["verified_by"], self.ids["a"])
        self.assertIsNotNone(row["verified_at"])

    def test_an_admins_result_is_confirmed_and_verified(self):
        self.submit("admin", self.match_a, 3, 0, "played")
        row = self.row(self.match_a)
        self.assertEqual(row["confidence"], "confirmed")
        self.assertEqual(row["verified_by"], self.ids["admin"])
        self.assertIsNotNone(row["verified_at"])

    def test_a_status_without_a_score_publishes(self):
        status, body = self.submit("a", self.match_a, None, None, "postponed")
        self.assertEqual(status, 200, body)
        row = self.row(self.match_a)
        self.assertEqual(row["status"], "postponed")
        self.assertIsNone(row["home_goals"])

    # ── Authorization ────────────────────────────────────────────────────────

    def test_a_reporter_cannot_publish_another_competition(self):
        status, body = self.submit("a", self.match_b, 5, 0, "played")
        self.assertEqual(status, 403, body)
        self.assertIn("not assigned", str(body))
        # And nothing moved.
        self.assertIsNone(self.row(self.match_b)["home_goals"])

    def test_an_inactive_reporter_cannot_publish(self):
        status, body = self.submit("inactive", self.match_a, 4, 4, "played")
        self.assertEqual(status, 403, body)
        self.assertIn("inactive", str(body))
        self.assertIsNone(self.row(self.match_a)["home_goals"])

    def test_an_admin_can_publish_any_competition(self):
        for match in (self.match_a, self.match_b):
            status, body = self.submit("admin", match, 1, 1, "played")
            self.assertEqual(status, 200, body)
            self.assertEqual(self.row(match)["home_goals"], 1)

    def test_anon_cannot_call_the_rpc(self):
        status, _body = call("rpc/submit_match_report", token=None,
                             method="POST", body={
                                 "p_match_id": self.match_a["match_id"],
                                 "p_home_score": 9, "p_away_score": 0,
                                 "p_status": "played"})
        self.assertIn(status, (401, 403, 404))
        self.assertIsNone(self.row(self.match_a)["home_goals"])

    def test_a_missing_match_is_reported_as_such(self):
        status, body = call("rpc/submit_match_report", token=self.tokens["a"],
                            method="POST", body={
                                "p_match_id": "MW_NO_SUCH_MATCH",
                                "p_home_score": 1, "p_away_score": 0,
                                "p_status": "played"})
        self.assertNotEqual(status, 200)
        self.assertIn("match not found", str(body))

    # ── Validation ───────────────────────────────────────────────────────────

    def test_a_played_result_needs_both_scores(self):
        status, body = self.submit("a", self.match_a, 2, None, "played")
        self.assertNotEqual(status, 200)
        self.assertIn("invalid score", str(body))

    def test_an_unplayed_match_cannot_carry_a_score(self):
        status, body = self.submit("a", self.match_a, 2, 1, "scheduled")
        self.assertNotEqual(status, 200)
        self.assertIn("invalid score", str(body))

    def test_a_negative_or_absurd_score_is_refused(self):
        for home, away in ((-1, 0), (0, -3), (100, 0)):
            status, body = self.submit("a", self.match_a, home, away, "played")
            self.assertNotEqual(status, 200, f"{home}-{away} was accepted")
            self.assertIn("invalid score", str(body))

    def test_an_unknown_status_is_refused(self):
        # Notably 'full_time': the reporter UI says "Full time" but the
        # database vocabulary is 'played', and nothing may smuggle in a
        # seventh status that the renderers do not understand.
        for bad in ("full_time", "live", "", "PLAYED "):
            status, body = self.submit("a", self.match_a, 1, 0, bad)
            self.assertNotEqual(status, 200, f"{bad!r} was accepted")

    def test_only_an_admin_can_award_a_match(self):
        status, body = self.submit("a", self.match_a, 3, 0, "awarded")
        self.assertEqual(status, 403, body)
        self.assertIn("administrator", str(body))
        status, body = self.submit("admin", self.match_a, 3, 0, "awarded")
        self.assertEqual(status, 200, body)

    # ── The narrow-update guarantee ──────────────────────────────────────────

    def test_publishing_cannot_move_the_fixture(self):
        """The reason a generic UPDATE policy was refused.

        Whatever else it does, the RPC must not be able to change who is
        playing, in what competition, in what season, or when.
        """
        before = self.row(self.match_a)
        self.submit("a", self.match_a, 2, 1, "played")
        after = self.row(self.match_a)
        for column in ("home_team_id", "away_team_id", "competition_id",
                       "season_id", "date", "kickoff", "venue_id", "stage",
                       "matchday", "public_id"):
            self.assertEqual(before[column], after[column], column)

    def test_the_rpc_ignores_extra_arguments(self):
        # PostgREST matches functions by argument names; an unknown one must
        # not silently become a column write.
        status, _body = call("rpc/submit_match_report", token=self.tokens["a"],
                             method="POST", body={
                                 "p_match_id": self.match_a["match_id"],
                                 "p_home_score": 1, "p_away_score": 0,
                                 "p_status": "played",
                                 "p_home_team_id": "MW_BULL_M1"})
        self.assertNotEqual(status, 200)

    # ── Audit ────────────────────────────────────────────────────────────────

    def test_a_correction_keeps_the_original_result(self):
        """2-1 corrected to 2-2 must not erase that 2-1 was published."""
        self.submit("a", self.match_a, 2, 1, "played")
        self.submit("a", self.match_a, 2, 2, "played")

        entries = self.log(self.match_a)
        self.assertEqual(len(entries), 2)

        # source_ref joined old_values/new_values in 0008 — where a result came
        # from is part of what changed, and is audited with the score.
        first, second = entries
        self.assertEqual(first["old_values"]["status"], "scheduled")
        self.assertIsNone(first["old_values"]["home_goals"])
        self.assertEqual(first["new_values"],
                         {"home_goals": 2, "away_goals": 1, "status": "played",
                          "source_ref": ""})
        self.assertEqual(second["old_values"],
                         {"home_goals": 2, "away_goals": 1, "status": "played",
                          "source_ref": ""})
        self.assertEqual(second["new_values"],
                         {"home_goals": 2, "away_goals": 2, "status": "played",
                          "source_ref": ""})

    def test_every_change_is_attributed(self):
        self.submit("a", self.match_a, 1, 0, "played")
        self.submit("admin", self.match_a, 1, 1, "played")
        who = [e["changed_by"] for e in self.log(self.match_a)]
        self.assertEqual(who, [self.ids["a"], self.ids["admin"]])

    def test_republishing_an_unchanged_result_adds_no_noise(self):
        self.submit("a", self.match_a, 2, 1, "played")
        self.submit("a", self.match_a, 2, 1, "played")
        self.assertEqual(len(self.log(self.match_a)), 1)

    def test_anon_cannot_read_the_audit_log(self):
        self.submit("a", self.match_a, 2, 1, "played")
        status, rows = call("match_change_log?select=*", token=None)
        self.assertEqual(status, 200, rows)
        self.assertEqual(rows, [])

    def test_a_reporter_cannot_read_another_competitions_audit(self):
        self.submit("admin", self.match_b, 1, 0, "played")
        status, rows = call(
            f"match_change_log?select=match_id&match_id=eq.{self.match_b['match_id']}",
            token=self.tokens["a"])
        self.assertEqual(status, 200, rows)
        self.assertEqual(rows, [])

    def test_a_reporter_can_read_their_own_matchs_audit(self):
        self.submit("a", self.match_a, 2, 1, "played")
        status, rows = call(
            f"match_change_log?select=match_id&match_id=eq.{self.match_a['match_id']}",
            token=self.tokens["a"])
        self.assertEqual(status, 200, rows)
        self.assertEqual(len(rows), 1)

    def test_the_audit_log_cannot_be_rewritten(self):
        self.submit("a", self.match_a, 2, 1, "played")
        entry = self.log(self.match_a)[0]
        for method, body in (("PATCH", {"new_values": {"home_goals": 9}}),
                             ("DELETE", None)):
            status, _body = call(f"match_change_log?id=eq.{entry['id']}",
                                 token=self.tokens["a"], method=method,
                                 body=body, prefer="return=representation")
            self.assertIn(status, (401, 403, 404, 405), method)
        # Still exactly as written.
        self.assertEqual(self.log(self.match_a)[0]["new_values"],
                         {"home_goals": 2, "away_goals": 1, "status": "played",
                          "source_ref": ""})


if __name__ == "__main__":
    unittest.main()
