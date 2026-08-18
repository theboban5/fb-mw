"""Tests for league team sheets: the `lineups` tab, its rules, and the pages.

Three things are being protected here, in rising order of how badly they fail:

  * **The tab parses and the rules bite.** A twelfth starter or a substitution
    naming nobody is an ERROR that aborts the build, exactly as on the
    national-team tab — because src/lineups.py pairs a substitute to the
    starter they replaced BY NAME, and a dangling name renders as one.
  * **The two schemas stay in step.** `lineups` and `nt_lineups` are the same
    shape on purpose, so one folding implementation and one renderer serve
    both. The moment they drift, one of the two pages silently loses a
    feature, and nothing else would notice.
  * **Every link goes somewhere.** A player is linked from a team sheet only
    when a page was written for them, and that set comes from ONE function so
    that the pages, the search index and the national-team page cannot
    disagree about it.
"""

import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src import dataset, hubs, lineups, nt, render, source_supabase  # noqa: E402
from src.dataset import DataError  # noqa: E402
import validate  # noqa: E402

HEADER = (
    "match_id,team_id,player_name,player_id,shirt_number,position,role,"
    "captain,minute_on,minute_off,replaced_player,yellow_card,"
    "yellow_red_card,red_card\n"
)


def rows(*lines):
    return HEADER + "".join(line + "\n" for line in lines)


# A complete, legal side: eleven starters, one substitution, one unused sub.
SIDE = rows(
    "M1,T_HOME,Mercy Sikelo,P1,1,GK,starting,,,,,,,",
    "M1,T_HOME,Ireen Khumalo,P2,2,DF,starting,,,,,1,,",
    "M1,T_HOME,Chimwemwe Madise,P3,3,DF,starting,,,,,,,",
    "M1,T_HOME,Adam Ali,P4,4,DF,starting,1,,,,,,",
    "M1,T_HOME,Mario Mkhoma,P5,5,DF,starting,,,,,,,",
    "M1,T_HOME,Precious Phiri,P6,6,MF,starting,,,,,,,",
    "M1,T_HOME,Slay Antony,P7,7,MF,starting,,,,,,,",
    "M1,T_HOME,Chifundo Molosen,P8,8,MF,starting,,,,,,,",
    "M1,T_HOME,Joseph Ziwa,P9,9,MF,starting,,,63,,,,",
    "M1,T_HOME,Zebron Kalima,P10,10,FW,starting,,,,,,,",
    "M1,T_HOME,Joseph Banda,P11,11,FW,starting,,,,,,,",
    "M1,T_HOME,Saulos Moyo,P12,12,FW,sub_on,,63,,Joseph Ziwa,,,",
    "M1,T_HOME,Enock Jobo,P13,13,MF,unused_sub,,,,,,,",
)


class ParseTest(unittest.TestCase):
    def test_a_whole_side_parses(self):
        parsed = dataset.parse_lineups(SIDE)
        self.assertEqual(len(parsed), 13)
        self.assertEqual(parsed[0].player_name, "Mercy Sikelo")
        self.assertEqual(parsed[0].position, "GK")
        self.assertTrue(parsed[3].captain)
        self.assertTrue(parsed[1].yellow_card)

    def test_position_is_optional_here(self):
        """A league sheet often arrives as eleven names off a photo."""
        parsed = dataset.parse_lineups(
            rows("M1,T_HOME,Mercy Sikelo,P1,1,,starting,,,,,,,"))
        self.assertEqual(parsed[0].position, "")

    def test_position_is_still_a_vocabulary(self):
        with self.assertRaises(DataError):
            dataset.parse_lineups(
                rows("M1,T_HOME,Mercy Sikelo,P1,1,SWEEPER,starting,,,,,,,"))

    def test_role_must_be_one_of_three(self):
        with self.assertRaises(DataError):
            dataset.parse_lineups(
                rows("M1,T_HOME,Mercy Sikelo,P1,1,GK,benched,,,,,,,"))

    def test_a_missing_name_is_an_error(self):
        with self.assertRaises(DataError):
            dataset.parse_lineups(rows("M1,T_HOME,,P1,1,GK,starting,,,,,,,"))


class SchemaParityTest(unittest.TestCase):
    """`lineups` is nt_lineups' shape on purpose. Prove it, and keep it."""

    def test_the_two_tabs_carry_the_same_columns(self):
        league = set(source_supabase.COLUMNS["lineups"])
        national = set(source_supabase.COLUMNS["nt_lineups"])
        self.assertEqual(league, national)

    def test_the_sheets_fallback_header_matches_the_emitter(self):
        """One tab, two places that state its columns — they must agree.

        dataset.SUPABASE_ONLY_TABS emits the header for a sheets build;
        source_supabase.COLUMNS emits it for a Supabase build. A drift between
        them would make the deprecated fallback produce a tab the parser
        rejects, which is the sort of thing nobody discovers until the day the
        fallback is needed.
        """
        self.assertEqual(
            tuple(dataset.SUPABASE_ONLY_TABS["lineups"]),
            tuple(source_supabase.COLUMNS["lineups"]))

    def test_the_row_classes_agree_on_every_attribute_lineups_py_reads(self):
        league = dataset.parse_lineups(SIDE)[0]
        for attribute in ("player_name", "player_id", "shirt_number", "position",
                          "role", "captain", "minute_on", "minute_off",
                          "replaced_player", "yellow_card", "yellow_red_card",
                          "red_card", "shirt_sort"):
            self.assertTrue(hasattr(league, attribute), attribute)
            self.assertTrue(hasattr(nt.NTLineupRow, "__dataclass_fields__"))
            self.assertTrue(
                attribute in nt.NTLineupRow.__dataclass_fields__
                or hasattr(nt.NTLineupRow, attribute), attribute)


class FoldTest(unittest.TestCase):
    def setUp(self):
        self.lineup = lineups.fold(dataset.parse_lineups(SIDE))

    def test_it_splits_into_three(self):
        self.assertEqual(len(self.lineup.starting), 11)
        self.assertEqual(len(self.lineup.substitutions), 1)
        self.assertEqual(len(self.lineup.unused), 1)

    def test_a_substitution_pairs_by_name(self):
        sub = self.lineup.substitutions[0]
        self.assertEqual(sub.on.player_name, "Saulos Moyo")
        self.assertEqual(sub.off_name, "Joseph Ziwa")
        self.assertEqual(sub.minute, "63")

    def test_a_substitution_naming_nobody_still_renders(self):
        """Dropping it would lose the fact that somebody came on."""
        lineup = lineups.fold(dataset.parse_lineups(rows(
            "M1,T_HOME,Mercy Sikelo,P1,1,GK,starting,,,,,,,",
            "M1,T_HOME,Saulos Moyo,P12,12,FW,sub_on,,63,,Nobody At All,,,",
        )))
        self.assertEqual(len(lineup.substitutions), 1)
        self.assertEqual(lineup.substitutions[0].off_name, "Nobody At All")

    def test_positions_group_in_reading_order(self):
        labels = [label for label, _rs in self.lineup.starting_by_position()]
        self.assertEqual(labels, ["Goalkeepers", "Defenders", "Midfielders",
                                  "Forwards"])

    def test_no_rows_is_no_lineup(self):
        self.assertIsNone(lineups.fold([]))


class MarkupTest(unittest.TestCase):
    def setUp(self):
        self.lineup = lineups.fold(dataset.parse_lineups(SIDE))

    def test_a_name_links_only_when_the_href_resolves(self):
        linked = lineups.lineup_row_html(
            self.lineup, player_href=lambda pid: f"../players/{pid}.html")
        self.assertIn('href="../players/P1.html"', linked)

        plain = lineups.lineup_row_html(self.lineup)
        self.assertNotIn("<a", plain)
        self.assertIn("Mercy Sikelo", plain)

    def test_the_captain_and_the_cards_render(self):
        html = lineups.lineup_row_html(self.lineup)
        self.assertIn("el-cap", html)          # Adam Ali
        self.assertIn("el-card-y", html)       # Ireen Khumalo
        self.assertNotIn("el-card-r", html)

    def test_a_replaced_starter_shows_the_minute_they_came_off(self):
        html = lineups.lineup_row_html(self.lineup)
        self.assertIn("63", html)

    def test_both_sides_render_under_one_toggle(self):
        html = lineups.two_sided_row_html(
            self.lineup, self.lineup, "Blue Eagles", "Civil Service United")
        self.assertIn("Blue Eagles", html)
        self.assertIn("Civil Service United", html)
        self.assertEqual(html.count("<details"), 1)

    def test_one_side_entered_still_renders_that_side(self):
        html = lineups.two_sided_row_html(
            self.lineup, None, "Blue Eagles", "Civil Service United")
        self.assertIn("Blue Eagles", html)
        self.assertNotIn("Civil Service United", html)

    def test_no_sheet_at_all_renders_nothing(self):
        self.assertEqual(
            lineups.two_sided_row_html(None, None, "A", "B"), "")

    def test_the_unknown_player_never_becomes_a_link(self):
        """No page is written for it, so a link would be a 404."""
        self.assertEqual(render._player_href(dataset.UNKNOWN_PLAYER_ID), "")
        self.assertEqual(render._player_href(""), "")


# ── The validator ────────────────────────────────────────────────────────────

class _Match:
    def __init__(self, home="T_HOME", away="T_AWAY"):
        self.home_team_id, self.away_team_id = home, away


class _DS:
    """Just enough Dataset for check_lineups, which reads three attributes."""
    def __init__(self, text):
        self.lineups = dataset.parse_lineups(text)
        self.matches = {"M1": _Match()}


class ValidatorTest(unittest.TestCase):
    def errors(self, text):
        return validate.check_lineups(_DS(text))

    def test_a_legal_side_passes(self):
        self.assertEqual(self.errors(SIDE), [])

    def test_a_twelfth_starter_is_an_error(self):
        text = SIDE + "M1,T_HOME,Twelfth Man,P14,14,FW,starting,,,,,,,\n"
        self.assertTrue(any("starting" in e for e in self.errors(text)))

    def test_eleven_per_SIDE_not_per_match(self):
        """Both sides may field eleven; the count is per match AND team."""
        away = SIDE.replace("T_HOME", "T_AWAY").replace(HEADER, "")
        self.assertEqual(self.errors(SIDE + away), [])

    def test_a_substitute_needs_a_minute(self):
        text = rows("M1,T_HOME,Saulos Moyo,P12,12,FW,sub_on,,,,Joseph Ziwa,,,")
        self.assertTrue(any("minute_on" in e for e in self.errors(text)))

    def test_a_replaced_player_must_be_on_this_side(self):
        text = rows(
            "M1,T_HOME,Joseph Ziwa,P9,9,MF,starting,,,,,,,",
            "M1,T_AWAY,Saulos Moyo,P12,12,FW,sub_on,,63,,Joseph Ziwa,,,")
        self.assertTrue(any("replaced_player" in e for e in self.errors(text)))

    def test_a_second_yellow_and_a_straight_red_is_an_error(self):
        text = rows("M1,T_HOME,Mercy Sikelo,P1,1,GK,starting,,,,,,1,1")
        self.assertTrue(any("second yellow IS a red" in e
                            for e in self.errors(text)))

    def test_a_team_that_did_not_play_is_an_error(self):
        text = rows("M1,T_ELSEWHERE,Mercy Sikelo,P1,1,GK,starting,,,,,,,")
        self.assertTrue(any("did not play" in e for e in self.errors(text)))

    def test_an_unresolvable_match_is_left_to_check_2(self):
        """Reporting it twice would be two errors for one broken row."""
        text = rows("M9,T_HOME,Mercy Sikelo,P1,1,GK,starting,,,,,,,")
        self.assertEqual(self.errors(text), [])

    def test_the_tab_has_a_primary_key(self):
        self.assertEqual(validate.PRIMARY_KEYS["lineups"],
                         ("match_id", "team_id", "player_name"))

    def test_the_same_player_twice_on_one_side_is_a_duplicate(self):
        text = SIDE + "M1,T_HOME,Mercy Sikelo,P1,1,GK,unused_sub,,,,,,,\n"
        self.assertTrue(validate.check_primary_keys(
            {"lineups": text}, keys={"lineups": validate.PRIMARY_KEYS["lineups"]}))


class SnapshotTest(unittest.TestCase):
    """The tab is optional everywhere: almost every match has no sheet."""

    def test_an_absent_tab_reads_as_an_empty_one(self):
        text = dataset.empty_csv("lineups")
        self.assertEqual(dataset.parse_lineups(text), [])

    def test_a_dataset_with_no_sheets_still_parses(self):
        self.assertIn("lineups", dataset.TABS)
        self.assertIn("lineups", dataset._PARSERS)


class CareerTest(unittest.TestCase):
    """What a profile counts, and what it deliberately does not."""

    def test_an_unused_substitute_is_not_an_appearance(self):
        self.assertFalse(hubs._playable(
            dataset.parse_lineups(
                rows("M1,T,X,P1,1,GK,unused_sub,,,,,,,"))[0]))
        self.assertTrue(hubs._playable(
            dataset.parse_lineups(
                rows("M1,T,X,P1,1,GK,starting,,,,,,,"))[0]))

    def test_an_unidentified_row_earns_nobody_anything(self):
        self.assertFalse(hubs._identified(""))
        self.assertFalse(hubs._identified(dataset.UNKNOWN_PLAYER_ID))
        self.assertTrue(hubs._identified("CAF_MW_000001"))

    def test_outcome_reads_from_the_players_own_side(self):
        self.assertEqual(hubs._outcome(2, 1), ("2-1", "W"))
        self.assertEqual(hubs._outcome(1, 1), ("1-1", "D"))
        self.assertEqual(hubs._outcome(0, 3), ("0-3", "L"))
        self.assertEqual(hubs._outcome(None, None), ("", ""))


if __name__ == "__main__":
    unittest.main()
