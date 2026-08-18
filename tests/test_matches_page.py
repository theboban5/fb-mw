"""Tests for the by-date match pages (src/matches_page.py).

The stakes are the same as the search index's: this page is nothing but links —
to the previous date, the next date, a club hub, a competition's results — and
a link to a page the build never wrote is worse than no link. So the heart of
this suite builds the real pages from the committed snapshot and walks every
href in them.
"""

import json
import os
import re
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src import adapt, dataset, matches_page, nt  # noqa: E402

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CANONICAL = os.path.join(ROOT, "data", "canonical")
TEMPLATES = os.path.join(ROOT, "templates")
STATIC = os.path.join(ROOT, "static")

# Pinned so the window and the "today" labels don't move with the wall clock.
TODAY = "2026-08-05"


def _load(tabs):
    return dataset.read_snapshot(CANONICAL, tabs)


class ClockTest(unittest.TestCase):
    """The kickoff column has never been rendered, so it has never been
    normalised either — both Sheets export forms are in the data."""

    def test_forms(self):
        self.assertEqual(matches_page._clock("14:30"), "14:30")
        self.assertEqual(matches_page._clock("14:30:00"), "14:30")
        self.assertEqual(matches_page._clock("9:05"), "09:05")
        self.assertEqual(matches_page._clock(" "), "")
        self.assertEqual(matches_page._clock("tbd"), "")


class LabelTest(unittest.TestCase):
    def test_full_and_short(self):
        self.assertEqual(matches_page.full_date_label("2026-08-05"),
                         "Wednesday 5 August 2026")
        self.assertEqual(matches_page.short_date_label("2026-08-05"), "Wed 5 Aug")

    def test_offsets(self):
        self.assertEqual(matches_page.offset_label("2026-08-05", TODAY), "Today")
        self.assertEqual(matches_page.offset_label("2026-08-04", TODAY), "Yesterday")
        self.assertEqual(matches_page.offset_label("2026-08-06", TODAY), "Tomorrow")
        self.assertEqual(matches_page.offset_label("2026-08-10", TODAY), "In 5 days")
        self.assertEqual(matches_page.offset_label("2026-07-29", TODAY), "7 days ago")


class WindowTest(unittest.TestCase):
    def test_window_is_contiguous_around_today(self):
        dates = matches_page.page_dates({}, TODAY)
        self.assertEqual(dates[0], "2026-07-29")            # today - 7
        self.assertEqual(dates[-1], "2026-09-04")           # today + 30
        self.assertEqual(len(dates),
                         matches_page.WINDOW_BACK + matches_page.WINDOW_FORWARD + 1)

    def test_match_dates_outside_the_window_still_get_a_page(self):
        far = "2025-01-31"
        dates = matches_page.page_dates({far: matches_page.Day(far)}, TODAY)
        self.assertIn(far, dates)


class CollectTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        texts = _load(dataset.TABS)
        nt_texts = _load(dataset.NT_TABS)
        if texts is None or nt_texts is None:
            raise unittest.SkipTest("data/canonical/ snapshot not present")
        cls.ds = dataset.parse_all(texts)
        cls.nt_data = nt.parse_all(nt_texts)
        cls.days = matches_page.collect(cls.ds, cls.nt_data)

    def test_every_dated_match_lands_on_its_date(self):
        expected = {}
        for m in self.ds.matches.values():
            if m.is_placeholder or not m.date:
                continue
            expected[m.date] = expected.get(m.date, 0) + 1
        for iso, n in expected.items():
            got = sum(len(g.matches) for g in self.days[iso].groups)
            self.assertEqual(got, n, f"{iso}: {got} rendered, {n} in the data")

    def test_placeholder_and_undated_matches_are_absent(self):
        rendered = {m.match_id for day in self.days.values()
                    for g in day.groups for m in g.matches}
        for m in self.ds.matches.values():
            if m.is_placeholder or not m.date:
                self.assertNotIn(m.match_id, rendered)

    def test_national_team_matches_are_included(self):
        # The nt_* tabs are a separate schema; a date view that skipped them
        # would silently drop the biggest match of the day.
        nt_dates = {m.date for m in self.nt_data.nt_matches.values() if m.date}
        for iso in nt_dates:
            self.assertTrue(self.days[iso].nt_matches, f"{iso} lost its NT match")

    def test_groups_follow_the_landing_page_order(self):
        order = list(adapt.COMPETITION_SLUGS)
        for day in self.days.values():
            ranks = [order.index(g.competition_id) for g in day.groups
                     if g.competition_id in order]
            self.assertEqual(ranks, sorted(ranks))

    def test_matches_are_in_kickoff_order_with_unknown_times_last(self):
        for day in self.days.values():
            for group in day.groups:
                times = [matches_page._clock(m.kickoff) for m in group.matches]
                known = [t for t in times if t]
                self.assertEqual(known, sorted(known))
                self.assertEqual(times, [t for t in times if t]
                                 + [t for t in times if not t])

    def test_round_label_is_dropped_when_a_group_spans_two_rounds(self):
        for day in self.days.values():
            for group in day.groups:
                comp = self.ds.competitions[group.competition_id]
                labels = {matches_page._round_label(comp, m) for m in group.matches}
                if len(labels) > 1:
                    self.assertEqual(group.round_label, "")


class BuiltPagesTest(unittest.TestCase):
    """Builds the real pages and walks every link in them."""

    @classmethod
    def setUpClass(cls):
        texts = _load(dataset.TABS)
        nt_texts = _load(dataset.NT_TABS)
        if texts is None or nt_texts is None:
            raise unittest.SkipTest("data/canonical/ snapshot not present")
        cls.ds = dataset.parse_all(texts)
        cls.nt_data = nt.parse_all(nt_texts)
        cls.club_hub_ids = {t.club_id for t in cls.ds.teams.values() if t.club_id}
        cls.tmp = tempfile.TemporaryDirectory()
        cls.pages, cls.match_dates = matches_page.build_pages(
            cls.tmp.name, TEMPLATES, STATIC, cls.ds, "1 January 2026, 00:00 CAT",
            TODAY, nt_data=cls.nt_data, club_hub_ids=cls.club_hub_ids)
        cls.dir = os.path.join(cls.tmp.name, matches_page.SLUG)
        cls.files = sorted(f for f in os.listdir(cls.dir) if f.endswith(".html"))
        # The dates that actually have matches, so a test can pick one that
        # does (or doesn't) instead of naming a date that the snapshot may
        # later contradict. build_pages returns counts, not the dates.
        cls.days = matches_page.collect(cls.ds, cls.nt_data)

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def _body(self, name):
        with open(os.path.join(self.dir, name), encoding="utf-8") as fh:
            return fh.read()

    def test_index_and_one_page_per_date(self):
        self.assertIn("index.html", self.files)
        self.assertEqual(self.pages, len(self.files))

    def test_index_is_todays_page(self):
        self.assertIn("WEDNESDAY 5 AUGUST 2026", self._body("index.html"))
        self.assertIn(f'data-day-today="{TODAY}"', self._body("index.html"))

    def test_every_date_link_resolves(self):
        """The whole point of generating a contiguous window: an arrow or chip
        that points at a date nobody wrote is a 404."""
        have = set(self.files)
        for name in self.files:
            for href in re.findall(r'href="(\d{4}-\d{2}-\d{2}\.html)"',
                                   self._body(name)):
                self.assertIn(href, have, f"{name} links at a missing {href}")

    def test_chips_are_anchored_on_the_date_being_shown(self):
        # Not on today: the middle chip is always this page's date, and its
        # neighbours are the pages either side of it.
        body = self._body("2026-08-08.html")
        self.assertIn('href="2026-08-07.html"', body)
        self.assertIn('<a class="day-chip active" href="2026-08-08.html"', body)
        self.assertIn('href="2026-08-09.html"', body)
        # ... and nothing anchors it to today any more.
        self.assertNotIn("2026-08-05.html", body.split("day-chips")[1]
                         .split("</div>")[0])

    def test_chip_labels_name_the_day(self):
        # Relative where that is what it is, the weekday otherwise.
        near = self._body("2026-08-05.html")
        for label in ("Yesterday", "Today", "Tomorrow"):
            self.assertIn(f">{label}</span>", near)
        far = self._body("2026-08-08.html")
        for label in ("Friday", "Saturday", "Sunday"):
            self.assertIn(f">{label}</span>", far)

    def test_the_ends_of_the_calendar_get_a_placeholder_not_a_dead_link(self):
        first = self._body(self.files[0])
        self.assertIn('class="day-chip is-off"', first)

    def test_club_links_only_point_at_hubs_that_exist(self):
        for name in self.files:
            for club_id in re.findall(r'href="\.\./clubs/([^"]+)\.html"',
                                      self._body(name)):
                self.assertIn(club_id, self.club_hub_ids)

    def test_competition_links_use_the_public_slugs(self):
        slugs = {adapt.competition_slug(c.competition_id, c.country)
                 for c in self.ds.competitions.values()}
        for name in self.files:
            for slug in re.findall(r'href="\.\./([a-z0-9-]+)/results\.html"',
                                   self._body(name)):
                self.assertIn(slug, slugs)

    # Both of the following used to name a date outright. The snapshot moved
    # under them — a date that had no matches when the test was written has
    # since been given some — so they now find a date that still has the
    # property being tested. The assertion is the same; only the way the date
    # is chosen changed.

    def _first_page_matching(self, pattern):
        for name in self.files:
            if name == "index.html":
                continue
            body = self._body(name)
            if re.search(pattern, body):
                return name, body
        self.fail(f"no generated page matches {pattern!r}")

    def test_a_fixture_shows_its_kickoff_where_a_result_shows_the_score(self):
        name, body = self._first_page_matching(
            r'class="v2-res-score day-res-time">\d{2}:\d{2}<')
        # The kickoff stands in the score column, so the same row must not also
        # carry a score cell.
        before = body.split("day-res-time")[0][-200:]
        self.assertNotIn('v2-res-score">', before, name)

    def test_an_empty_date_says_so_and_offers_the_nearest(self):
        empty = [f for f in self.files
                 if f != "index.html" and f[:-5] not in self.days]
        self.assertTrue(empty, "the window should span at least one empty date")
        body = self._body(empty[0])
        self.assertIn("No matches on this date.", body)
        # It has to offer a way out: a link to some date that does have matches.
        self.assertRegex(body, r'href="\d{4}-\d{2}-\d{2}\.html"')

    def test_the_date_bar_always_offers_exactly_three_chips(self):
        for name in self.files:
            # [ "] so the .day-chips container and the -label/-date spans
            # inside each chip don't count as chips themselves.
            chips = re.findall(r'class="day-chip[ "]', self._body(name))
            self.assertEqual(len(chips), 3, name)

    def test_the_picker_ships_only_dates_that_have_a_page(self):
        """The calendar decides what is clickable from `win` + `match`; if that
        rule disagreed with page_dates, cells would link at 404s."""
        have = {f[:-5] for f in self.files if f != "index.html"}
        payload = json.loads(re.search(
            r'<script type="application/json" data-day-cal>(.*?)</script>',
            self._body("index.html"), re.S).group(1))
        self.assertEqual(payload["sel"], TODAY)
        self.assertEqual(payload["today"], TODAY)
        for d in payload["match"]:
            self.assertIn(d, have)
        lo, hi = payload["win"]
        self.assertTrue(all(lo <= d <= hi or d in set(payload["match"])
                            for d in have if lo <= d <= hi))
        # Every page in the window is reachable, which is what makes the
        # day-by-day chips work without gaps.
        for d in have:
            reachable = (lo <= d <= hi) or d in set(payload["match"])
            self.assertTrue(reachable, f"{d} has a page the picker cannot reach")


if __name__ == "__main__":
    unittest.main()
