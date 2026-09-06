"""How a result was submitted, recorded and readable (0047).

    RLS_LIVE=1 python3 -m unittest tests.test_submission_channel_live

WHAT THESE PIN. match_change_log.source defaulted to 'reporter' for eighteen
months and nothing ever wrote anything else, which is how a result a model read
off a blurry photograph became indistinguishable from one typed by hand. The
column is only worth having if every path actually fills it in, and if the one
value that can be checked — the import — is checked rather than believed.

  * each of the three paths writes its own channel, and the import path also
    writes the import it came from;
  * an import_id that does not exist, or belongs to somebody else, is REFUSED
    rather than recorded, which is the difference between an audit log and a
    collection of claims;
  * a no-op republish writes no row at all. That is not a bug being tolerated,
    it is the rule the screen's wording depends on: "everything submitted
    today" is everything that changed today;
  * the four-argument call every browser in the field makes still resolves
    after the drop-and-recreate.

The fixture is a throwaway match in a real competition (source_type
'placeholder', so it renders nowhere even if one leaks) and it is reset before
every test. Nothing here touches a real result.
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


def message(body):
    if isinstance(body, dict):
        return body.get("message") or body.get("hint") or str(body)
    return str(body)


def delete(table, query):
    sb._request("DELETE", table, query=query,
                headers={"Prefer": "return=minimal"}, require_secret=True)


def patch(table, query, body):
    sb._request("PATCH", table, query=query, body=body,
                headers={"Prefer": "return=minimal"}, require_secret=True)


@unittest.skipUnless(live_support.available(), "Supabase credentials not configured")
class SubmissionChannelTest(unittest.TestCase):

    COMP = live_support.Identities.COMP_A     # MW_NRFA — 'a' is assigned

    @classmethod
    def setUpClass(cls):
        cls.identities = live_support.Identities(prefix="MW_CHANTEST").setup()
        cls.tokens = cls.identities.tokens
        cls.suffix = cls.identities.suffix
        cls.match = live_support.make_test_match(cls.COMP, cls.suffix)
        cls.match_id = cls.match["match_id"]
        cls.imports = []

    @classmethod
    def tearDownClass(cls):
        delete("match_change_log", f"match_id=eq.{cls.match_id}")
        live_support.drop_test_match(cls.match_id)
        for label in ("a", "b"):
            delete("report_imports", f"reporter_id=eq.{cls.identities.ids[label]}")
        cls.identities.teardown()

    def setUp(self):
        """Every test starts from an unreported fixture.

        The tests share one match — creating a fixture per test would mean a
        fixture per test in a real competition's table — so the state one
        leaves behind would otherwise decide what the next one is even
        testing: a second 2–1 onto a match that already says 2–1 changes
        nothing and writes no log row, which is precisely one of the rules
        below and precisely the wrong accident to have elsewhere.
        """
        delete("match_change_log", f"match_id=eq.{self.match_id}")
        patch("matches", f"match_id=eq.{self.match_id}", {
            "status": "scheduled", "home_goals": None, "away_goals": None,
            "source_ref": "", "reported_by": None, "reported_at": None,
        })

    # ── helpers ──────────────────────────────────────────────────────────────

    def log(self):
        return sb.select(
            "match_change_log",
            columns="id,match_id,changed_by,source,import_id,old_values,new_values",
            params={"match_id": f"eq.{self.match_id}"},
            order="id.asc", require_secret=True)

    def single(self, home, away, token=None, source="a live test"):
        return rpc("submit_match_report", {
            "p_match_id": self.match_id,
            "p_home_score": home, "p_away_score": away,
            "p_status": "played", "p_source_ref": source,
        }, token=token or self.tokens["a"])

    def batch(self, home, away, token=None, extra=None):
        body = {
            "p_competition_id": self.COMP,
            "p_season_id": self.match["season_id"],
            "p_source_ref": "a live test",
            "p_reports": [{"match_id": self.match_id, "status": "played",
                           "home": home, "away": away}],
        }
        body.update(extra or {})
        return rpc("submit_match_reports", body, token=token or self.tokens["a"])

    def an_import(self, label="a"):
        """A real import row, opened the way the portal opens one."""
        status, body = rpc("create_report_import", {
            "p_channel": "text",
            "p_pasted_text": f"channel test {self.suffix}",
        }, token=self.tokens[label])
        self.assertEqual(status, 200, body)
        self.imports.append(body)
        return body

    # ── the three channels ───────────────────────────────────────────────────

    def test_the_single_match_screen_says_single(self):
        status, body = self.single(2, 1)
        self.assertEqual(status, 200, body)
        rows = self.log()
        self.assertEqual(len(rows), 1, rows)
        self.assertEqual(rows[0]["source"], "single")
        self.assertIsNone(rows[0]["import_id"])

    def test_the_grid_says_grid(self):
        status, body = self.batch(3, 0)
        self.assertEqual(status, 200, body)
        self.assertTrue(body[0]["ok"], body)
        rows = self.log()
        self.assertEqual(len(rows), 1, rows)
        self.assertEqual(rows[0]["source"], "grid")
        self.assertIsNone(rows[0]["import_id"])

    def test_an_import_says_import_and_names_it(self):
        import_id = self.an_import()
        status, body = self.batch(1, 1, extra={"p_import_id": import_id})
        self.assertEqual(status, 200, body)
        self.assertTrue(body[0]["ok"], body)
        rows = self.log()
        self.assertEqual(len(rows), 1, rows)
        self.assertEqual(rows[0]["source"], "import")
        # The evidence, one join away. This is the whole point: the extraction
        # that produced this scoreline is now findable from the scoreline.
        self.assertEqual(rows[0]["import_id"], import_id)

    # ── the import is checked, not believed ──────────────────────────────────

    def test_an_import_that_does_not_exist_is_refused(self):
        status, body = self.batch(
            2, 2, extra={"p_import_id": "00000000-0000-0000-0000-000000000000"})
        self.assertNotEqual(status, 200, body)
        self.assertIn("no longer exists", message(body))
        # And nothing was published: the refusal is before the loop, so the
        # whole call fails rather than half of it landing unattributed.
        self.assertEqual(self.log(), [])

    def test_another_reporters_import_is_refused(self):
        import_id = self.an_import("b")
        status, body = self.batch(2, 2, extra={"p_import_id": import_id})
        self.assertNotEqual(status, 200, body)
        self.assertIn("belongs to another reporter", message(body))
        self.assertEqual(self.log(), [])

    def test_an_admin_may_publish_from_someone_elses_import(self):
        # The same rule resolve_and_save_import (0045) has: an admin helping
        # with an import that went wrong is a real thing, and the row still
        # names the import it actually came from.
        import_id = self.an_import("a")
        status, body = self.batch(4, 2, token=self.tokens["admin"],
                                  extra={"p_import_id": import_id})
        self.assertEqual(status, 200, body)
        self.assertTrue(body[0]["ok"], body)
        rows = self.log()
        self.assertEqual(rows[0]["source"], "import")
        self.assertEqual(rows[0]["import_id"], import_id)

    # ── what does and does not get written ───────────────────────────────────

    def test_republishing_an_unchanged_result_writes_nothing(self):
        self.assertEqual(self.batch(2, 1)[0], 200)
        self.assertEqual(len(self.log()), 1)
        # Same score again — a dropped connection and a second tap. The update
        # still refreshes reported_by/reported_at; the log does not grow, which
        # is what makes the Submitted screen's count mean "what changed".
        self.assertEqual(self.batch(2, 1)[0], 200)
        self.assertEqual(len(self.log()), 1)

    def test_a_correction_is_a_second_row(self):
        self.assertEqual(self.batch(2, 1)[0], 200)
        self.assertEqual(self.batch(3, 1)[0], 200)
        rows = self.log()
        self.assertEqual(len(rows), 2, rows)
        self.assertEqual(rows[1]["old_values"]["home_goals"], 2)
        self.assertEqual(rows[1]["new_values"]["home_goals"], 3)

    def test_the_old_four_argument_call_still_resolves(self):
        # submit_match_reports was dropped and recreated to gain p_import_id.
        # Every browser in the field sends exactly these four named arguments,
        # and PostgREST resolving them to the new function with the fifth
        # defaulted is the whole reason that was safe to do.
        status, body = rpc("submit_match_reports", {
            "p_competition_id": self.COMP,
            "p_reports": [{"match_id": self.match_id, "status": "played",
                           "home": 0, "away": 0}],
            "p_source_ref": "a live test",
            "p_season_id": self.match["season_id"],
        }, token=self.tokens["a"])
        self.assertEqual(status, 200, body)
        self.assertTrue(body[0]["ok"], body)
        self.assertEqual(self.log()[0]["source"], "grid")

    # ── ops_submissions ──────────────────────────────────────────────────────

    def test_the_screen_shows_what_was_submitted(self):
        import_id = self.an_import()
        self.assertEqual(self.batch(5, 0, extra={"p_import_id": import_id})[0], 200)

        status, body = call(
            f"ops_submissions?select=*&match_id=eq.{self.match_id}",
            token=self.tokens["admin"])
        self.assertEqual(status, 200, body)
        self.assertEqual(len(body), 1, body)
        row = body[0]
        self.assertEqual(row["new_home"], 5)
        self.assertEqual(row["new_away"], 0)
        self.assertEqual(row["new_status"], "played")
        self.assertEqual(row["source"], "import")
        self.assertEqual(row["import_id"], import_id)
        # The channel the import itself arrived by, which is what turns "AI
        # import" into "read from pasted text".
        self.assertEqual(row["import_channel"], "text")
        self.assertEqual(row["source_ref"], "a live test")
        self.assertEqual(row["changed_by"], self.identities.ids["a"])
        self.assertEqual(row["reporter_name"], "Live a")
        self.assertTrue(row["home_name"], row)
        self.assertTrue(row["day"], row)

    def test_a_result_is_labelled_a_result(self):
        self.assertEqual(self.batch(2, 2)[0], 200)
        status, body = call(
            f"ops_submissions?select=kind&match_id=eq.{self.match_id}",
            token=self.tokens["admin"])
        self.assertEqual(status, 200, body)
        self.assertEqual(body[0]["kind"], "result")

    def test_a_reschedule_is_not_a_result_with_the_score_missing(self):
        """The log has seven writers and only one of them reports a result.

        Before 0047 gave the view a `kind`, a reschedule row would have come
        back with every score column null and rendered as an empty line — a
        change nobody could read, filed under the heading of a thing it is not.
        """
        dates = live_support.season_dates(2, self.match["season_id"])
        status, body = rpc("reschedule_match", {
            "p_match_id": self.match_id, "p_date": dates[1], "p_kickoff": "15:00",
        }, token=self.tokens["a"])
        self.assertEqual(status, 200, body)

        status, body = call(
            f"ops_submissions?select=*&match_id=eq.{self.match_id}",
            token=self.tokens["admin"])
        self.assertEqual(status, 200, body)
        row = body[0]
        self.assertEqual(row["kind"], "reschedule")
        self.assertIsNone(row["new_status"])
        # The payload is carried through, because a reschedule's whole content
        # is its own keys and the screen renders them.
        self.assertEqual(row["new_values"]["date"], dates[1])
        self.assertEqual(row["new_values"]["kickoff"], "15:00")
        # And it says nothing about a channel, because the function that wrote
        # it records none.
        self.assertEqual(row["source"], "reporter")

    def test_a_first_report_says_what_it_replaced(self):
        self.assertEqual(self.batch(1, 0)[0], 200)
        status, body = call(
            f"ops_submissions?select=*&match_id=eq.{self.match_id}",
            token=self.tokens["admin"])
        self.assertEqual(status, 200, body)
        # 'scheduled' with no goals — which the screen reads as "this replaced
        # nothing" and renders as no "was …" clause at all.
        self.assertEqual(body[0]["old_status"], "scheduled")
        self.assertIsNone(body[0]["old_home"])

    def test_a_reporter_sees_nothing_in_it(self):
        self.assertEqual(self.batch(1, 2)[0], 200)
        for label in ("a", "inactive"):
            with self.subTest(identity=label):
                status, body = call("ops_submissions?select=*",
                                    token=self.tokens[label])
                self.assertEqual(status, 200, body)
                self.assertEqual(body, [], f"{label} saw rows")

    def test_anon_is_refused(self):
        status, body = call("ops_submissions?select=*", token=None)
        self.assertGreaterEqual(status, 400, body)


if __name__ == "__main__":
    unittest.main()
