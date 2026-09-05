"""submit_match_reports: a whole matchday published in one call (0041).

These attack the RPC directly rather than through the grid, for the same
reason test_reporting_live and test_entry_live do — the function is the
security boundary, and the client is not trusted.

    RLS_LIVE=1 python3 -m unittest tests.test_results_grid_live

The behaviour worth pinning is not "it updates n rows". It is:

  * that the batch path enforces EXACTLY what the single path enforces, since
    0041 exists to stop those two drifting — a rule relaxed here would be a
    build failure everyone else pays for (validate.py check 4);
  * that one bad line does not cost the good ones, which is the whole reason
    the function is shaped this way;
  * that the one-time authorization done before the loop cannot be widened by
    putting another competition's match_id on a line. That is the only genuinely
    new attack surface this migration opens, and it is the reason the per-row
    competition+season pin exists.

Nothing here touches a real result. Each run creates its own fixture rows in a
real competition and deletes them again, because proving a policy works is not
a good enough reason to rewrite a genuine scoreline. Every fixture carries
source_type='placeholder', which renders nowhere even if one leaks.
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
    """(status, body), never an exception — the shape live_support.call uses."""
    return call(f"rpc/{name}", token=token, method="POST", body=body)


def message(body):
    if isinstance(body, dict):
        return body.get("message") or body.get("hint") or str(body)
    return str(body)


def delete(table, query):
    sb._request("DELETE", table, query=query,
                headers={"Prefer": "return=minimal"}, require_secret=True)


def make_matches(competition_id, suffix, count):
    """`count` throwaway fixtures in a real competition.

    live_support.make_test_match mints ONE per competition (its id is fixed by
    the suffix), and a batch needs several — so this is that function with an
    index in the id. Both teams must already hold an entries row for the
    competition+season: the composite foreign key added in 0001 enforces
    validate.py check 3, so the sides are taken from existing entries rather
    than invented.
    """
    entries = sb.select(
        "entries", columns="team_id,season_id",
        params={"competition_id": f"eq.{competition_id}"},
        order="ord.asc", require_secret=True)
    if len(entries) < 2:
        raise unittest.SkipTest(f"{competition_id} has too few entries")
    season = entries[0]["season_id"]
    rows = []
    for i in range(count):
        # Pairs walk the entry list so no two test fixtures share a pairing;
        # a competition with only two entries reuses them, which is fine —
        # nothing here depends on the pairing being distinct.
        home = entries[(2 * i) % len(entries)]["team_id"]
        away = entries[(2 * i + 1) % len(entries)]["team_id"]
        if home == away:
            away = entries[(2 * i + 2) % len(entries)]["team_id"]
        rows.append({
            "match_id": f"MW_GRIDTEST_{suffix}_{competition_id}_{i}",
            "competition_id": competition_id,
            "season_id": season,
            "home_team_id": home,
            "away_team_id": away,
            "stage": "md_1",
            "status": "scheduled",
            "source_type": "placeholder",   # renders nowhere even if one leaks
            "confidence": "unconfirmed",
            "ord": 0,
        })
    sb.upsert("matches", rows, on_conflict="match_id")
    return [r["match_id"] for r in rows], season


@unittest.skipUnless(live_support.available(), "Supabase credentials not configured")
class SubmitMatchReportsTest(unittest.TestCase):

    COMP = live_support.Identities.COMP_A   # MW_NRFA — 'a' is assigned to it
    OTHER = live_support.Identities.COMP_B  # MW_SRFA — 'a' is NOT

    @classmethod
    def setUpClass(cls):
        cls.identities = live_support.Identities(prefix="MW_GRIDTEST").setup()
        cls.tokens = cls.identities.tokens
        suffix = cls.identities.suffix
        cls.ids, cls.season = make_matches(cls.COMP, suffix, 4)
        # One in a competition 'a' cannot report, for the pin test below.
        cls.other_ids, cls.other_season = make_matches(cls.OTHER, suffix, 1)

    @classmethod
    def tearDownClass(cls):
        for match_id in cls.ids + cls.other_ids:
            delete("match_change_log", f"match_id=eq.{match_id}")
            live_support.drop_test_match(match_id)
        cls.identities.teardown()

    def setUp(self):
        # Each test starts from unreported fixtures.
        for match_id in self.ids + self.other_ids:
            sb._request("PATCH", "matches",
                        query=f"match_id=eq.{match_id}",
                        body={"home_goals": None, "away_goals": None,
                              "status": "scheduled", "reported_by": None,
                              "reported_at": None, "source_ref": "",
                              "source_type": "placeholder",
                              "confidence": "unconfirmed",
                              "verified_by": None, "verified_at": None},
                        headers={"Prefer": "return=minimal"},
                        require_secret=True)
            delete("match_change_log", f"match_id=eq.{match_id}")

    # ── helpers ──────────────────────────────────────────────────────────────

    def send(self, token, reports, competition=None, **kwargs):
        body = {"p_competition_id": competition or self.COMP,
                "p_reports": reports}
        body.update(kwargs)
        return rpc("submit_match_reports", body, token=token)

    def send_ok(self, token, reports, **kwargs):
        status, body = self.send(token, reports, **kwargs)
        self.assertEqual(status, 200, body)
        return body

    def row(self, match_id):
        rows = sb.select("matches", columns="*",
                         params={"match_id": f"eq.{match_id}"},
                         require_secret=True)
        self.assertTrue(rows, match_id)
        return rows[0]

    def log(self, match_id):
        return sb.select("match_change_log", columns="*",
                         params={"match_id": f"eq.{match_id}"},
                         order="changed_at.asc", require_secret=True)

    def played(self, match_id, home, away, **extra):
        report = {"match_id": match_id, "home": home, "away": away,
                  "status": "played"}
        report.update(extra)
        return report

    # ── several valid results together ───────────────────────────────────────

    def test_a_whole_matchday_lands_in_one_call(self):
        rows = self.send_ok(self.tokens["a"], [
            self.played(self.ids[0], 2, 1),
            self.played(self.ids[1], 0, 0),
            {"match_id": self.ids[2], "status": "postponed"},
        ], p_source_ref="https://facebook.com/post/1")

        self.assertEqual([r["idx"] for r in rows], [1, 2, 3])
        self.assertTrue(all(r["ok"] for r in rows), rows)
        self.assertEqual([r["home_goals"] for r in rows], [2, 0, None])
        self.assertEqual([r["status"] for r in rows],
                         ["played", "played", "postponed"])

        first = self.row(self.ids[0])
        self.assertEqual((first["home_goals"], first["away_goals"]), (2, 1))
        self.assertEqual(first["status"], "played")

    def test_it_preserves_the_single_paths_provenance_rules(self):
        """0041 must not have changed what publishing MEANS.

        source_type='reporter', reported_by set, and 0029's rule that any
        authorized reporter's own result is confirmed and verified by them.
        """
        self.send_ok(self.tokens["a"], [self.played(self.ids[0], 3, 0)],
                     p_source_ref="League official")
        row = self.row(self.ids[0])
        self.assertEqual(row["source_type"], "reporter")
        self.assertEqual(row["source_ref"], "League official")
        self.assertEqual(row["reported_by"], self.identities.ids["a"])
        self.assertEqual(row["confidence"], "confirmed")
        self.assertEqual(row["verified_by"], self.identities.ids["a"])
        self.assertIsNotNone(row["verified_at"])

    def test_every_change_is_audited_once(self):
        self.send_ok(self.tokens["a"], [self.played(self.ids[0], 1, 0)])
        log = self.log(self.ids[0])
        self.assertEqual(len(log), 1, log)
        self.assertEqual(log[0]["changed_by"], self.identities.ids["a"])
        self.assertEqual(log[0]["new_values"]["home_goals"], 1)
        self.assertEqual(log[0]["old_values"]["status"], "scheduled")

    def test_publishing_cannot_move_the_fixture(self):
        """The narrow-update guarantee, re-asked of the batch path.

        A generic UPDATE is exactly what 0003 refused, and a batch is not a
        reason to grant one. Anything structural sent on a line is ignored.
        """
        before = self.row(self.ids[0])
        self.send_ok(self.tokens["a"], [{
            "match_id": self.ids[0], "home": 1, "away": 1, "status": "played",
            # None of these are parameters of the function. If any of them ever
            # became one, this test is what says so.
            "date": "2026-01-01", "home_team_id": "MW_NOPE",
            "competition_id": self.OTHER, "matchday": 99, "venue_id": "MW_NOPE",
        }])
        after = self.row(self.ids[0])
        for column in ("date", "home_team_id", "away_team_id", "competition_id",
                       "season_id", "kickoff", "venue_id", "stage", "matchday"):
            self.assertEqual(before[column], after[column], column)
        self.assertEqual(after["home_goals"], 1)

    # ── partial failure ──────────────────────────────────────────────────────

    def test_a_bad_line_does_not_cost_the_good_ones(self):
        """The whole reason this is not all-or-nothing."""
        rows = self.send_ok(self.tokens["a"], [
            self.played(self.ids[0], 2, 1),
            # A played result with one score missing — validate.py check 4.
            {"match_id": self.ids[1], "home": 2, "status": "played"},
            self.played(self.ids[2], 1, 1),
        ], p_source_ref="League official")

        self.assertEqual([r["ok"] for r in rows], [True, False, True])
        self.assertIn("needs both scores", rows[1]["message"])
        self.assertEqual(self.row(self.ids[0])["home_goals"], 2)
        self.assertEqual(self.row(self.ids[2])["home_goals"], 1)
        # The failed one is untouched, not half-written.
        middle = self.row(self.ids[1])
        self.assertIsNone(middle["home_goals"])
        self.assertEqual(middle["status"], "scheduled")

    def test_the_index_says_which_line(self):
        """The client puts the message back on the line it belongs to."""
        rows = self.send_ok(self.tokens["a"], [
            {"match_id": self.ids[0], "status": "nonsense"},
            self.played(self.ids[1], 1, 0),
        ], p_source_ref="League official")
        self.assertEqual(rows[0]["idx"], 1)
        self.assertFalse(rows[0]["ok"])
        self.assertEqual(rows[0]["match_id"], self.ids[0])
        self.assertIn("invalid status", rows[0]["message"])
        self.assertEqual(rows[1]["idx"], 2)
        self.assertTrue(rows[1]["ok"], rows[1])

    def test_an_unknown_match_fails_only_its_own_line(self):
        rows = self.send_ok(self.tokens["a"], [
            {"match_id": "MW_NO_SUCH_MATCH", "home": 1, "away": 0,
             "status": "played"},
            self.played(self.ids[0], 1, 0),
        ], p_source_ref="League official")
        self.assertEqual([r["ok"] for r in rows], [False, True])
        self.assertIn("match not found", rows[0]["message"])

    def test_a_line_with_no_match_id_fails_only_itself(self):
        rows = self.send_ok(self.tokens["a"], [
            {"home": 1, "away": 0, "status": "played"},
            self.played(self.ids[0], 1, 0),
        ], p_source_ref="League official")
        self.assertEqual([r["ok"] for r in rows], [False, True])
        self.assertIn("no match on it", rows[0]["message"])

    # ── authorization ────────────────────────────────────────────────────────

    def test_a_reporter_cannot_publish_another_competition(self):
        status, body = self.send(self.tokens["b"], [self.played(self.ids[0], 1, 0)])
        self.assertEqual(status, 403, body)
        self.assertIn("not assigned", message(body))
        self.assertIsNone(self.row(self.ids[0])["home_goals"])

    def test_an_inactive_reporter_cannot_publish(self):
        status, body = self.send(self.tokens["inactive"],
                                 [self.played(self.ids[0], 1, 0)])
        self.assertEqual(status, 403, body)
        self.assertIn("inactive", message(body))

    def test_anon_cannot_call_the_rpc(self):
        status, body = rpc("submit_match_reports", {
            "p_competition_id": self.COMP,
            "p_reports": [self.played(self.ids[0], 1, 0)],
        }, token=None)
        self.assertIn(status, (401, 403, 404), body)
        self.assertIsNone(self.row(self.ids[0])["home_goals"])

    def test_an_admin_can_publish_any_competition(self):
        rows = self.send_ok(self.tokens["admin"],
                            [self.played(self.other_ids[0], 4, 0)],
                            competition=self.OTHER,
                            p_season_id=self.other_season,
                            p_source_ref="League official")
        self.assertTrue(rows[0]["ok"], rows[0])

    def test_a_match_from_another_competition_cannot_ride_along(self):
        """THE ONE NEW ATTACK SURFACE, AND THE REASON FOR THE PER-ROW PIN.

        Authorization is done once, before the loop, against the competition
        the CLIENT NAMED. Without pinning each row to that competition and
        season, a reporter assigned to one league could publish into another
        simply by putting its match_id on a line — the check would have passed
        on a competition that line has nothing to do with.
        """
        rows = self.send_ok(self.tokens["a"], [
            self.played(self.ids[0], 1, 0),
            self.played(self.other_ids[0], 9, 0),   # MW_SRFA — 'a' is not assigned
        ], p_source_ref="League official")

        self.assertEqual([r["ok"] for r in rows], [True, False])
        self.assertIn("not in this competition", rows[1]["message"])
        self.assertIsNone(self.row(self.other_ids[0])["home_goals"])

    def test_only_an_admin_can_award_a_match(self):
        rows = self.send_ok(self.tokens["a"], [
            {"match_id": self.ids[0], "home": 3, "away": 0, "status": "awarded"},
        ], p_source_ref="League official")
        self.assertFalse(rows[0]["ok"])
        self.assertIn("only an administrator", rows[0]["message"])

        rows = self.send_ok(self.tokens["admin"], [
            {"match_id": self.ids[0], "home": 3, "away": 0, "status": "awarded"},
        ], p_source_ref="League official")
        self.assertTrue(rows[0]["ok"], rows[0])
        self.assertEqual(self.row(self.ids[0])["status"], "awarded")

    # ── score and status validation ──────────────────────────────────────────

    def test_a_postponed_or_cancelled_result_cannot_carry_a_score(self):
        for status in ("postponed", "cancelled", "abandoned", "scheduled"):
            with self.subTest(status=status):
                rows = self.send_ok(self.tokens["a"], [
                    {"match_id": self.ids[0], "home": 1, "away": 0,
                     "status": status},
                ], p_source_ref="League official")
                self.assertFalse(rows[0]["ok"], rows[0])
                self.assertIn("cannot carry a score", rows[0]["message"])
                self.assertEqual(self.row(self.ids[0])["status"], "scheduled")

    def test_a_negative_or_absurd_score_is_refused(self):
        for home, away in ((-1, 0), (0, 100), (999, 1)):
            with self.subTest(score=(home, away)):
                rows = self.send_ok(
                    self.tokens["a"], [self.played(self.ids[0], home, away)],
                    p_source_ref="League official")
                self.assertFalse(rows[0]["ok"], rows[0])
                self.assertIn("between 0 and 99", rows[0]["message"])

    def test_the_whole_call_is_refused_when_the_list_is_wrong(self):
        for reports, expected in (([], "at least one result"),
                                  ([{}] * 61, "in two goes")):
            with self.subTest(n=len(reports)):
                status, body = self.send(self.tokens["a"], reports,
                                         p_source_ref="League official")
                self.assertEqual(status, 400, body)
                self.assertIn(expected, message(body))

    # ── the shared source ────────────────────────────────────────────────────

    def test_the_shared_source_reaches_every_row(self):
        self.send_ok(self.tokens["a"], [
            self.played(self.ids[0], 1, 0),
            self.played(self.ids[1], 2, 2),
        ], p_source_ref="Witnessed at the match")
        for match_id in self.ids[:2]:
            self.assertEqual(self.row(match_id)["source_ref"],
                             "Witnessed at the match")

    def test_a_blank_shared_source_keeps_each_rows_own(self):
        """The rule that makes one shared box safe on a screen full of history.

        A matchday republished from a screen whose source box is empty must not
        erase the links somebody recorded last week.
        """
        sb._request("PATCH", "matches", query=f"match_id=eq.{self.ids[0]}",
                    body={"source_ref": "https://example.com/original"},
                    headers={"Prefer": "return=minimal"}, require_secret=True)
        self.send_ok(self.tokens["a"], [self.played(self.ids[0], 1, 0)],
                     p_source_ref="")
        self.assertEqual(self.row(self.ids[0])["source_ref"],
                         "https://example.com/original")

    def test_an_overlong_source_is_truncated_not_rejected(self):
        self.send_ok(self.tokens["a"], [self.played(self.ids[0], 1, 0)],
                     p_source_ref="x" * 900)
        self.assertEqual(len(self.row(self.ids[0])["source_ref"]), 500)

    # ── the conflict guard ───────────────────────────────────────────────────

    def test_expect_lets_a_row_through_when_nothing_has_moved(self):
        rows = self.send_ok(self.tokens["a"], [
            dict(self.played(self.ids[0], 2, 1),
                 expect={"status": "scheduled", "home": None, "away": None}),
        ], p_source_ref="League official")
        self.assertTrue(rows[0]["ok"], rows[0])

    def test_expect_refuses_a_row_someone_else_has_published(self):
        """The race the grid is exposed to: a matchday is loaded, filled in
        slowly on a weak connection, and someone else publishes one of the same
        matches in between. Without this the last write silently wins, which is
        the one outcome nobody can detect afterwards."""
        self.send_ok(self.tokens["admin"], [self.played(self.ids[0], 0, 0)],
                     p_source_ref="League official")

        rows = self.send_ok(self.tokens["a"], [
            # The client still believes this fixture is unreported.
            dict(self.played(self.ids[0], 2, 1),
                 expect={"status": "scheduled", "home": None, "away": None}),
            self.played(self.ids[1], 1, 0),
        ], p_source_ref="League official")

        self.assertEqual([r["ok"] for r in rows], [False, True])
        self.assertIn("someone else published", rows[0]["message"])
        # It names what is actually there, so the reporter can decide.
        self.assertIn("0–0", rows[0]["message"])
        # ...and the other row still published.
        self.assertEqual(self.row(self.ids[1])["home_goals"], 1)
        # The refused row is untouched.
        self.assertEqual(self.row(self.ids[0])["home_goals"], 0)

    def test_omitting_expect_overwrites_deliberately(self):
        """A confirmed correction. This is what the grid's "Replace 2–1" tap
        produces, and it is exactly what submit_match_report has always done."""
        self.send_ok(self.tokens["a"], [self.played(self.ids[0], 1, 1)],
                     p_source_ref="League official")
        rows = self.send_ok(self.tokens["a"], [self.played(self.ids[0], 2, 1)],
                            p_source_ref="League official")
        self.assertTrue(rows[0]["ok"], rows[0])
        self.assertEqual(self.row(self.ids[0])["home_goals"], 2)
        # Both are in the log: the correction did not destroy the first claim.
        log = self.log(self.ids[0])
        self.assertEqual(len(log), 2, log)
        self.assertEqual(log[0]["new_values"]["home_goals"], 1)
        self.assertEqual(log[1]["old_values"]["home_goals"], 1)
        self.assertEqual(log[1]["new_values"]["home_goals"], 2)

    # ── retry / idempotency ──────────────────────────────────────────────────

    def test_resending_the_same_matchday_adds_no_audit_noise(self):
        """The retry after a dropped connection. Re-publishing an unchanged
        result is a no-op worth attributing and not worth a log row."""
        reports = [self.played(self.ids[0], 2, 1), self.played(self.ids[1], 0, 0)]
        self.send_ok(self.tokens["a"], reports, p_source_ref="League official")
        self.assertEqual(len(self.log(self.ids[0])), 1)

        rows = self.send_ok(self.tokens["a"], reports,
                            p_source_ref="League official")
        self.assertTrue(all(r["ok"] for r in rows), rows)
        self.assertEqual(len(self.log(self.ids[0])), 1,
                         "an unchanged resend must not add a log row")
        self.assertEqual(self.row(self.ids[0])["home_goals"], 2)

    # ── the single path still works ──────────────────────────────────────────

    def test_submit_match_report_is_unchanged_by_the_refactor(self):
        """0041 rewrote submit_match_report's body. Its contract must not have
        moved — every browser in the field is still calling it."""
        status, body = rpc("submit_match_report", {
            "p_match_id": self.ids[0], "p_home_score": 3, "p_away_score": 2,
            "p_status": "played", "p_source_ref": "https://example.com/x",
        }, token=self.tokens["a"])
        self.assertEqual(status, 200, body)
        row = self.row(self.ids[0])
        self.assertEqual((row["home_goals"], row["away_goals"]), (3, 2))
        self.assertEqual(row["source_ref"], "https://example.com/x")
        self.assertEqual(row["confidence"], "confirmed")
        self.assertEqual(len(self.log(self.ids[0])), 1)

    def test_the_single_path_still_refuses_what_it_always_did(self):
        for body, expected in (
            ({"p_match_id": self.ids[0], "p_home_score": None,
              "p_away_score": None, "p_status": "played"}, "needs both scores"),
            ({"p_match_id": self.ids[0], "p_home_score": 1, "p_away_score": 0,
              "p_status": "postponed"}, "cannot carry a score"),
            ({"p_match_id": self.ids[0], "p_home_score": 1, "p_away_score": 0,
              "p_status": "awarded"}, "only an administrator"),
        ):
            with self.subTest(status=body["p_status"]):
                status, got = rpc("submit_match_report", body,
                                  token=self.tokens["a"])
                self.assertIn(status, (400, 403), got)
                self.assertIn(expected, message(got))

    def test_apply_match_report_is_not_reachable_from_the_api(self):
        """It does no authorization — its callers do. Granted to nobody."""
        for token in (self.tokens["a"], self.tokens["admin"], None):
            status, body = rpc("apply_match_report", {}, token=token)
            self.assertIn(status, (401, 403, 404), body)


if __name__ == "__main__":
    unittest.main()
