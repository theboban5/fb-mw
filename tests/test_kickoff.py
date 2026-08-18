"""Tests for kickoff times on fixtures and results.

The matches tab has carried a `kickoff` column since the new schema landed,
but only the day view rendered it. These cover the column's trip to the
competition pages: normalisation (two Sheets export forms are in the data),
the "unknown time shows nothing" rule, and the three places a match caption
is built (results table wide + compact, club hub).
"""

import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src import adapt, dataset, matches_page, render  # noqa: E402
from src.adapt import MatchView, TeamView  # noqa: E402

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CANONICAL = os.path.join(ROOT, "data", "canonical")

TEAMS = {"AAA": TeamView(code="AAA", name="Alpha FC"),
         "BBB": TeamView(code="BBB", name="Beta FC")}


def view(kickoff="14:30", hg=None, ag=None, status="scheduled", stadium="Kamuzu Stadium"):
    if hg is not None and status == "scheduled":
        status = "played"
    return MatchView(2, 13, "2026-08-15", "AAA", "BBB", hg, ag,
                     kickoff=adapt.clock(kickoff), stadium=stadium, status=status)


class ClockTest(unittest.TestCase):
    def test_both_sheets_export_forms(self):
        self.assertEqual(adapt.clock("14:30"), "14:30")
        self.assertEqual(adapt.clock("14:30:00"), "14:30")

    def test_hour_is_padded(self):
        self.assertEqual(adapt.clock("9:05"), "09:05")

    def test_unknown_times_normalise_to_blank(self):
        for raw in ("", "  ", None, "tbd", "TBA", "afternoon", "1430", "25:00",
                    "14:75", "14:3"):
            self.assertEqual(adapt.clock(raw), "", repr(raw))

    def test_the_day_view_uses_the_same_normaliser(self):
        # A kickoff must not read differently depending on which page you
        # land on, so matches_page delegates rather than keeping a copy.
        for raw in ("14:30", "14:30:00", "9:05", "tbd", ""):
            self.assertEqual(matches_page._clock(raw), adapt.clock(raw), repr(raw))


class LabelTest(unittest.TestCase):
    def test_label_names_the_zone(self):
        self.assertEqual(view("14:30").kickoff_label, "14:30 CAT")

    def test_no_kickoff_means_no_label(self):
        self.assertEqual(view("").kickoff_label, "")
        self.assertEqual(view("tbd").kickoff_label, "")

    def test_the_zone_is_the_one_the_national_team_pages_use(self):
        from src import nt
        self.assertEqual(adapt.KICKOFF_TZ, nt.KICKOFF_TZ)


class CaptionTest(unittest.TestCase):
    """The compact caption: date · kickoff · venue."""

    def test_kickoff_sits_between_the_date_and_the_venue(self):
        meta = render._match_meta(view(), "15 Aug 2026")
        self.assertEqual(
            meta, "15 Aug 2026 &middot; 14:30 CAT &middot; Kamuzu Stadium")

    def test_unknown_kickoff_leaves_the_caption_as_it_was(self):
        meta = render._match_meta(view(kickoff=""), "15 Aug 2026")
        self.assertEqual(meta, "15 Aug 2026 &middot; Kamuzu Stadium")

    def test_kickoff_alone_needs_no_separator(self):
        meta = render._match_meta(view(stadium=""), "")
        self.assertEqual(meta, "14:30 CAT")

    def test_old_schema_matches_without_the_field_still_render(self):
        class Old:
            stadium = "Kamuzu Stadium"
        self.assertEqual(render._match_meta(Old(), "15 Aug 2026"),
                         "15 Aug 2026 &middot; Kamuzu Stadium")


class ResultsTableTest(unittest.TestCase):
    def _html(self, matches, compact=False):
        return render.render_results(matches, TEAMS, season="26/27",
                                     league_name="FDH Bank Premiership",
                                     compact=compact)

    def test_wide_table_shows_the_time_under_the_date(self):
        html = self._html([view()])
        self.assertIn('<td class="v2-res-date">15 Aug 2026'
                      '<span class="v2-res-time">14:30 CAT</span></td>', html)

    def test_wide_table_omits_the_span_when_the_time_is_unknown(self):
        html = self._html([view(kickoff="")])
        self.assertIn('<td class="v2-res-date">15 Aug 2026</td>', html)
        self.assertNotIn("v2-res-time", html)

    def test_compact_table_shows_the_time_in_the_caption(self):
        html = self._html([view()], compact=True)
        self.assertIn("15 Aug 2026 &middot; 14:30 CAT &middot; Kamuzu Stadium",
                      html)

    def test_a_played_result_keeps_its_kickoff(self):
        # "What time did it kick off" stays answerable after the match: the
        # time is fixture metadata, not a stand-in for a missing score.
        html = self._html([view(hg=2, ag=1)])
        self.assertIn('<span class="v2-res-time">14:30 CAT</span>', html)
        self.assertIn('<td class="v2-res-score">2:1</td>', html)

    def test_a_fixture_still_reads_as_vs(self):
        html = self._html([view()])
        self.assertIn("v2-res-vs", html)


class SnapshotTest(unittest.TestCase):
    """End to end from the committed snapshot: the sheet's column reaches
    the adapted matches the pages render."""

    @classmethod
    def setUpClass(cls):
        texts = dataset.read_snapshot(CANONICAL)
        if texts is None:
            raise unittest.SkipTest("data/canonical/ snapshot not present")
        cls.ds = dataset.parse_all(texts)

    def test_every_adapted_match_carries_the_sheets_kickoff(self):
        seen_a_time = False
        for cs in adapt.current_competition_seasons(self.ds):
            league = adapt.league_data(self.ds, cs.competition_id, cs.season_id)
            for m in league.matches:
                raw = self.ds.matches[m.match_id].kickoff
                self.assertEqual(m.kickoff, adapt.clock(raw), m.match_id)
                seen_a_time = seen_a_time or bool(m.kickoff)
        self.assertTrue(seen_a_time, "no kickoff in the snapshot to check")

    def test_no_kickoff_in_the_snapshot_is_unreadable(self):
        # A time the normaliser rejects would silently vanish from the page,
        # so a new sheet form should surface here rather than in production.
        for m in self.ds.matches.values():
            raw = (m.kickoff or "").strip()
            if raw and raw.lower() not in ("tbd", "tba"):
                self.assertTrue(adapt.clock(raw),
                                f"{m.match_id}: unreadable kickoff {raw!r}")


if __name__ == "__main__":
    unittest.main()
