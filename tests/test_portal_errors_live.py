"""Recording a screen that broke (0052).

    RLS_LIVE=1 python3 -m unittest tests.test_portal_errors_live

The client half is tested by tests/js/test_error_report.mjs, which can assert
what a browser on fire cannot. This is the other half:

  * the function NEVER RAISES for its actual callers. It runs from an error
    handler, and one that can itself fail turns a broken screen into an
    outage — so an inactive reporter, a caller over the cap and a call with
    nothing in it all return quietly rather than refusing;
  * the cap holds, because the bug that prompted the table threw on every
    draw and the client's dedupe is not trusted to be the only thing working;
  * a reporter cannot read what it collects, and anon cannot touch it at all.

Every row is deleted in teardown.
"""

import os
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from src import supabase_client as sb  # noqa: E402
from tests import live_support  # noqa: E402
from tests.live_support import call  # noqa: E402


def rpc(name, body, *, token):
    return call(f"rpc/{name}", token=token, method="POST", body=body)


# `token=None` MEANS ANON, and `token or self.tokens["a"]` behind a default of
# None silently runs the anon test as an authorized reporter — which is exactly
# how this file's first draft "passed" an anon check by recording a row as
# reporter A. test_import_matching_live carries the same sentinel and the same
# warning; it is worth repeating because the mistake is invisible when the
# assertion is only about a status code.
UNSET = object()


def delete(table, query):
    sb._request("DELETE", table, query=query,
                headers={"Prefer": "return=minimal"}, require_secret=True)


@unittest.skipUnless(live_support.available(), "Supabase credentials not configured")
class PortalErrorsTest(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        cls.identities = live_support.Identities(prefix="MW_ERRTEST").setup()
        cls.tokens = cls.identities.tokens
        cls.suffix = cls.identities.suffix

    @classmethod
    def tearDownClass(cls):
        for reporter_id in cls.identities.ids.values():
            delete("portal_errors", f"reporter_id=eq.{reporter_id}")
        cls.identities.teardown()

    def setUp(self):
        for reporter_id in self.identities.ids.values():
            delete("portal_errors", f"reporter_id=eq.{reporter_id}")

    # ── helpers ──────────────────────────────────────────────────────────────

    def record(self, message, route="/add", token=UNSET, stack="", agent="node"):
        return rpc("record_portal_error", {
            "p_route": route, "p_message": message,
            "p_stack": stack, "p_user_agent": agent,
        }, token=self.tokens["a"] if token is UNSET else token)

    def rows(self, label="a"):
        return sb.select(
            "portal_errors", columns="*",
            params={"reporter_id": f"eq.{self.identities.ids[label]}"},
            order="id.asc", require_secret=True)

    # ── the ordinary case ────────────────────────────────────────────────────

    def test_an_error_is_recorded_with_the_screen_it_happened_on(self):
        status, _ = self.record(
            "Cannot read properties of undefined (reading 'status')",
            stack="TypeError\\n  at gridRowHtml")
        self.assertIn(status, (200, 204))
        rows = self.rows()
        self.assertEqual(len(rows), 1, rows)
        self.assertEqual(rows[0]["route"], "/add")
        self.assertIn("Cannot read properties", rows[0]["message"])
        self.assertIn("gridRowHtml", rows[0]["stack"])
        self.assertEqual(rows[0]["reporter_id"], self.identities.ids["a"])

    def test_long_values_are_truncated_rather_than_refused(self):
        # A report that arrives shortened is worth immeasurably more than one
        # that does not arrive.
        self.record("m" * 900, route="/" + "r" * 300, stack="s" * 5000,
                    agent="u" * 900)
        row = self.rows()[0]
        self.assertEqual(len(row["message"]), 500)
        self.assertEqual(len(row["route"]), 100)
        self.assertEqual(len(row["stack"]), 2000)
        self.assertEqual(len(row["user_agent"]), 300)

    # ── it never raises ──────────────────────────────────────────────────────

    def test_anon_is_refused_by_the_grant(self):
        """Refused outright, and that is stronger than the check inside.

        EXECUTE is revoked from anon, so an error thrown on the sign-in screen
        is not recorded at all. That is a real gap and it is the trade 0052
        states: the alternative is an unauthenticated write endpoint on a
        public database. The "never raises" promise is about the function's
        actual callers — the client swallows this promise either way, so a 401
        here reaches nobody and causes nothing.
        """
        status, body = self.record("boom", token=None)
        self.assertGreaterEqual(status, 400, body)
        self.assertEqual(self.rows(), [])

    def test_an_inactive_reporter_is_not_an_error_either(self):
        status, body = self.record("boom", token=self.tokens["inactive"])
        self.assertIn(status, (200, 204), body)
        self.assertEqual(self.rows("inactive"), [])

    def test_nulls_are_not_an_error(self):
        status, body = rpc("record_portal_error", {}, token=self.tokens["a"])
        self.assertIn(status, (200, 204), body)

    # ── the cap ──────────────────────────────────────────────────────────────

    def test_a_render_loop_cannot_fill_the_table(self):
        # THE ASSERTION THIS FUNCTION EXISTS FOR. #/add threw on every draw;
        # the client dedupes, and this is what holds when the client is the
        # thing that is broken.
        for i in range(26):
            self.record(f"loop {i}")
        rows = self.rows()
        self.assertEqual(len(rows), 20, f"cap did not hold: {len(rows)} rows")

    # ── who can see it ───────────────────────────────────────────────────────

    def test_a_reporter_cannot_read_what_it_collects(self):
        self.record("something private-ish")
        for label in ("a", "b"):
            with self.subTest(identity=label):
                status, body = call("portal_errors?select=*", token=self.tokens[label])
                self.assertEqual(status, 200, body)
                # Not even their own: a stack trace is an operational artefact
                # and a reporter who sees one has been handed a worry they
                # cannot act on.
                self.assertEqual(body, [], f"{label} read portal_errors")

    def test_an_admin_can(self):
        self.record("visible to an admin")
        status, body = call(
            f"portal_errors?select=*&reporter_id=eq.{self.identities.ids['a']}",
            token=self.tokens["admin"])
        self.assertEqual(status, 200, body)
        self.assertEqual(len(body), 1, body)

    def test_anon_gets_nothing_from_the_table_and_is_refused_the_view(self):
        """Two different boundaries, and the difference is the schema's, not a
        gap here.

        The TABLE behaves like report_imports, match_change_log and reporters:
        this project's default privileges grant select to anon on a new table,
        RLS denies every row, and anon gets an empty list. The VIEW is revoked
        outright, because that is what every ops_* view does — 0016 and 0039
        say so, and 0048 exists because 0047 forgot.
        """
        self.record("hidden from anon")
        status, body = call("portal_errors?select=*", token=None)
        self.assertEqual(status, 200, body)
        self.assertEqual(body, [], "a row leaked to anon")

        status, body = call("ops_portal_errors?select=*", token=None)
        self.assertGreaterEqual(status, 400, body)

    # ── the grouped view ─────────────────────────────────────────────────────

    def test_the_view_is_one_row_per_broken_screen(self):
        for _ in range(5):
            self.record("the same error every draw")
        self.record("the same error every draw", token=self.tokens["b"])
        self.record("a different problem", route="/teams")

        status, body = call("ops_portal_errors?select=*", token=self.tokens["admin"])
        self.assertEqual(status, 200, body)
        rows = {(r["route"], r["message"]): r for r in body}
        same = rows[("/add", "the same error every draw")]
        # Six hits from two people, on one line — which is what an
        # administrator needs to read, rather than six rows of it.
        self.assertEqual(same["hits"], 6)
        self.assertEqual(same["reporters"], 2)
        self.assertTrue(same["first_seen"] <= same["last_seen"])
        self.assertIn(("/teams", "a different problem"), rows)

    def test_a_reporter_sees_nothing_in_the_view(self):
        self.record("hidden")
        status, body = call("ops_portal_errors?select=*", token=self.tokens["a"])
        self.assertEqual(status, 200, body)
        self.assertEqual(body, [])


if __name__ == "__main__":
    unittest.main()
