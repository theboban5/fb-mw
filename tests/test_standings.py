"""Tests for the standings rules and the loud-failure validation."""

import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src import dataset, standings  # noqa: E402
from src.adapt import MatchView, TeamView  # noqa: E402
import validate  # noqa: E402


def teams():
    return {
        "AAA": TeamView("AAA", "Alpha"),
        "BBB": TeamView("BBB", "Bravo"),
        "CCC": TeamView("CCC", "Charlie"),
    }


def match(row, md, home, away, hg, ag, date="2026-04-01"):
    status = "played" if hg is not None else "scheduled"
    return MatchView(row, md, date, home, away, hg, ag, status=status)


class StandingsTest(unittest.TestCase):
    def test_points_win_draw_loss(self):
        ms = [
            match(2, 1, "AAA", "BBB", 2, 0),  # AAA win
            match(3, 1, "BBB", "CCC", 1, 1),  # draw
        ]
        table = {s.code: s for s in standings.compute_standings(ms, teams())}
        self.assertEqual(table["AAA"].points, 3)
        self.assertEqual(table["BBB"].points, 1)
        self.assertEqual(table["CCC"].points, 1)
        self.assertEqual(table["AAA"].won, 1)
        self.assertEqual(table["CCC"].played, 1)

    def test_goals_and_gd(self):
        ms = [match(2, 1, "AAA", "BBB", 3, 1)]
        table = {s.code: s for s in standings.compute_standings(ms, teams())}
        self.assertEqual(table["AAA"].gf, 3)
        self.assertEqual(table["AAA"].ga, 1)
        self.assertEqual(table["AAA"].gd, 2)
        self.assertEqual(table["BBB"].gd, -2)

    def test_unplayed_matches_ignored(self):
        ms = [match(2, 1, "AAA", "BBB", None, None)]
        table = {s.code: s for s in standings.compute_standings(ms, teams())}
        self.assertEqual(table["AAA"].played, 0)
        self.assertEqual(table["AAA"].points, 0)

    def test_sort_order(self):
        # Alpha and Bravo tie on points; Bravo has better GD -> ranks first.
        ms = [
            match(2, 1, "AAA", "CCC", 1, 0),  # AAA +1
            match(3, 1, "BBB", "CCC", 5, 0),  # BBB +5
        ]
        order = [s.code for s in standings.compute_standings(ms, teams())]
        self.assertEqual(order[0], "BBB")
        self.assertEqual(order[1], "AAA")

    def test_sort_tiebreak_alphabetical(self):
        # Both win by the same margin and score -> alphabetical by name.
        ms = [
            match(2, 1, "BBB", "CCC", 1, 0),
            match(3, 1, "AAA", "CCC", 1, 0),
        ]
        order = [s.name for s in standings.compute_standings(ms, teams())][:2]
        self.assertEqual(order, ["Alpha", "Bravo"])

    def test_configurable_points_and_adjustment(self):
        # competition_seasons points + entries.points_adjustment (negative).
        ms = [
            match(2, 1, "AAA", "BBB", 2, 0),
            match(3, 1, "BBB", "CCC", 1, 1),
        ]
        table = {s.code: s for s in standings.compute_standings(
            ms, teams(), points_win=2, points_draw=1,
            adjustments={"AAA": -3, "CCC": 1},
        )}
        self.assertEqual(table["AAA"].points, -1)  # 2 for the win, -3 adj
        self.assertEqual(table["BBB"].points, 1)
        self.assertEqual(table["CCC"].points, 2)   # 1 draw + 1 adj

    def test_awarded_match_counts_with_recorded_score(self):
        m = MatchView(2, 1, "2026-04-01", "AAA", "BBB", 3, 0, status="awarded")
        table = {s.code: s for s in standings.compute_standings([m], teams())}
        self.assertEqual(table["AAA"].points, 3)
        self.assertEqual(table["AAA"].gf, 3)


class FormTest(unittest.TestCase):
    def test_recent_form_orders_oldest_to_newest(self):
        ms = [
            match(2, 1, "AAA", "BBB", 2, 0),  # AAA W
            match(3, 2, "CCC", "AAA", 1, 1),  # AAA D
            match(4, 3, "AAA", "BBB", 0, 3),  # AAA L
        ]
        form = standings.recent_form(ms, teams())
        self.assertEqual(form["AAA"], ["W", "D", "L"])
        self.assertEqual(form["BBB"], ["L", "W"])

    def test_recent_form_caps_at_last_n(self):
        ms = [match(i, i - 1, "AAA", "BBB", 1, 0) for i in range(2, 9)]  # 7 AAA wins
        form = standings.recent_form(ms, teams(), last_n=5)
        self.assertEqual(form["AAA"], ["W"] * 5)

    def test_recent_form_ignores_unplayed(self):
        ms = [match(2, 1, "AAA", "BBB", None, None)]
        self.assertEqual(standings.recent_form(ms, teams())["AAA"], [])


class PositionChangeTest(unittest.TestCase):
    def test_single_matchday_is_all_same(self):
        ms = [match(2, 1, "AAA", "BBB", 1, 0)]
        self.assertEqual(
            standings.position_changes(ms, teams()),
            {"AAA": "same", "BBB": "same", "CCC": "same"},
        )

    def test_climbing_team_is_up_and_overtaken_is_down(self):
        # After MD1: Bravo top (won big), Alpha 2nd. After MD2 Alpha overtakes.
        ms = [
            match(2, 1, "BBB", "CCC", 5, 0),  # BBB +5
            match(3, 1, "AAA", "CCC", 1, 0),  # AAA +1  -> BBB 1st, AAA 2nd
            match(4, 2, "AAA", "CCC", 9, 0),  # AAA now far ahead on GD -> 1st
        ]
        changes = standings.position_changes(ms, teams())
        self.assertEqual(changes["AAA"], "up")
        self.assertEqual(changes["BBB"], "down")


class PositionHistoryTest(unittest.TestCase):
    def test_history_tracks_position_each_matchday(self):
        ms = [
            match(2, 1, "AAA", "BBB", 1, 0),  # MD1: AAA leads
            match(3, 2, "BBB", "AAA", 5, 0),  # MD2: BBB jumps ahead on GD
        ]
        days, history = standings.position_history(ms, teams())
        self.assertEqual(days, [1, 2])
        self.assertEqual(history["AAA"], [1, 2])
        self.assertEqual(history["BBB"][-1], 1)


class DatasetParseTest(unittest.TestCase):
    def test_bad_date_rejected(self):
        text = (
            "season_id,country,label,start_date,end_date,status\n"
            "MW_2026_27,MW,2026/27,01/04/2026,2027-06-30,active\n"
        )
        with self.assertRaises(dataset.DataError):
            dataset.parse_seasons(text)

    def test_duplicate_primary_key_rejected(self):
        text = (
            "club_id,name,status\n"
            "MW_AAA,Alpha,active\n"
            "MW_AAA,Alpha Again,active\n"
        )
        with self.assertRaises(dataset.DataError):
            dataset.parse_clubs(text)

    def test_unknown_enum_rejected(self):
        text = (
            "match_id,competition_id,season_id,matchday,date,venue_id,"
            "home_team_id,away_team_id,home_goals,away_goals,status,"
            "source_type,confidence\n"
            "M1,C1,S1,1,2026-04-01,,T1,T2,1,0,finished,fa,confirmed\n"
        )
        with self.assertRaises(dataset.DataError):
            dataset.parse_matches(text)


class ValidatorTest(unittest.TestCase):
    MATCH_HEADER = (
        "match_id,competition_id,season_id,matchday,date,venue_id,"
        "home_team_id,away_team_id,home_goals,away_goals,status,"
        "source_type,confidence\n"
    )

    def _matches_ds(self, row):
        texts = {
            "matches": self.MATCH_HEADER + row,
            "seasons": ("season_id,country,label,start_date,end_date,status\n"
                        "S1,MW,2026/27,2026-04-01,2027-06-30,active\n"),
        }
        ds = dataset.Dataset(
            matches=dataset.parse_matches(texts["matches"]),
            seasons=dataset.parse_seasons(texts["seasons"]),
        )
        return ds

    def test_played_without_score_fails(self):
        ds = self._matches_ds("M1,C1,S1,1,2026-04-01,,T1,T2,,,played,fa,confirmed\n")
        errs = validate.check_match_consistency(ds)
        self.assertTrue(any("goals are blank" in e for e in errs))

    def test_scheduled_with_score_fails(self):
        ds = self._matches_ds("M1,C1,S1,1,2026-04-01,,T1,T2,2,1,scheduled,fa,confirmed\n")
        errs = validate.check_match_consistency(ds)
        self.assertTrue(any("status=scheduled" in e for e in errs))

    def test_self_play_fails(self):
        ds = self._matches_ds("M1,C1,S1,1,2026-04-01,,T1,T1,2,1,played,fa,confirmed\n")
        errs = validate.check_match_consistency(ds)
        self.assertTrue(any("home_team_id == away_team_id" in e for e in errs))

    def test_match_date_outside_season_fails(self):
        ds = self._matches_ds("M1,C1,S1,1,2028-01-01,,T1,T2,2,1,played,fa,confirmed\n")
        errs = validate.check_dates(ds)
        self.assertTrue(any("outside season" in e for e in errs))


if __name__ == "__main__":
    unittest.main()
