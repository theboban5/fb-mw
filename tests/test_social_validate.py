"""Validation must stop a board, not decorate it with a warning."""

import os
import shutil
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))   # the repo root, for `social`
sys.path.insert(0, HERE)                    # this directory, for the fixture

from social import output, validate           # noqa: E402
from social.posts import base                 # noqa: E402
import social_fixture_data as fixture_data    # noqa: E402


def _break(ctx, mutate):
    """Apply a mutation to the fixture dataset and re-validate."""
    mutate(ctx)
    return validate.run(ctx)


class ValidationErrorTest(unittest.TestCase):

    def test_clean_fixture_data_passes(self):
        report = validate.run(fixture_data.ctx())
        self.assertTrue(report.ok, [i.message for i in report.errors])

    def test_played_with_no_score_is_an_error(self):
        ctx = fixture_data.ctx()
        report = _break(ctx, lambda c: c.ds.matches.__setitem__(
            "T_M1", c.ds.matches["T_M1"].__class__(
                **{**c.ds.matches["T_M1"].__dict__,
                   "home_goals": None, "away_goals": None})))
        self.assertFalse(report.ok)
        self.assertIn("no score", report.errors[0].message)

    def test_goal_for_a_team_not_in_the_match_is_an_error(self):
        ctx = fixture_data.ctx()
        goal = ctx.ds.goals["T_G1"]
        ctx.ds.goals["T_G1"] = goal.__class__(
            **{**goal.__dict__, "team_id": "T_FFF"})
        report = validate.run(ctx)
        self.assertFalse(report.ok)
        self.assertTrue(any("not playing in this match" in i.message
                            for i in report.errors))

    def test_more_goal_rows_than_the_score_is_an_error(self):
        ctx = fixture_data.ctx()
        match = ctx.ds.matches["T_M2"]
        ctx.ds.matches["T_M2"] = match.__class__(
            **{**match.__dict__, "home_goals": 1})
        report = validate.run(ctx)
        self.assertFalse(report.ok)
        self.assertTrue(any("goal rows recorded" in i.message
                            for i in report.errors))

    def test_duplicate_fixture_is_an_error(self):
        ctx = fixture_data.ctx()
        original = ctx.ds.matches["T_M1"]
        ctx.ds.matches["T_M1_COPY"] = original.__class__(
            **{**original.__dict__, "match_id": "T_M1_COPY"})
        report = validate.run(ctx)
        self.assertFalse(report.ok)
        self.assertTrue(any("duplicate fixture" in i.message
                            for i in report.errors))

    def test_date_outside_the_season_is_an_error(self):
        # The season is shortened rather than the match moved: validation is
        # scoped to a window around the run date, so a match dropped into 2028
        # would simply fall out of scope and never be checked.
        ctx = fixture_data.ctx()
        season = ctx.ds.seasons[fixture_data.SEASON]
        ctx.ds.seasons[fixture_data.SEASON] = season.__class__(
            **{**season.__dict__, "end_date": "2026-08-01"})
        report = validate.run(ctx)
        self.assertFalse(report.ok)
        self.assertTrue(any("outside season" in i.message
                            for i in report.errors))

    def test_missing_venue_is_only_a_warning(self):
        ctx = fixture_data.ctx()
        match = ctx.ds.matches["T_M1"]
        ctx.ds.matches["T_M1"] = match.__class__(
            **{**match.__dict__, "venue_id": ""})
        report = validate.run(ctx)
        self.assertTrue(report.ok)
        self.assertTrue(any("no venue" in i.message for i in report.warnings))


class BlockingTest(unittest.TestCase):
    """An error blocks the boards it affects — and only those."""

    def setUp(self):
        self.ctx = fixture_data.ctx(days="1")
        match = self.ctx.ds.matches["T_M2"]
        self.ctx.ds.matches["T_M2"] = match.__class__(
            **{**match.__dict__, "home_goals": 1})   # fewer goals than rows
        self.report = validate.run(self.ctx)
        self.assertFalse(self.report.ok)

    def test_a_board_holding_the_bad_match_is_blocked(self):
        self.assertTrue(self.report.blocks(match_ids=("T_M2",)))

    def test_a_board_without_the_bad_match_is_not_blocked(self):
        # Same competition, different match: a bad row must not take down
        # every card the competition ever produces.
        self.assertFalse(self.report.blocks(match_ids=("T_M1",)))

    def test_a_derived_board_in_that_competition_is_blocked(self):
        # A table has no match list; the bad row feeds its computation.
        self.assertTrue(self.report.blocks(
            competition_ids=(fixture_data.COMPETITION,)))


class NoOutputOnErrorTest(unittest.TestCase):
    """The end-to-end guarantee: a blocked board writes no files at all."""

    def setUp(self):
        self.folder = tempfile.mkdtemp(prefix="social-test-")
        self.addCleanup(shutil.rmtree, self.folder, ignore_errors=True)

    def test_blocked_draft_writes_nothing(self):
        ctx = fixture_data.ctx(days="1")
        match = ctx.ds.matches["T_M2"]
        ctx.ds.matches["T_M2"] = match.__class__(
            **{**match.__dict__, "home_goals": 1})
        report = validate.run(ctx)

        written = []
        for draft in base.get("results").build(ctx):
            if report.blocks(draft.match_ids, draft.payload.competition_ids):
                continue
            written.append(output.write_post(draft, ctx.date, self.folder,
                                             dry_run=True))

        self.assertEqual(written, [], "a blocked board produced output")
        self.assertEqual(os.listdir(self.folder), [])


if __name__ == "__main__":
    unittest.main()
