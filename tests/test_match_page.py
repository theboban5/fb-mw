"""Tests for the per-match pages (src/match_page.py) and the links into them.

Two things can go wrong here and both are silent on the live site: a link to
a match page nobody wrote (every list of matches is rendered before these
pages exist), and history that leaks forward — a "form going in" that
includes the match itself, or a head-to-head that counts a later meeting. So
this suite builds every page from the committed snapshot, walks every link in
them, and pins the before/after rule on real rows.
"""

import os
import re
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src import (adapt, dataset, hubs, match_page, matches_page, nt,  # noqa: E402
                 officials, render, standings)

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CANONICAL = os.path.join(ROOT, "data", "canonical")
TEMPLATES = os.path.join(ROOT, "templates")
STATIC = os.path.join(ROOT, "static")
TODAY = "2026-08-05"


def _load(tabs):
    return dataset.read_snapshot(CANONICAL, tabs)


def _leagues(ds):
    """The leagues and tables build.py would build — the same two calls."""
    leagues, tables = [], {}
    for cs in adapt.current_competition_seasons(ds):
        league = adapt.league_data(ds, cs.competition_id, cs.season_id)
        leagues.append(league)
        tables[league.slug] = ([] if league.kind == "cup" else
                               standings.compute_standings(
                                   league.matches, league.teams,
                                   points_win=league.points_win,
                                   points_draw=league.points_draw,
                                   adjustments=league.adjustments,
                                   groups=league.groups))
    return leagues, tables


class _Snapshot(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        texts = _load(dataset.TABS)
        nt_texts = _load(dataset.NT_TABS)
        if texts is None or nt_texts is None:
            raise unittest.SkipTest("data/canonical/ snapshot not present")
        cls.ds = dataset.parse_all(texts)
        cls.nt_data = nt.parse_all(nt_texts)
        cls.page_ids = match_page.match_page_ids(cls.ds)
        cls.leagues, cls.tables = _leagues(cls.ds)


class PageSetTest(_Snapshot):
    def test_every_match_on_a_matches_tab_gets_a_page(self):
        on_tabs = {m.match_id for league in self.leagues for m in league.matches}
        self.assertEqual(self.page_ids, on_tabs)

    def test_placeholders_get_none(self):
        for m in self.ds.matches.values():
            if m.is_placeholder:
                self.assertNotIn(m.match_id, self.page_ids)

    def test_the_href_callback_links_only_what_was_written(self):
        href = render.match_href_for("../", self.page_ids)
        some = next(iter(self.page_ids))
        self.assertEqual(href(some), f"../match/{some}.html")
        self.assertEqual(href("MW_NOT_A_MATCH"), "")
        self.assertEqual(href(""), "")
        # No set means no links, not every link.
        self.assertEqual(render.match_href_for("../")(some), "")


class HistoryTest(_Snapshot):
    """Form and head-to-head never look forward, and never include the match."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.by_team = match_page._results_by_team(cls.ds)

    def test_form_is_strictly_earlier_and_oldest_first(self):
        checked = 0
        for m in self.ds.matches.values():
            if not m.date or m.match_id not in self.page_ids:
                continue
            for team_id in (m.home_team_id, m.away_team_id):
                form = match_page.form_before(self.by_team, m, team_id)
                self.assertLessEqual(len(form), match_page.FORM_N)
                for o in form:
                    self.assertNotEqual(o.match_id, m.match_id)
                    self.assertLess(o.date, m.date)
                    self.assertIn(team_id, (o.home_team_id, o.away_team_id))
                self.assertEqual([o.date for o in form],
                                 sorted(o.date for o in form))
                checked += 1
        self.assertGreater(checked, 100)

    def test_meetings_are_this_pair_only_earlier_and_newest_first(self):
        found = 0
        for m in self.ds.matches.values():
            if not m.date:
                continue
            past = match_page.meetings_before(self.by_team, m)
            pair = {m.home_team_id, m.away_team_id}
            for o in past:
                self.assertEqual({o.home_team_id, o.away_team_id}, pair)
                self.assertLess(o.date, m.date)
            self.assertEqual([o.date for o in past],
                             sorted((o.date for o in past), reverse=True))
            found += bool(past)
        # Second legs exist in the snapshot, so something must have a history.
        self.assertGreater(found, 0)

    def test_a_fixture_counts_everything_already_played(self):
        fixture = next(m for m in self.ds.matches.values()
                       if m.status == "scheduled" and m.date
                       and not m.is_placeholder)
        played = [o for o in self.by_team.get(fixture.home_team_id, [])
                  if o.date < fixture.date]
        self.assertEqual(
            match_page.form_before(self.by_team, fixture, fixture.home_team_id),
            played[-match_page.FORM_N:])


class BuiltPagesTest(_Snapshot):
    """Builds every page and walks every link in them."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.player_pages = hubs.player_page_ids(cls.ds, ntd=cls.nt_data)
        cls.official_pages = officials.official_page_ids(cls.ds)
        cls.club_hub_ids = {tv.club_id for league in cls.leagues
                            for tv in league.teams.values() if tv.club_id}
        cls.tmp = tempfile.TemporaryDirectory()
        cls.count = match_page.build_pages(
            cls.tmp.name, TEMPLATES, STATIC, cls.ds, cls.leagues, cls.tables,
            "1 January 2026, 00:00 CAT", club_hub_ids=cls.club_hub_ids,
            player_pages=cls.player_pages, official_pages=cls.official_pages,
            page_ids=cls.page_ids)
        cls.dir = os.path.join(cls.tmp.name, match_page.SLUG)
        cls.slugs = {league.slug for league in cls.leagues}

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def _body(self, match_id):
        with open(os.path.join(self.dir, f"{match_id}.html"), encoding="utf-8") as fh:
            main = fh.read()
        return main[main.index("<main>"):main.index("</main>")]

    def test_one_page_per_id(self):
        self.assertEqual(self.count, len(self.page_ids))
        self.assertEqual({f[:-5] for f in os.listdir(self.dir)}, self.page_ids)

    def test_every_link_in_every_page_resolves(self):
        kinds = {
            "match": self.page_ids,
            "clubs": self.club_hub_ids,
            "players": self.player_pages,
            "officials": self.official_pages,
        }
        for match_id in self.page_ids:
            for href in re.findall(r'href="([^"]+)"', self._body(match_id)):
                m = re.fullmatch(r"\.\./(match|clubs|players|officials)/([^/]+)\.html",
                                 href)
                if m:
                    self.assertIn(m.group(2), kinds[m.group(1)], href)
                    continue
                m = re.fullmatch(r"\.\./([a-z0-9]+)/(?:results\.html)?", href)
                self.assertTrue(m and m.group(1) in self.slugs,
                                f"{match_id}: unexpected link {href}")

    def test_a_result_shows_its_score_and_a_fixture_its_kickoff(self):
        played = next(m for m in self.ds.matches.values()
                      if m.match_id in self.page_ids and m.status == "played"
                      and m.has_score)
        body = self._body(played.match_id)
        self.assertIn(f'<span class="mp-score">{played.home_goals}', body)
        fixture = next(m for m in self.ds.matches.values()
                       if m.match_id in self.page_ids and m.status == "scheduled"
                       and adapt.clock(m.kickoff))
        body = self._body(fixture.match_id)
        self.assertNotIn('class="mp-score"', body)
        self.assertIn(f'<span class="mp-time">{adapt.clock(fixture.kickoff)}', body)

    def test_the_table_highlights_both_sides_and_only_them(self):
        league = next(lg for lg in self.leagues
                      if lg.kind == "league" and not lg.groups and lg.matches)
        mv = league.matches[0]
        body = self._body(mv.match_id)
        self.assertEqual(body.count('<tr class="mp-hl">'), 2)

    def test_a_clustered_match_shows_its_own_cluster(self):
        league = next((lg for lg in self.leagues if lg.groups), None)
        if league is None:
            self.skipTest("no clustered competition in the snapshot")
        mv = next(m for m in league.matches if league.groups.get(m.home_code))
        group = league.groups[mv.home_code]
        body = self._body(mv.match_id)
        rows = body[body.index('class="v2-standings mp-table"'):]
        rows = rows[:rows.index("</table>")]
        in_group = sum(1 for g in league.groups.values() if g == group)
        self.assertEqual(rows.count("<tr"), in_group + 1)   # + the header

    def test_a_cup_has_no_table(self):
        cup = next((lg for lg in self.leagues if lg.kind == "cup" and lg.matches),
                   None)
        if cup is None:
            self.skipTest("no cup in the snapshot")
        self.assertNotIn("mp-table", self._body(cup.matches[0].match_id))

    def test_the_share_button_ships_hidden(self):
        body = self._body(next(iter(self.page_ids)))
        self.assertRegex(body, r'<button class="mp-share"[^>]* hidden>')


class LinksInTest(_Snapshot):
    """Every list of matches links into a page that exists — and only that."""

    def test_day_view_lines_link_to_written_pages(self):
        days = matches_page.collect(self.ds, self.nt_data)
        club_hub_ids = {t.club_id for t in self.ds.teams.values() if t.club_id}
        with tempfile.TemporaryDirectory() as tmp:
            matches_page.build_pages(
                tmp, TEMPLATES, STATIC, self.ds, "x", TODAY,
                nt_data=self.nt_data, club_hub_ids=club_hub_ids, days=days,
                match_pages=self.page_ids)
            linked = set()
            out = os.path.join(tmp, matches_page.SLUG)
            for name in os.listdir(out):
                with open(os.path.join(out, name), encoding="utf-8") as fh:
                    linked |= set(re.findall(
                        r'class="dm-go" href="\.\./match/([^"]+)\.html"', fh.read()))
        self.assertTrue(linked)
        self.assertLessEqual(linked, self.page_ids)

    def test_a_linked_line_does_not_also_link_the_clubs(self):
        # Nested targets on one line are what the stretched link replaces.
        day = next(d for d in matches_page.collect(self.ds).values() if d.groups)
        html = matches_page.render_day(
            self.ds, day, day.iso, day.iso, [day.iso], [day.iso], STATIC, "../",
            {t.club_id for t in self.ds.teams.values()}, None,
            match_pages=self.page_ids)
        for line in re.findall(r'<li class="dm[^"]*has-go.*?</li>', html):
            self.assertNotIn("dm-link", line)

    def test_results_scores_link_to_written_pages(self):
        league = next(lg for lg in self.leagues if lg.matches)
        html = render.render_results(
            league.matches, league.teams, match_pages=self.page_ids, compact=True)
        linked = set(re.findall(r'class="v2-res-link" href="\.\./match/([^"]+)\.html"',
                                html))
        self.assertEqual(linked, {m.match_id for m in league.matches})

    def test_no_match_pages_means_no_score_links(self):
        league = next(lg for lg in self.leagues if lg.matches)
        html = render.render_results(league.matches, league.teams, compact=True)
        self.assertNotIn("v2-res-link", html)


if __name__ == "__main__":
    unittest.main()
