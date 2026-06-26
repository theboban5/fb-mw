"""Tests for the standings rules and the loud-failure validation."""

import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src import data, standings  # noqa: E402
from src.data import Match, Team  # noqa: E402


def teams():
    return {
        "AAA": Team("AAA", "Alpha"),
        "BBB": Team("BBB", "Bravo"),
        "CCC": Team("CCC", "Charlie"),
    }


def match(row, md, home, away, hg, ag, date="2026-04-01"):
    return Match(row, md, date, home, away, hg, ag)


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


class ValidationTest(unittest.TestCase):
    def test_unknown_code_fails_loudly(self):
        ms = [match(2, 1, "AAA", "ZZZ", 1, 0)]
        with self.assertRaises(data.DataError) as ctx:
            data.validate_match_codes(ms, teams())
        msg = str(ctx.exception)
        self.assertIn("ZZZ", msg)
        self.assertIn("row 2", msg)

    def test_half_filled_score_rejected(self):
        text = (
            "matchday,date,home_code,away_code,home_goals,away_goals\n"
            "1,2026-04-01,AAA,BBB,2,\n"
        )
        with self.assertRaises(data.DataError):
            data.parse_matches(text)

    def test_bad_date_rejected(self):
        text = (
            "matchday,date,home_code,away_code,home_goals,away_goals\n"
            "1,01/04/2026,AAA,BBB,2,1\n"
        )
        with self.assertRaises(data.DataError):
            data.parse_matches(text)


if __name__ == "__main__":
    unittest.main()
