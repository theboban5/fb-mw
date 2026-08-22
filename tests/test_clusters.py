"""A competition played as several tables at once (entries."group").

The NRFA Division Two League is thirty-two clubs in four clusters of eight,
sharing one fixture list, one scorer chart and one page. What these tests pin
down is that a rank is a rank INSIDE a cluster — everywhere a rank is shown.
"""

import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src import adapt, dataset, render, standings  # noqa: E402
from src.adapt import MatchView, TeamView  # noqa: E402


def teams(*codes):
    return {code: TeamView(code, code.title()) for code in codes}


def match(row, md, home, away, hg, ag, date="2026-04-01"):
    status = "played" if hg is not None else "scheduled"
    return MatchView(row, md, date, home, away, hg, ag, status=status)


# Two clusters of two. Ann beats Bob in A; Cal beats Dee in C — so the top of
# each cluster is a different team, and a single table would rank all four.
CLUSTERS = {"ann": "Cluster A", "bob": "Cluster A",
            "cal": "Cluster C", "dee": "Cluster C"}


def two_clusters():
    ms = [
        match(2, 1, "ann", "bob", 5, 0),
        match(3, 1, "cal", "dee", 1, 0),
    ]
    return standings.compute_standings(
        ms, teams("ann", "bob", "cal", "dee"), groups=CLUSTERS)


class GroupedStandingsTest(unittest.TestCase):
    def test_position_restarts_in_every_cluster(self):
        rows = {s.code: s for s in two_clusters()}
        self.assertEqual(rows["ann"].position, 1)
        self.assertEqual(rows["bob"].position, 2)
        # Not 3 and 4: cal has not played ann, and never will.
        self.assertEqual(rows["cal"].position, 1)
        self.assertEqual(rows["dee"].position, 2)

    def test_rows_come_back_in_cluster_order(self):
        self.assertEqual([s.group for s in two_clusters()],
                         ["Cluster A", "Cluster A", "Cluster C", "Cluster C"])

    def test_no_groups_is_one_table_with_positions(self):
        ms = [match(2, 1, "ann", "bob", 1, 0)]
        rows = standings.compute_standings(ms, teams("ann", "bob"))
        self.assertEqual([s.position for s in rows], [1, 2])
        self.assertEqual({s.group for s in rows}, {""})
        self.assertFalse(standings.has_groups(rows))

    def test_a_team_with_no_cluster_gets_its_own_table_last(self):
        # Missing data renders as nothing, never as a build failure: the
        # unlabelled team is not dropped and not filed under Cluster A.
        rows = standings.compute_standings(
            [], teams("ann", "bob", "zed"),
            groups={"ann": "Cluster A", "bob": "Cluster A"})
        self.assertEqual([(s.group, s.code) for s in rows][-1], ("", "zed"))
        self.assertEqual({s.code: s.position for s in rows}["zed"], 1)

    def test_by_group_cuts_the_list_into_tables(self):
        cut = standings.by_group(two_clusters())
        self.assertEqual([label for label, _ in cut], ["Cluster A", "Cluster C"])
        self.assertEqual([len(rows) for _, rows in cut], [2, 2])

    def test_by_group_of_an_ungrouped_league_is_one_table(self):
        rows = standings.compute_standings([], teams("ann", "bob"))
        self.assertEqual([label for label, _ in standings.by_group(rows)], [""])


class GroupedPositionChangeTest(unittest.TestCase):
    def test_an_arrow_compares_ranks_inside_the_cluster(self):
        # Matchday 2 turns Cluster C over; Cluster A is untouched by it and
        # must not move because a team in another cluster won.
        ms = [
            match(2, 1, "ann", "bob", 1, 0),
            match(3, 1, "cal", "dee", 1, 0),
            match(4, 2, "dee", "cal", 5, 0),
        ]
        changes = standings.position_changes(
            ms, teams("ann", "bob", "cal", "dee"), groups=CLUSTERS)
        self.assertEqual(changes["ann"], "same")
        self.assertEqual(changes["bob"], "same")
        self.assertEqual(changes["dee"], "up")
        self.assertEqual(changes["cal"], "down")

    def test_history_is_a_rank_inside_the_cluster(self):
        ms = [
            match(2, 1, "ann", "bob", 1, 0),
            match(3, 1, "cal", "dee", 1, 0),
        ]
        days, history = standings.position_history(
            ms, teams("ann", "bob", "cal", "dee"), groups=CLUSTERS)
        self.assertEqual(days, [1])
        # Two firsts and two seconds, not 1-2-3-4.
        self.assertEqual(sorted(p[0] for p in history.values()), [1, 1, 2, 2])


class ClusterMarkupTest(unittest.TestCase):
    def html(self, **kwargs):
        return render.render_standings(two_clusters(), league_name="Div 2",
                                       **kwargs)

    def test_one_section_and_one_chip_per_cluster(self):
        html = self.html()
        self.assertEqual(html.count('class="v2-grp"'), 2)
        self.assertIn('data-grp="Cluster A"', html)
        self.assertIn('data-grp="Cluster C"', html)
        self.assertIn(">Cluster A</h3>", html)
        # "All" first, then one chip per cluster.
        self.assertIn('data-grp-chip=""', html)
        self.assertIn('data-grp-chip="Cluster A"', html)
        self.assertEqual(html.count("data-grp-chip"), 3)

    def test_the_strip_ships_hidden(self):
        # Progressive enhancement, exactly as the matchday pager: with no JS
        # every cluster shows under its own heading.
        self.assertIn("data-grp-pager hidden", self.html())

    def test_chips_drop_the_word_every_cluster_shares(self):
        html = self.html()
        self.assertIn('data-grp-chip="Cluster A">A</button>', html)

    def test_a_chip_keeps_a_label_that_is_the_distinguishing_part(self):
        rows = standings.compute_standings(
            [], teams("ann", "bob"), groups={"ann": "North", "bob": "South"})
        html = render.render_standings(rows, league_name="Div 2")
        self.assertIn('data-grp-chip="North">North</button>', html)

    def test_every_table_numbers_from_one(self):
        html = self.html()
        # Two leaders, not one: each cluster has a 1.
        self.assertEqual(html.count("v2-pos-leader"), 2)

    def test_qualification_zone_is_read_inside_the_cluster(self):
        # Top two of eight go through; in a cluster of two that is both rows,
        # and the point is that the marker never lands on row 2 of the whole
        # list of four.
        html = self.html(promotion_spots=2)
        self.assertEqual(html.count("v2-pos-promotion"), 2)

    def test_an_ungrouped_league_renders_no_chips_and_no_sections(self):
        rows = standings.compute_standings(
            [match(2, 1, "ann", "bob", 1, 0)], teams("ann", "bob"))
        html = render.render_standings(rows, league_name="Prem")
        self.assertNotIn("data-grp-chip", html)
        self.assertNotIn('class="v2-grp"', html)
        self.assertIn('class="v2-standings"', html)

    def test_club_page_names_the_cluster_beside_the_position(self):
        rows = two_clusters()
        html = render.render_club("bob", [], teams("ann", "bob"), rows,
                                  league_name="Div 2")
        self.assertIn("Div 2 &middot; Cluster A &middot; 2nd", html)

    def test_overview_draws_one_chart_per_cluster(self):
        ms = [match(2, 1, "ann", "bob", 1, 0), match(3, 1, "cal", "dee", 1, 0)]
        rows = two_clusters()
        days, history = standings.position_history(
            ms, teams("ann", "bob", "cal", "dee"), groups=CLUSTERS)
        html = render.render_overview(
            ms, teams("ann", "bob", "cal", "dee"), days, history, rows,
            league_name="Div 2")
        self.assertEqual(html.count("<svg"), 2)
        self.assertIn(">Cluster A</h3>", html)


def clustered_dataset():
    """One competition, four teams, two clusters — as entries would hold it."""
    ds = dataset.Dataset()
    ds.competitions["MW_D2"] = dataset.Competition(
        "MW_D2", "mw", "Division Two", "league", 4, "m", "senior", "North",
        "NRFA", "")
    ds.seasons["S1"] = dataset.Season(
        "S1", "MW", "2026/27", "2026-04-01", "2027-06-30", "active")
    ds.competition_seasons[("MW_D2", "S1")] = dataset.CompetitionSeason(
        "MW_D2", "S1", "", "clusters", 4, 2, 0, 3, 1, "active")
    for tid, group in (("T1", "Cluster A"), ("T2", "Cluster A"),
                       ("T3", "Cluster B"), ("T4", "")):
        club = f"MW_{tid}"
        ds.clubs[club] = dataset.Club(
            club, tid, "", "City", "", "", "", "", "", "")
        ds.teams[tid] = dataset.Team(tid, club, "m", "senior", 1, tid,
                                     f"D2_{tid}", "")
        ds.entries[f"E_{tid}"] = dataset.Entry(
            f"E_{tid}", "MW_D2", "S1", tid, group, 0, "", "active")
    return ds


class DatasetToLeagueTest(unittest.TestCase):
    def test_the_cluster_travels_from_entries_to_the_league(self):
        league = adapt.league_data(clustered_dataset(), "MW_D2", "S1")
        self.assertEqual(league.groups,
                         {"D2_T1": "Cluster A", "D2_T2": "Cluster A",
                          "D2_T3": "Cluster B"})
        # A blank group is absent rather than present-and-empty: it is the
        # single-table case, and every ungrouped competition maps to {}.
        self.assertNotIn("D2_T4", league.groups)

    def test_an_ungrouped_competition_carries_no_groups(self):
        ds = clustered_dataset()
        for key, e in list(ds.entries.items()):
            ds.entries[key] = dataset.Entry(
                e.entry_id, e.competition_id, e.season_id, e.team_id, "",
                e.points_adjustment, e.adjustment_reason, e.status)
        self.assertEqual(adapt.league_data(ds, "MW_D2", "S1").groups, {})


if __name__ == "__main__":
    unittest.main()
