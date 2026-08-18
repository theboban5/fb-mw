"""The ops dashboard views, exercised over the network with real identities.

Two things are proved here, and the second is the one that would otherwise rot.

  1. ACCESS. anon, an ordinary reporter, an inactive reporter and an
     administrator each ask PostgREST for every ops object. The reporter must
     get nothing — not a filtered subset, nothing — and anon must be refused
     outright. As in test_rls_live.py, the identities are genuine auth users
     signed in over the network for real JWTs, not a simulation.

  2. ARITHMETIC. Every count the dashboard shows is recomputed here in Python
     from the raw matches and goals rows, and compared with what the view says.
     Re-running the view's own SQL would prove only that Postgres is
     deterministic. The point is to catch a predicate that is subtly wrong —
     a 0-0 draw counted as missing its scorers, a postponed fixture counted as
     overdue — which is exactly the class of error the definitions in
     OPS_DASHBOARD_PLAN.md exist to pin down.

It creates real auth users and reads production data, so it is opt-in:

    RLS_LIVE=1 python3 -m unittest tests.test_ops_live

Every fixture it creates is torn down again. It writes no football data.
"""

import datetime
import os
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from src import supabase_client as sb  # noqa: E402
from tests import live_support  # noqa: E402
from tests.live_support import call as _call  # noqa: E402

# Every object 0016 creates. Kept as one list so a new view added later without
# a matching access test is an obvious omission rather than a silent gap.
OPS_VIEWS = (
    "ops_match_flags",
    "ops_competition_expectation",
    "ops_matchday_status",
    "ops_competition_summary",
    "ops_dashboard_totals",
)
OPS_TABLES = ("ops_competition_settings",)


def cat_today():
    """Today in Malawi — CAT, UTC+2, no DST. Mirrors public.ops_today()."""
    now = datetime.datetime.now(datetime.timezone.utc)
    return (now + datetime.timedelta(hours=2)).date().isoformat()


@unittest.skipUnless(live_support.available(), "Supabase credentials not configured")
class OpsDashboardTest(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        cls.identities = live_support.Identities(prefix="MW_OPSTEST").setup()
        cls.tokens = cls.identities.tokens

        # The raw material for the independent recomputation, read with the
        # secret key so RLS cannot quietly narrow it.
        season = [s for s in sb.select("seasons", columns="season_id,status")
                  if s["status"] == "active"][0]["season_id"]
        cls.season = season
        active_comps = {
            cs["competition_id"]
            for cs in sb.select("competition_seasons",
                                columns="competition_id,season_id,status")
            if cs["season_id"] == season and cs["status"] == "active"}
        cls.matches = [
            m for m in sb.select("matches", columns="*", require_secret=True)
            if m["season_id"] == season and m["competition_id"] in active_comps]
        goals = sb.select("goals", columns="match_id", require_secret=True)
        cls.goal_count = {}
        for g in goals:
            cls.goal_count[g["match_id"]] = cls.goal_count.get(g["match_id"], 0) + 1
        cls.today = cat_today()

    @classmethod
    def tearDownClass(cls):
        cls.identities.teardown()

    # ── helpers ──────────────────────────────────────────────────────────────

    def admin_rows(self, path):
        status, body = _call(path, token=self.tokens["admin"])
        self.assertEqual(status, 200, f"admin was refused {path}: {body}")
        return body

    def scored(self, m):
        return (m["home_goals"] or 0) + (m["away_goals"] or 0)

    # ── 1. Access ────────────────────────────────────────────────────────────

    def test_anon_is_refused_every_ops_object(self):
        """Not 'sees no rows' — refused. anon holds no grant on any of them."""
        for name in OPS_VIEWS + OPS_TABLES:
            with self.subTest(object=name):
                status, body = _call(f"{name}?select=*", token=None)
                self.assertGreaterEqual(
                    status, 400,
                    f"anon read {name} and got {body!r}")

    def test_reporter_sees_nothing(self):
        """An ordinary reporter is authenticated, so the grant lets the query
        through — and the is_admin() predicate inside each view means it comes
        back empty. Including ops_dashboard_totals, which is a bare aggregate
        and would otherwise hand back a tidy row of zeros."""
        for label in ("a", "inactive"):
            for name in OPS_VIEWS + OPS_TABLES:
                with self.subTest(identity=label, object=name):
                    status, body = _call(f"{name}?select=*", token=self.tokens[label])
                    self.assertEqual(status, 200)
                    self.assertEqual(body, [], f"{label} saw rows in {name}")

    def test_admin_sees_data(self):
        for name in OPS_VIEWS:
            with self.subTest(object=name):
                self.assertTrue(self.admin_rows(f"{name}?select=*"),
                                f"admin saw nothing in {name}")

    def test_rebuild_status_is_admin_only(self):
        status, _ = _call("rpc/ops_rebuild_status", token=None,
                          method="POST", body={})
        self.assertGreaterEqual(status, 400)

        status, body = _call("rpc/ops_rebuild_status", token=self.tokens["a"],
                             method="POST", body={})
        self.assertGreaterEqual(status, 400,
                                f"a reporter read rebuild state: {body!r}")

        status, body = _call("rpc/ops_rebuild_status", token=self.tokens["admin"],
                             method="POST", body={})
        self.assertEqual(status, 200, body)
        self.assertEqual(len(body), 1)
        self.assertIn("pending", body[0])

    def test_reporter_cannot_write_settings(self):
        """The dashboard is read-only over football data, but its own settings
        are writable — by administrators only."""
        status, _ = _call("ops_competition_settings?competition_id=eq.MW_SL",
                          token=self.tokens["a"], method="PATCH",
                          body={"note": "tampered"},
                          prefer="return=representation")
        # RLS with no matching policy returns success on zero rows, so the
        # status code alone proves nothing. Check the row.
        rows = self.admin_rows(
            "ops_competition_settings?competition_id=eq.MW_SL&select=note")
        self.assertNotIn("tampered", [r["note"] for r in rows])

    # ── 2. Arithmetic ────────────────────────────────────────────────────────

    def test_totals_match_an_independent_count(self):
        totals = self.admin_rows("ops_dashboard_totals?select=*")[0]

        overdue = [m for m in self.matches
                   if m["status"] == "scheduled" and m["date"]
                   and m["date"] < self.today]
        unscheduled = [m for m in self.matches
                       if m["status"] == "scheduled" and not m["date"]]
        postponed = [m for m in self.matches if m["status"] == "postponed"]
        # The definition that matters: fewer goal rows than the SCORE implies.
        missing_scorers = [
            m for m in self.matches
            if m["status"] == "played"
            and self.goal_count.get(m["match_id"], 0) < self.scored(m)]
        missing_venue = [m for m in self.matches
                         if not m["venue_id"] and m["status"] != "cancelled"]
        missing_source = [m for m in self.matches
                          if m["status"] in ("played", "awarded")
                          and not m["source_ref"]]
        unconfirmed = [m for m in self.matches
                       if m["status"] in ("played", "awarded")
                       and m["confidence"] == "unconfirmed"]

        for name, expected in (
                ("overdue", len(overdue)),
                ("unscheduled", len(unscheduled)),
                ("awaiting_reschedule", len(postponed)),
                ("missing_scorers", len(missing_scorers)),
                ("missing_venue", len(missing_venue)),
                ("missing_source", len(missing_source)),
                ("unconfirmed", len(unconfirmed))):
            with self.subTest(count=name):
                self.assertEqual(totals[name], expected)

    def test_goalless_draws_are_complete(self):
        """The edge case the whole scorer count turns on. A 0-0 has no goals to
        record, so it must never appear in the missing-scorers backlog."""
        goalless = {m["match_id"] for m in self.matches
                    if m["status"] == "played" and self.scored(m) == 0}
        self.assertTrue(goalless, "no 0-0 in the season; this test proves nothing")

        flagged = {r["match_id"] for r in self.admin_rows(
            "ops_match_flags?missing_scorers=is.true&select=match_id")}
        self.assertEqual(goalless & flagged, set())

    def test_postponed_and_undated_are_not_overdue(self):
        overdue = self.admin_rows(
            "ops_match_flags?is_overdue=is.true&select=match_id,status,date")
        for row in overdue:
            self.assertEqual(row["status"], "scheduled")
            self.assertIsNotNone(row["date"])
            self.assertLess(row["date"], self.today)

    def test_cancelled_matches_need_no_venue(self):
        rows = self.admin_rows(
            "ops_match_flags?missing_venue=is.true&select=status")
        self.assertNotIn("cancelled", [r["status"] for r in rows])

    def test_awarded_is_excluded_from_missing_scorers(self):
        """A walkover carries a score and no goals; it is not a scorer gap."""
        rows = self.admin_rows(
            "ops_match_flags?missing_scorers=is.true&select=status")
        self.assertEqual({r["status"] for r in rows} - {"played"}, set())

    def test_round_expectation_is_floor_half_the_entries(self):
        for row in self.admin_rows(
                "ops_competition_expectation?select=*"):
            with self.subTest(competition=row["competition_id"]):
                if row["competition_type"] == "cup":
                    self.assertIsNone(row["derived_per_round"],
                                      "a cup round has no derivable size")
                else:
                    self.assertEqual(row["derived_per_round"],
                                     row["active_entries"] // 2)

    def test_size_delta_is_entered_minus_expected(self):
        for row in self.admin_rows("ops_matchday_status?select=*"):
            with self.subTest(competition=row["competition_id"],
                              round=row["round_label"]):
                if row["expected"] is None or row["round_key"] is None:
                    self.assertIsNone(row["size_delta"])
                else:
                    self.assertEqual(row["size_delta"],
                                     row["entered"] - row["expected"])

    def test_unassigned_round_has_no_expectation(self):
        """The unassigned bucket collects fixtures with no round at all. It is
        not a round that is short of fixtures, and must not claim to be."""
        for row in self.admin_rows(
                "ops_matchday_status?round_key=is.null&select=*"):
            self.assertEqual(row["round_label"], "unassigned")
            self.assertIsNone(row["expected"])
            self.assertIsNone(row["size_delta"])

    def test_next_round_is_upcoming_not_the_oldest_unfinished(self):
        """The distinction the summary exists to make: a straggler left in an
        old round is backlog, not the next round to be played."""
        for row in self.admin_rows("ops_competition_summary?select=*"):
            comp = row["competition_id"]
            with self.subTest(competition=comp):
                if row["next_round_state"] == "none":
                    self.assertIsNone(row["next_round_label"])
                    continue
                upcoming = [
                    m for m in self.matches
                    if m["competition_id"] == comp
                    and m["status"] in ("scheduled", "postponed")
                    and (not m["date"] or m["date"] >= self.today)]
                self.assertTrue(upcoming, f"{comp} claims a next round with none due")
                if row["next_round_state"] == "dated":
                    earliest = min(m["date"] for m in upcoming if m["date"])
                    self.assertEqual(row["next_date"], earliest)

    def test_reconciliation_arithmetic(self):
        """whole_rounds and fixtures_spare are what separate 'fixtures are
        mislabelled' from 'fixtures were never entered'."""
        for row in self.admin_rows("ops_competition_summary?select=*"):
            with self.subTest(competition=row["competition_id"]):
                per = row["expected_per_round"]
                if not per:
                    self.assertIsNone(row["whole_rounds"])
                    self.assertIsNone(row["fixtures_spare"])
                    continue
                self.assertEqual(row["whole_rounds"], row["matches_total"] // per)
                self.assertEqual(row["fixtures_spare"], row["matches_total"] % per)

    def test_pre_tracker_split_adds_up(self):
        """Hidden rows are folded away, never dropped: the 'current' count can
        never exceed the total it is a subset of."""
        for row in self.admin_rows("ops_competition_summary?select=*"):
            for total, current in (("missing_venue", "missing_venue_current"),
                                   ("missing_source", "missing_source_current"),
                                   ("missing_scorers", "missing_scorers_current")):
                with self.subTest(competition=row["competition_id"], count=total):
                    self.assertLessEqual(row[current], row[total])

    def test_only_active_competition_seasons_are_covered(self):
        comps = {r["competition_id"] for r in
                 self.admin_rows("ops_competition_summary?select=competition_id")}
        active = {cs["competition_id"] for cs in sb.select(
            "competition_seasons", columns="competition_id,season_id,status")
            if cs["season_id"] == self.season and cs["status"] == "active"}
        self.assertEqual(comps, active)


if __name__ == "__main__":
    unittest.main()
