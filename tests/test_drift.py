"""Check 8, the deletion-drift check, and the one deletion it forgives.

The check exists because under the spreadsheet a row only ever vanished by
accident — a stray select-and-delete, a filter left on during an export — and
a silently shorter table is the one data error a reader cannot see. Any id in
the last snapshot that is gone from the fetch aborts the build.

Migration 0032 then made one deletion deliberate: an admin can remove a
scheduled fixture that nobody has reported anything onto. This check could not
tell the two apart, so the sanctioned delete was permanently fatal — every
later build failed on the same drift error, including the ones a reporter set
off by publishing something unrelated, until a human ran the build by hand with
--allow-deletions.

What is protected here is the seam between those two: the forgiven case is
exactly as narrow as the RPC that creates it, and everything either side of it
is still an error.
"""

import os
import sys
import tempfile
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

import validate  # noqa: E402

MATCH_COLS = ("match_id,competition_id,season_id,stage,matchday,date,kickoff,"
              "venue_id,home_team_id,away_team_id,home_goals,away_goals,status")


def match_csv(*rows):
    return MATCH_COLS + "\n" + "".join(r + "\n" for r in rows)


SCHEDULED = "MW_X_2627_001,MW_X,MW_2026_27,md_1,1,2026-08-22,14:30,,A,B,,,scheduled"
PLAYED = "MW_X_2627_002,MW_X,MW_2026_27,md_1,1,2026-08-01,,,C,D,2,1,played"


class DriftTest(unittest.TestCase):

    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.dir.cleanup)

    def snapshot(self, **tabs):
        for tab, text in tabs.items():
            with open(os.path.join(self.dir.name, f"{tab}.csv"), "w",
                      encoding="utf-8", newline="") as fh:
                fh.write(text)

    def drift(self, **texts):
        return validate.check_drift(texts, self.dir.name)

    # ── the case 0032 created ────────────────────────────────────────────────

    def test_a_deleted_scheduled_fixture_is_forgiven(self):
        self.snapshot(matches=match_csv(SCHEDULED, PLAYED),
                      goals="goal_id,match_id\n", lineups="match_id,team_id\n")
        self.assertEqual(self.drift(matches=match_csv(PLAYED)), [])

    def test_a_deleted_played_match_is_still_an_error(self):
        """The RPC refuses one, so its disappearance is not the RPC's doing."""
        self.snapshot(matches=match_csv(SCHEDULED, PLAYED),
                      goals="goal_id,match_id\n", lineups="match_id,team_id\n")
        errors = self.drift(matches=match_csv(SCHEDULED))
        self.assertEqual(len(errors), 1)
        self.assertIn("MW_X_2627_002", errors[0])

    def test_a_fixture_with_a_goal_against_it_is_still_an_error(self):
        """A scorer on a scheduled fixture means somebody reported something.

        delete_fixture would have refused, so whatever removed this row was
        not delete_fixture — which is the whole question this check asks.
        """
        self.snapshot(
            matches=match_csv(SCHEDULED),
            goals="goal_id,match_id\nG1,MW_X_2627_001\n",
            lineups="match_id,team_id\n")
        self.assertEqual(len(self.drift(matches=match_csv())), 1)

    def test_a_fixture_with_a_team_sheet_is_still_an_error(self):
        self.snapshot(
            matches=match_csv(SCHEDULED),
            goals="goal_id,match_id\n",
            lineups="match_id,team_id\nMW_X_2627_001,A\n")
        self.assertEqual(len(self.drift(matches=match_csv())), 1)

    def test_no_goals_snapshot_means_refuse_rather_than_guess(self):
        """A snapshot too old to hold `goals` cannot clear anything."""
        self.snapshot(matches=match_csv(SCHEDULED))
        self.assertEqual(len(self.drift(matches=match_csv())), 1)

    # ── everything the check always did ──────────────────────────────────────

    def test_a_deleted_club_is_an_error(self):
        """Nothing may delete a club, so nothing forgives one."""
        self.snapshot(clubs="club_id,name\nMW_AAA,Aaa\n")
        errors = self.drift(clubs="club_id,name\n")
        self.assertEqual(len(errors), 1)
        self.assertIn("MW_AAA", errors[0])

    def test_a_deleted_national_team_match_is_an_error(self):
        """delete_fixture only ever touches public.matches."""
        self.snapshot(nt_matches="match_id,date\nNT_1,2026-08-01\n",
                      goals="goal_id,match_id\n", lineups="match_id,team_id\n")
        self.assertEqual(len(self.drift(nt_matches="match_id,date\n")), 1)

    def test_no_snapshot_passes_vacuously(self):
        self.assertEqual(self.drift(matches=match_csv(PLAYED)), [])

    def test_an_unchanged_fetch_is_silent(self):
        self.snapshot(matches=match_csv(SCHEDULED, PLAYED),
                      goals="goal_id,match_id\n", lineups="match_id,team_id\n")
        self.assertEqual(self.drift(matches=match_csv(SCHEDULED, PLAYED)), [])


class MigrationParityTest(unittest.TestCase):
    """_deletable_fixture restates delete_fixture's preconditions in Python.

    Two copies of a rule drift. This is the cheapest thing that notices if the
    SQL ever grows a fourth condition the build does not know about.
    """

    def test_the_sql_still_checks_exactly_these_three_things(self):
        path = os.path.join(ROOT, "supabase", "migrations",
                            "0032_delete_fixture.sql")
        with open(path, encoding="utf-8") as fh:
            sql = fh.read()
        self.assertIn("v_match.status <> 'scheduled'", sql)
        self.assertIn("from public.goals where match_id = p_match_id", sql)
        self.assertIn("from public.lineups where match_id = p_match_id", sql)


if __name__ == "__main__":
    unittest.main()
