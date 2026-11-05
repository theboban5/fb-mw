"""scripts/rsssf_nt.py against the committed RSSSF internationals page.

Offline, no database. The check parse makes — the lines against RSSSF's own
summary, every remaining difference pinned — is the main assertion; the rest
pin the page's quirks, each of which that check would only report as a number
being off.
"""

import os
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "scripts"))

import rsssf_nt as R  # noqa: E402


def parsed():
    with open(os.path.join(R.DATA_DIR, R.PAGE), "rb") as fh:
        return R.parse_page(fh.read(), R.load_names())


def line(rows, start):
    return next(r for r in rows if r["rsssf"]["line"].startswith(start))


class PageTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.rows, cls.tables, cls.errors = parsed()

    def test_agrees_with_rsssf_but_for_the_pinned_differences(self):
        self.assertEqual(self.errors, [])

    def test_every_line_is_read(self):
        self.assertEqual(len(self.rows), 710)
        self.assertEqual(sum(1 for r in self.rows if r["unofficial"]), 35)

    def test_unidentified_scorers_match_rsssfs_count(self):
        # The one summary figure that agrees exactly — and the one that says
        # every scorer form below is read right.
        official = [r for r in self.rows if not r["unofficial"]]
        self.assertEqual(sum(r["unidentified_goals"] for r in official), 25)

    def test_a_wrapped_scorer_line_is_rejoined(self):
        djibouti = line(self.rows, "31/05/08 DJIBOUTI")
        self.assertEqual(len(djibouti["goals"]), 8)
        self.assertEqual(djibouti["goals"][-1]["player_name"], "N. Mkwandawire")

    def test_scorer_forms(self):
        # 'x2-1Pen' is two goals, one of them a penalty.
        osman = [g for g in line(self.rows, "06/07/73 Mauritius")["goals"]
                 if g["player_name"] == "Y. Osman"]
        self.assertEqual([g["goal_type"] for g in osman], ["penalty", ""])
        # Several minutes for one scorer, stoppage time, and an own goal.
        self.assertEqual(
            [(g["minute"], g["goal_type"])
             for g in line(self.rows, "05/06/22 ETHIOPIA")["goals"]],
            [("10", "penalty"), ("34", "penalty")])
        self.assertEqual(line(self.rows, "02/03/25 Comoros")["goals"][1]["stoppage"], "1")
        og = line(self.rows, "06/07/25 Botswana")["goals"][0]
        self.assertEqual((og["player_name"], og["goal_type"]),
                         ("M. Gaolaolwe", "own goal"))
        # A half and no minute; a comma where a '>' belongs.
        self.assertEqual(line(self.rows, "06/06/00 Nigeria")["goals"][0]["period"], "2h")
        self.assertEqual(len(line(self.rows, "22/11/92 Ethiopia")["goals"]), 2)

    def test_a_trailing_separator_is_an_unknown_scorer(self):
        phiri = line(self.rows, "13/09/76 Botswana")   # 7-1, 'K.Phiri x4>'
        self.assertEqual(len(phiri["goals"]), 4)
        self.assertEqual(phiri["unidentified_goals"], 3)

    def test_shootouts_abandonments_and_extra_time(self):
        sa = line(self.rows, "07/06/19 South Africa")
        self.assertEqual((sa["shootout_score"], sa["extra_time_result"]), ("4-5", "loss"))
        kenya = line(self.rows, "22/04/00 KENYA")
        self.assertEqual((kenya["status"], kenya["abandoned_minute"]), ("abandoned", 87))
        zim = line(self.rows, "16/11/88 Zimbabwe")
        self.assertEqual(zim["abandoned_minute"], 107)
        self.assertTrue(zim["extra_time"])
        self.assertTrue(line(self.rows, "19/11/88 Zambia")["extra_time"])

    def test_ground_from_the_venue_code(self):
        self.assertEqual(line(self.rows, "08/09/25 LIBERIA")["home_away"], "home")
        self.assertEqual(line(self.rows, "24/03/25 TUNISIA")["home_away"], "away")
        neutral = line(self.rows, "05/09/25 NAMIBIA")   # in Botswana
        self.assertEqual((neutral["neutral"], neutral["home_away"]), (True, None))

    def test_names_are_the_names_of_the_day(self):
        swz = line(self.rows, "04/09/16 SWAZILAND")
        self.assertEqual((swz["opponent"], swz["opponent_country"]),
                         ("Swaziland", "Eswatini"))
        self.assertEqual(swz["country"], "Malawi")
        self.assertEqual(line(self.rows, "06/09/15 SWAZILAND")["country"], "Swaziland")
        self.assertEqual(line(self.rows, "04/09/94 ZAIRE")["opponent_country"], "DR Congo")
        self.assertEqual(line(self.rows, "13/10/25 SAO TOME")["opponent"],
                         "São Tomé and Príncipe")
        self.assertEqual(line(self.rows, "20/07/64 Nth Rhodesia")["country"],
                         "Northern Rhodesia")

    def test_stage_and_unofficial_come_off_the_competition(self):
        r = line(self.rows, "26/03/24 Zambia")
        self.assertEqual((r["competition"], r["stage"]), ("Four Nations Tournament", "3p"))
        r = line(self.rows, "06/07/08 Kenya")
        self.assertEqual(r["competition"], "Malawi Independence Tournament")
        self.assertTrue(r["unofficial"])
        self.assertTrue(line(self.rows, "07/12/10 Ivory Coast CHAN")["unofficial"])

    def test_pre_independence_and_a_year_only_date(self):
        first = self.rows[-1]
        self.assertEqual((first["date"], first["year"]), ("", 1957))
        self.assertTrue(first["pre_independence"] and first["unofficial"])
        pre = [r for r in self.rows if r["pre_independence"]]
        self.assertEqual(len(pre), 6)
        self.assertTrue(all(r["unofficial"] for r in pre))

    def test_corrections_settled_by_the_page(self):
        swz = line(self.rows, "08/07/04 Swaziland")
        self.assertEqual((swz["team_score"], swz["unidentified_goals"]), (2, 0))
        self.assertIn("correction", swz["rsssf"])


class CheckTest(unittest.TestCase):
    """The check must still catch what it exists to catch."""

    def setUp(self):
        self.rows, self.tables, _ = parsed()
        self.names = R.load_names()

    def test_a_misread_score_fails(self):
        line(self.rows, "18/11/25 Lesotho")["team_score"] = 2
        errors = R.check(self.rows, self.tables, self.names)
        self.assertTrue(any(e.startswith("LESOTHO: pinned") for e in errors), errors)

    def test_a_lost_unofficial_flag_fails(self):
        line(self.rows, "07/10/20 Zambia")["unofficial"] = False
        self.assertNotEqual(R.check(self.rows, self.tables, self.names), [])

    def test_a_new_difference_in_a_clean_country_fails(self):
        line(self.rows, "17/11/19 UGANDA")["opponent_country"] = "Kenya"
        errors = R.check(self.rows, self.tables, self.names)
        self.assertTrue(any(e.startswith("KENYA:") and "unexplained" in e
                            for e in errors), errors)

    def test_a_pin_that_no_longer_differs_must_be_removed(self):
        self.names["table_differences"]["BENIN"] = {"lines": "x", "rsssf": "y"}
        errors = R.check(self.rows, self.tables, self.names)
        self.assertIn("BENIN: pinned in table_differences but now agrees — "
                      "remove the pin", errors)

    def test_an_unmapped_name_stops_the_parse(self):
        del self.names["opponents"]["zambia"]
        with open(os.path.join(R.DATA_DIR, R.PAGE), "rb") as fh:
            with self.assertRaises(R.ParseError):
                R.parse_page(fh.read(), self.names)


class LoadPlanTest(unittest.TestCase):
    """`apply`'s plan, against the committed snapshot standing in for the live
    table — the same rows, without a network."""

    @classmethod
    def setUpClass(cls):
        cls.rows, _, _ = parsed()
        cls.live = R.snapshot_rows()
        cls.goals = [{"goal_id": str(i), "match_id": "1"} for i in range(1, 87)]
        cls.inserts, cls.goal_inserts, cls.patches = R.plan_load(
            cls.rows, cls.live, cls.goals)

    def test_pairs_the_typed_rows_and_inserts_the_rest(self):
        self.assertEqual(len(self.inserts), 702)
        self.assertEqual(len(self.goal_inserts), 783)

    def test_ids_follow_the_table_and_stay_numeric(self):
        # next_nt_id (0012) casts every id to an integer.
        ids = [int(m["match_id"]) for m in self.inserts]
        self.assertEqual(ids[0], max(int(m["match_id"]) for m in self.live) + 1)
        self.assertEqual(ids, list(range(ids[0], ids[0] + len(ids))))
        self.assertEqual(int(self.goal_inserts[0]["goal_id"]), 87)

    def test_the_cosafa_dates_are_corrected_and_nothing_typed_is_overwritten(self):
        dates = {d["match_id"]: p.get("date") for d, p in self.patches}
        self.assertEqual(dates["2"], "2025-06-08")
        self.assertEqual(dates["3"], "2025-06-10")
        for _d, patch in self.patches:
            self.assertLessEqual(set(patch), {"date", *R._FILL})

    def test_inserts_are_backfill_oldest_first(self):
        self.assertTrue(all(m["source_type"] == "backfill" for m in self.inserts))
        first = self.inserts[0]
        self.assertEqual((first["date"], first["date_year"]), ("", 1957))
        self.assertTrue(first["unofficial"])

    def test_a_neutral_ground_is_stored_away(self):
        m = next(m for m in self.inserts if m["neutral"])
        self.assertEqual(m["home_away"], "away")

    def test_a_second_run_plans_nothing(self):
        live = [dict(m) for m in self.live] + [dict(m) for m in self.inserts]
        for d, patch in self.patches:
            next(m for m in live if m["match_id"] == d["match_id"]).update(patch)
        goals = self.goals + [{"goal_id": g["goal_id"], "match_id": g["match_id"]}
                              for g in self.goal_inserts]
        self.assertEqual(R.plan_load(self.rows, live, goals), ([], [], []))

    def test_an_archive_match_missing_its_goals_gets_them_on_the_next_run(self):
        live = [dict(m) for m in self.live] + [dict(m) for m in self.inserts]
        goals = self.goals    # the match inserts landed, the goals did not
        _i, goal_inserts, _p = R.plan_load(self.rows, live, goals)
        self.assertEqual(len(goal_inserts), 783)


if __name__ == "__main__":
    unittest.main()
