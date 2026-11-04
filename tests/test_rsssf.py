"""scripts/rsssf.py against the committed RSSSF pages. Offline, no database.

The strongest assertion here is the one parse itself makes: the results add
up to RSSSF's own final table, team by team. These tests pin that it still
holds for every committed season, plus the page quirks that check would not
explain by itself if it broke.
"""

import os
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "scripts"))

import rsssf  # noqa: E402


def parsed(label):
    with open(os.path.join(rsssf.DATA_DIR, rsssf.PAGES[label]), "rb") as fh:
        return rsssf.parse_page(fh.read(), label)


class EverySeasonTest(unittest.TestCase):
    def test_every_season_adds_up_to_rsssfs_table(self):
        names = rsssf.load_names()
        for label in rsssf.PAGES:
            with self.subTest(season=label):
                p = parsed(label)
                self.assertEqual(rsssf.unmapped(p, names, label), [])
                self.assertEqual(len(p["results"]), 240)
                errors, order = rsssf.check(p, names, label)
                self.assertEqual(errors, [])
                # Head to head (src/standings.py) settles 2023's 13th/14th;
                # nothing is left to the alphabet.
                self.assertEqual(order, [])

    def test_every_date_is_inside_its_season(self):
        names = rsssf.load_names()
        for label in rsssf.PAGES:
            with self.subTest(season=label):
                spec = rsssf.to_spec(parsed(label), names, label)
                s = spec["season"]
                for f in spec["fixtures"]:
                    self.assertTrue(s["start_date"] <= f["date"] <= s["end_date"], f)


class PageQuirksTest(unittest.TestCase):
    def test_2025_champions_and_points(self):
        table = parsed("2025")["table"]
        self.assertEqual([(r["name"], r["pts"]) for r in table[:3]], [
            ("Mighty Wanderers (Blantyre)", 69),
            ("Nyasa Big Bullets FC (Blantyre)", 66),
            ("Silver Strikers (Lilongwe)", 64)])
        self.assertEqual(sum(r["relegated"] for r in table), 3)

    def test_abandoned_match_is_left_out_and_its_replay_kept(self):
        p = parsed("2025")
        pairs = [(r["home"], r["away"]) for r in p["results"]]
        self.assertEqual(pairs.count(("Mighty Wanderers", "Ekhaya")), 1)
        self.assertTrue(any("abandoned" in w for w in p["warnings"]))

    def test_points_deduction_is_read(self):
        nchalo = next(r for r in parsed("2018")["table"] if r["name"] == "Nchalo United")
        self.assertEqual(nchalo["adjustment"], -3)
        self.assertEqual(nchalo["pts"], 17)

    def test_awarded_matches_take_the_awarded_score(self):
        awarded = [r for r in parsed("2016")["results"] if r["status"] == "awarded"]
        self.assertEqual(len(awarded), 9)
        first = awarded[0]
        self.assertEqual((first["home"], first["home_goals"], first["away_goals"]),
                         ("EPAC United", 3, 0))
        self.assertTrue(first["awarded_note"].startswith("Awarded 3-0"))

    def test_split_season_rolls_the_year(self):
        dates = [r["date"] for r in parsed("2020/21")["results"]]
        self.assertTrue(dates[0].startswith("2020-11"))
        self.assertTrue(dates[-1].startswith("2021-"))
        self.assertEqual(rsssf.season_id_for("2020/21"), "MW_2020_21")

    def test_round_headers_are_not_matchdays(self):
        # 2023 prints "Round N" for the first half only; reading it would put
        # 120 second-half matches on "matchday 15".
        for label in ("2020/21", "2022", "2023", "2024"):
            with self.subTest(season=label):
                self.assertTrue(all("matchday" not in r for r in parsed(label)["results"]))

    def test_a_spelling_not_in_names_json_is_named(self):
        p = parsed("2025")
        missing = rsssf.unmapped(p, {"spellings": {}}, "2025")
        self.assertIn("Bullets", missing)


if __name__ == "__main__":
    unittest.main()
