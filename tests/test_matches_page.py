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

from src import adapt, dataset, flags, matches_page, nt  # noqa: E402

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CANONICAL = os.path.join(ROOT, "data", "canonical")
TEMPLATES = os.path.join(ROOT, "templates")
STATIC = os.path.join(ROOT, "static")

# Pinned so the window and the "today" labels don't move with the wall clock.
TODAY = "2026-08-05"


def _load(tabs):
    return dataset.read_snapshot(CANONICAL, tabs)


class NationalTeamCardTest(unittest.TestCase):
    """One card per national team, each under its own name.

    There used to be one card, headed "Malawi Scorchers" and linking to
    /scorchers/, holding every nt_matches row on the date — so the Flames'
    friendlies in Ethiopia rendered as the women's team's.
    """

    HEADER = ("match_id,team_code,date,competition,opponent,home_away,"
              "neutral,venue,city,country,team_score,opponent_score,status,"
              "coach,extra_time,penalty_shootout,extra_time_result,kickoff\n")

    def setUp(self):
        texts = {
            "nt_teams": "team_code,team_name,category\n"
                        "MW_W,Malawi (Women's),senior\n"
                        "MW_M,Malawi (Men's),senior\n"
                        "MW_U20M,Malawi U20 (Men's),youth\n",
            "nt_matches": self.HEADER + (
                "1,MW_W,2026-06-06,Friendly,Zambia,home,FALSE,,,,2,0,played,,"
                "FALSE,FALSE,,\n"
                "2,MW_M,2026-06-06,Friendly,Ethiopia,away,FALSE,,,,0,1,played,,"
                "FALSE,FALSE,,\n"
                "3,MW_U20M,2026-06-06,Friendly,Kenya U20,home,FALSE,,,,1,1,"
                "played,,FALSE,FALSE,,\n"
                "4,MW_M,2026-06-06,Friendly,Tanzania,away,TRUE,,,,1,2,played,,"
                "FALSE,FALSE,,\n"),
        }
        # The other tabs empty, header only — the snapshot's own header lines.
        for tab in dataset.NT_TABS:
            if tab not in texts:
                with open(os.path.join(CANONICAL, f"{tab}.csv")) as f:
                    texts[tab] = f.readline()
        ntd = nt.parse_all(texts)
        # collect()'s national-team half, without a league dataset behind it.
        day = matches_page.Day("2026-06-06", nt_matches=[
            (m, ntd.nt_teams[m.team_code]) for m in ntd.nt_matches.values()])
        self.html = matches_page.render_day(
            None, day, "2026-06-06", "2026-06-06",
            ["2026-06-06"], ["2026-06-06"], STATIC, "", set(),
            flags.Flags(STATIC))

    def _cards(self):
        return self.html.split('<section class="day-comp">')[1:]

    def test_a_card_per_team_flames_first(self):
        cards = self._cards()
        self.assertEqual(len(cards), 3)
        self.assertIn('href="flames/"', cards[0])
        self.assertIn("Malawi Flames", cards[0])
        self.assertIn("Ethiopia", cards[0])
        self.assertNotIn("Zambia", cards[0])
        self.assertIn('href="scorchers/"', cards[1])
        self.assertIn("Zambia", cards[1])
        self.assertNotIn("Ethiopia", cards[1])

    def _line(self, card, opponent):
        line = next(li for li in card.split('<li class="dm')
                    if opponent in li)
        names = re.findall(r'<span class="dm-name">([^<]*)</span>', line)
        score = re.search(r'class="dm-score">(\d+)<span class="dm-dash">'
                          r'&ndash;</span>(\d+)', line).groups()
        return names, score

    def test_the_home_side_reads_first(self):
        # Every club line on the day view reads home first, so "Malawi 0–1
        # Ethiopia" for a match in Addis read as a home defeat. An away match
        # swaps, score with it; a neutral one (stored `away`) does not.
        flames, scorchers = self._cards()[:2]
        self.assertEqual(self._line(flames, "Ethiopia"),
                         (["Ethiopia", "Malawi"], ("1", "0")))
        self.assertEqual(self._line(flames, "Tanzania"),
                         (["Malawi", "Tanzania"], ("1", "2")))
        self.assertEqual(self._line(scorchers, "Zambia"),
                         (["Malawi", "Zambia"], ("2", "0")))

    def test_a_team_without_a_page_gets_a_card_that_links_nowhere(self):
        youth = self._cards()[2]
        self.assertIn("Malawi U20 (Men&#x27;s)", youth)
        self.assertIn("Kenya U20", youth)
        self.assertNotIn("<a ", youth)


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

    def test_the_national_team_archive_stays_off_the_day_pages(self):
        # 0066: RSSSF's 700 Flames matches since 1957 are source_type
        # backfill and would otherwise write a day page each.
        import dataclasses
        ntd = nt.parse_all(_load(dataset.NT_TABS))
        m = next(iter(ntd.nt_matches.values()))
        typed = dataclasses.replace(m, match_id="9001", date="1984-07-08")
        archive = dataclasses.replace(m, match_id="9002", date="1984-07-09",
                                      source_type="backfill")
        ntd.nt_matches.update({"9001": typed, "9002": archive})
        days = matches_page.collect(self.ds, ntd)
        self.assertIn("1984-07-08", days)
        self.assertNotIn("1984-07-09", days)

    def test_every_dated_match_lands_on_its_date(self):
        # ...except a past season's, which is read on its archive page
        # (adapt.archive_competition_seasons) rather than one Saturday at a
        # time; the next test pins that half.
        archived = {(cs.competition_id, cs.season_id)
                    for cs in adapt.archive_competition_seasons(self.ds)}
        expected = {}
        for m in self.ds.matches.values():
            if m.is_placeholder or not m.date:
                continue
            if (m.competition_id, m.season_id) in archived:
                continue
            expected[m.date] = expected.get(m.date, 0) + 1
        for iso, n in expected.items():
            got = sum(len(g.matches) for g in self.days[iso].groups)
            self.assertEqual(got, n, f"{iso}: {got} rendered, {n} in the data")

    def test_archived_seasons_are_absent(self):
        archived = {(cs.competition_id, cs.season_id)
                    for cs in adapt.archive_competition_seasons(self.ds)}
        rendered = {m.match_id for day in self.days.values()
                    for g in day.groups for m in g.matches}
        for m in self.ds.matches.values():
            if (m.competition_id, m.season_id) in archived:
                self.assertNotIn(m.match_id, rendered)

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

    def test_matches_are_played_first_then_kickoff_ordered(self):
        for day in self.days.values():
            for group in day.groups:
                played = [matches_page._is_played(m) for m in group.matches]
                self.assertEqual(played, sorted(played, reverse=True),
                                 "played matches must sort before scheduled ones")
                for flag in (True, False):
                    bucket = [m for m, p in zip(group.matches, played) if p is flag]
                    times = [matches_page._clock(m.kickoff) for m in bucket]
                    known = [t for t in times if t]
                    self.assertEqual(known, sorted(known))
                    self.assertEqual(times, known + [t for t in times if not t])

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
            TODAY, nt_data=cls.nt_data, club_hub_ids=cls.club_hub_ids,
            leagues_html='<a class="lc-row" href="../sl/">Super League</a>')
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
        self.assertIn('<span class="day-rel" data-day-rel="2026-08-05">Today'
                      '</span>Wednesday 5 August<', self._body("index.html"))
        self.assertIn(f'data-day-today="{TODAY}"', self._body("index.html"))

    def test_clock_words_carry_their_date_for_calendar_js(self):
        """A page says Today about the day it was BUILT; read after midnight it
        is a day behind, and calendar.js rewrites the words in the browser. It
        can only do that if every clock-dependent word says which date it is
        about, and the page says which timezone "today" is in."""
        body = self._body("2026-08-06.html")
        self.assertIn('data-day-label="2026-08-05">Today<', body)
        self.assertIn('data-day-label="2026-08-06">Tomorrow<', body)
        self.assertIn('data-day-rel="2026-08-06">Tomorrow<', body)
        self.assertIn('"tz":2', body)

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
            r'class="dm-time day-res-time">\d{2}:\d{2}<')
        # The kickoff stands where the score would, so the same line must not
        # also carry one.
        line = body.split("day-res-time")[0].rsplit("<li", 1)[1]
        self.assertNotIn("dm-score", line, name)
        self.assertIn('class="dm is-fixture"', line, name)

    def test_an_empty_date_says_so_and_offers_the_nearest(self):
        empty = [f for f in self.files
                 if f != "index.html" and f[:-5] not in self.days]
        self.assertTrue(empty, "the window should span at least one empty date")
        body = self._body(empty[0])
        self.assertRegex(body, r"No matches (on this date|today|yesterday|tomorrow)\.")
        # Nothing to put a sidebar beside: the layout collapses to one column.
        self.assertIn('class="hm-grid is-empty"', body)
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

    def test_every_date_page_carries_the_competition_list(self):
        """The sidebar is the same on every date — that is what makes stepping
        to yesterday feel like the same page. (build.py renders the real list;
        this checks that whatever it hands over lands on every page.)"""
        for name in self.files:
            body = self._body(name)
            self.assertIn('id="leagues"', body, name)
            self.assertIn('href="../sl/"', body, name)


class HomeDayTest(unittest.TestCase):
    """The homepage is today's date page one directory up, so every link in
    its day region has to reach into matches/ — and a link that forgot to is
    a 404 on the most visited page on the site."""

    @classmethod
    def setUpClass(cls):
        texts = _load(dataset.TABS)
        nt_texts = _load(dataset.NT_TABS)
        if texts is None or nt_texts is None:
            raise unittest.SkipTest("data/canonical/ snapshot not present")
        cls.ds = dataset.parse_all(texts)
        cls.days = matches_page.collect(cls.ds, nt.parse_all(nt_texts))
        cls.hubs = {t.club_id for t in cls.ds.teams.values() if t.club_id}
        from src import flags
        cls.fl = flags.Flags(STATIC)

    def _home(self, today):
        return matches_page.home_day(self.ds, self.days, today, STATIC,
                                     self.hubs, self.fl)

    def test_date_links_point_into_matches(self):
        html, empty = self._home(TODAY)
        self.assertFalse(empty)
        written = set(matches_page.page_dates(self.days, TODAY))
        chips = re.findall(r'class="day-chip[^"]*" href="([^"]+)"', html)
        self.assertEqual(len(chips), 3)
        for href in chips:
            self.assertTrue(href.startswith("matches/"), href)
            self.assertIn(href[len("matches/"):-len(".html")], written)
        payload = json.loads(re.search(
            r"data-day-cal>(.*?)</script>", html, re.S).group(1))
        self.assertEqual(payload["base"], "matches/")

    def test_page_links_are_relative_to_the_site_root(self):
        html, _ = self._home(TODAY)
        self.assertNotIn('href="../', html)
        self.assertRegex(html, r'href="clubs/[^"]+\.html"')
        self.assertRegex(html, r'href="[a-z0-9-]+/results\.html"')

    def test_an_empty_today_says_so(self):
        empty_day = next(d for d in matches_page.page_dates(self.days, TODAY)
                         if d not in self.days)
        html, empty = self._home(empty_day)
        self.assertTrue(empty)
        self.assertIn("No matches today.", html)
        self.assertRegex(html, r'href="matches/\d{4}-\d{2}-\d{2}\.html"')


if __name__ == "__main__":
    unittest.main()
