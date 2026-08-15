"""Row Level Security, exercised with real signed-in identities.

The point of this file is that it does NOT trust the UI, the client library, or
a reading of the policy SQL. It creates four genuine auth users, signs each of
them in over the network to get a real JWT, and then asks PostgREST — as those
users — for things they should and should not be able to have. A policy that
looks right and is not will fail here.

The four identities:

    A          assigned to one competition
    B          assigned to a different competition
    inactive   assigned, but reporters.active = false
    admin      assigned to nothing, role = admin

It touches the network and creates real auth users, so it is opt-in: the plain
`python3 -m unittest discover -s tests` stays offline, fast and green. Run it
deliberately, against a project you are willing to write to:

    RLS_LIVE=1 python3 -m unittest tests.test_rls_live

Every fixture it creates is torn down again, including the auth users.
"""

import os
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from src import supabase_client as sb  # noqa: E402
from tests import live_support  # noqa: E402
from tests.live_support import call as _call  # noqa: E402


@unittest.skipUnless(live_support.available(), "Supabase credentials not configured")
class RLSTest(unittest.TestCase):

    ids = {}      # label -> reporter_id
    users = {}    # label -> auth user id
    tokens = {}   # label -> JWT

    @classmethod
    def setUpClass(cls):
        cls.identities = live_support.Identities().setup()
        cls.ids = cls.identities.ids
        cls.tokens = cls.identities.tokens
        cls.comp_a = live_support.Identities.COMP_A
        cls.comp_b = live_support.Identities.COMP_B
        # Real matches, read only: nothing in this file publishes a result.
        cls.match_a = sb.select(
            "matches", columns="match_id",
            params={"competition_id": f"eq.{cls.comp_a}"})[0]["match_id"]
        cls.match_b = sb.select(
            "matches", columns="match_id",
            params={"competition_id": f"eq.{cls.comp_b}"})[0]["match_id"]

    @classmethod
    def tearDownClass(cls):
        cls.identities.teardown()

    def assertBlockedWrite(self, path, *, token, body, table, match_id, column):
        """An UPDATE must leave the row untouched.

        Checking the status code is not enough, and getting this wrong would
        hide a real hole. With RLS enabled and no UPDATE policy, Postgres does
        not refuse the statement — it makes the row invisible to it, so the
        UPDATE matches zero rows and PostgREST answers 204 (or 200 with an
        empty body). That looks like success. The tell is that nothing changed,
        so this asserts on the effect: no row came back as affected, and a
        privileged re-read shows the original value.
        """
        before = sb.select(table, params={"match_id": f"eq.{match_id}"},
                           require_secret=True)[0]
        status, affected = _call(path, token=token, method="PATCH", body=body,
                                 prefer="return=representation")
        if status in (401, 403):
            affected = []          # refused outright, which is also fine
        self.assertEqual(affected, [], f"{path}: rows were modified")
        after = sb.select(table, params={"match_id": f"eq.{match_id}"},
                          require_secret=True)[0]
        self.assertEqual(before[column], after[column])

    def can_report(self, label, match_id):
        status, body = _call("rpc/can_report_match", token=self.tokens[label],
                             method="POST", body={"p_match_id": match_id})
        self.assertEqual(status, 200, body)
        return body

    # ── The assignment boundary ──────────────────────────────────────────────

    def test_reporter_can_report_their_own_competition(self):
        self.assertTrue(self.can_report("a", self.match_a))
        self.assertTrue(self.can_report("b", self.match_b))

    def test_reporter_cannot_report_another_competition(self):
        # The whole point of the assignment table.
        self.assertFalse(self.can_report("a", self.match_b))
        self.assertFalse(self.can_report("b", self.match_a))

    def test_admin_can_report_across_competitions(self):
        # Assigned to nothing, yet authorized everywhere.
        self.assertTrue(self.can_report("admin", self.match_a))
        self.assertTrue(self.can_report("admin", self.match_b))

    def test_inactive_reporter_cannot_report_even_when_assigned(self):
        # Assigned to comp_a, but active = false.
        self.assertFalse(self.can_report("inactive", self.match_a))

    def test_inactive_reporter_has_no_reporter_identity(self):
        status, body = _call("rpc/current_reporter_id",
                             token=self.tokens["inactive"], method="POST",
                             body={})
        self.assertEqual(status, 200, body)
        self.assertIsNone(body)

    def test_a_nonexistent_match_is_not_reportable(self):
        self.assertFalse(self.can_report("a", "MW_NO_SUCH_MATCH"))

    # ── What a reporter may read ─────────────────────────────────────────────

    def test_reporter_sees_only_their_own_reporters_row(self):
        status, rows = _call("reporters?select=reporter_id",
                             token=self.tokens["a"])
        self.assertEqual(status, 200, rows)
        self.assertEqual([r["reporter_id"] for r in rows], [self.ids["a"]])

    def test_reporter_sees_only_their_own_assignments(self):
        status, rows = _call("reporter_assignments?select=competition_id",
                             token=self.tokens["b"])
        self.assertEqual(status, 200, rows)
        self.assertEqual([r["competition_id"] for r in rows], [self.comp_b])

    def test_admin_sees_every_reporter(self):
        status, rows = _call("reporters?select=reporter_id",
                             token=self.tokens["admin"])
        self.assertEqual(status, 200, rows)
        self.assertGreaterEqual(len(rows), len(self.ids))

    # ── Anonymous visitors ───────────────────────────────────────────────────

    def test_anon_can_read_the_public_football_data(self):
        for table in ("competitions", "teams", "matches", "goals", "seasons"):
            status, rows = _call(f"{table}?select=*&limit=1", token=None)
            self.assertEqual(status, 200, f"{table}: {rows}")
            self.assertTrue(rows, table)

    def test_anon_cannot_read_reporters(self):
        status, rows = _call("reporters?select=*", token=None)
        # RLS filters rather than refuses, so the tell is an empty result.
        self.assertEqual(status, 200, rows)
        self.assertEqual(rows, [])

    def test_anon_cannot_read_reporter_assignments(self):
        status, rows = _call("reporter_assignments?select=*", token=None)
        self.assertEqual(status, 200, rows)
        self.assertEqual(rows, [])

    def test_anon_cannot_call_the_authorization_helpers(self):
        # EXECUTE was revoked from anon: leaving it callable would let anyone
        # probe the reporting surface without an account.
        for fn in ("can_report_match", "is_admin", "current_reporter_id"):
            status, _body = _call(f"rpc/{fn}", token=None, method="POST",
                                  body={"p_match_id": self.match_a}
                                  if fn == "can_report_match" else {})
            self.assertIn(status, (401, 403, 404), fn)

    def test_anon_cannot_modify_football_data(self):
        # Filtered, so PostgREST cannot reject it merely for being an
        # unqualified UPDATE — the only thing left to stop it is RLS.
        self.assertBlockedWrite(
            f"matches?match_id=eq.{self.match_a}", token=None,
            body={"home_goals": 9}, table="matches",
            match_id=self.match_a, column="home_goals")

    def test_anon_cannot_insert_football_data(self):
        status, _body = _call("goals", token=None, method="POST",
                              body={"goal_id": "RLS_TEST", "match_id": self.match_a,
                                    "team_id": "MW_BULL_M1",
                                    "player_id": "CAF_MW_UNKNOWN"},
                              prefer="return=representation")
        # INSERT has no row to hide behind, so RLS refuses outright rather than
        # silently affecting nothing (see assertBlockedWrite).
        self.assertIn(status, (401, 403))

    # ── A reporter still cannot write directly ───────────────────────────────

    def test_reporter_cannot_update_a_match_directly(self):
        """Authorization to report is not permission to edit the row.

        There is no UPDATE policy on matches at all; reporting goes through a
        narrow RPC (0003) so a reporter can never move a fixture, swap the
        teams, or reassign a season.
        """
        # Reporter A IS authorized to report this match — can_report_match is
        # true for it — which is exactly what makes this the important case.
        self.assertTrue(self.can_report("a", self.match_a))
        self.assertBlockedWrite(
            f"matches?match_id=eq.{self.match_a}", token=self.tokens["a"],
            body={"home_team_id": "MW_BULL_M1"}, table="matches",
            match_id=self.match_a, column="home_team_id")

    def test_reporter_cannot_move_a_match_to_another_season(self):
        self.assertBlockedWrite(
            f"matches?match_id=eq.{self.match_a}", token=self.tokens["a"],
            body={"season_id": "MW_2025_26"}, table="matches",
            match_id=self.match_a, column="season_id")

    def test_reporter_cannot_set_a_score_by_direct_update(self):
        # The score is reportable, but only through the RPC (0003), never by
        # writing the column.
        self.assertBlockedWrite(
            f"matches?match_id=eq.{self.match_a}", token=self.tokens["a"],
            body={"home_goals": 7, "away_goals": 0, "status": "played"},
            table="matches", match_id=self.match_a, column="home_goals")

    def test_reporter_cannot_grant_themselves_a_competition(self):
        status, _body = _call(
            "reporter_assignments", token=self.tokens["a"], method="POST",
            body={"reporter_id": self.ids["a"], "competition_id": self.comp_b},
            prefer="return=minimal")
        self.assertIn(status, (401, 403, 404, 405))
        # And the boundary genuinely held.
        self.assertFalse(self.can_report("a", self.match_b))

    def test_reporter_cannot_make_themselves_an_admin(self):
        # They CAN read this row (reporters_read_own), which is what makes the
        # write worth testing: visibility is not permission.
        status, affected = _call(
            f"reporters?reporter_id=eq.{self.ids['a']}", token=self.tokens["a"],
            method="PATCH", body={"role": "admin"},
            prefer="return=representation")
        if status in (401, 403):
            affected = []
        self.assertEqual(affected, [])
        row = sb.select("reporters",
                        params={"reporter_id": f"eq.{self.ids['a']}"},
                        require_secret=True)[0]
        self.assertEqual(row["role"], "reporter")
        _status, is_admin = _call("rpc/is_admin", token=self.tokens["a"],
                                  method="POST", body={})
        self.assertFalse(is_admin)

    def test_reporter_cannot_reactivate_themselves(self):
        status, affected = _call(
            f"reporters?reporter_id=eq.{self.ids['inactive']}",
            token=self.tokens["inactive"], method="PATCH",
            body={"active": True}, prefer="return=representation")
        if status in (401, 403):
            affected = []
        self.assertEqual(affected, [])
        row = sb.select("reporters",
                        params={"reporter_id": f"eq.{self.ids['inactive']}"},
                        require_secret=True)[0]
        self.assertFalse(row["active"])


if __name__ == "__main__":
    unittest.main()
