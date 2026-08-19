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


# The same, with 0028's column on the end. A separate header on purpose: the
# fixtures below it pre-date the column and must keep parsing without it, which
# is the state every row written before 0028 is in.
MOTM_HEADER = HEADER.rstrip("\n") + ",motm\n"


def motm_rows(*lines):
    return MOTM_HEADER + "".join(line + "\n" for line in lines)


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

    def test_a_sheet_with_no_positions_still_renders_its_eleven(self):
        """The bug the first real league sheet hit.

        Position is optional on this tab, and grouping ONLY by position meant a
        whole starting XI disappeared under its own "Starting XI" heading.
        """
        lineup = lineups.fold(dataset.parse_lineups(*[rows(*[
            f"M1,T_HOME,Player {c},P{i},,,starting,,,,,,,"
            for i, c in enumerate("KJIHGFEDCBA")])]))
        groups = lineup.starting_by_position()
        self.assertEqual([label for label, _rs in groups], [""])
        self.assertEqual(len(groups[0][1]), 11)
        html = lineups.lineup_row_html(lineup)
        for c in "KJIHGFEDCBA":
            self.assertIn(f"Player {c}", html)
        # A blank label renders no heading, not an empty one.
        self.assertNotIn("el-pos-title", html)

    def test_a_positionless_player_joins_a_positioned_sheet(self):
        lineup = lineups.fold(dataset.parse_lineups(rows(
            "M1,T_HOME,Keeper,P1,1,GK,starting,,,,,,,",
            "M1,T_HOME,Nobody Knows,P2,2,,starting,,,,,,,")))
        groups = lineup.starting_by_position()
        self.assertEqual([label for label, _rs in groups], ["Goalkeepers", ""])
        self.assertIn("Nobody Knows", lineups.lineup_row_html(lineup))

    def test_no_shirts_keeps_the_order_the_sheet_was_entered_in(self):
        """Alphabetical would be wrong: a team sheet is written keeper-first."""
        lineup = lineups.fold(dataset.parse_lineups(rows(
            "M1,T_HOME,Zebedee Keeper,P1,,,starting,,,,,,,",
            "M1,T_HOME,Andrew Striker,P2,,,starting,,,,,,,")))
        names = [r.player_name for _l, rs in lineup.starting_by_position() for r in rs]
        self.assertEqual(names, ["Zebedee Keeper", "Andrew Striker"])

    def test_shirt_numbers_still_order_a_sheet_that_has_them(self):
        lineup = lineups.fold(dataset.parse_lineups(rows(
            "M1,T_HOME,Number Nine,P9,9,FW,starting,,,,,,,",
            "M1,T_HOME,Number Seven,P7,7,FW,starting,,,,,,,")))
        names = [r.player_name for _l, rs in lineup.starting_by_position() for r in rs]
        self.assertEqual(names, ["Number Seven", "Number Nine"])

    def test_a_substitution_with_nobody_named_drops_the_dangling_for(self):
        """A reporter naming three replacements out of four is normal."""
        lineup = lineups.fold(dataset.parse_lineups(rows(
            "M1,T_HOME,Came On,P1,,,sub_on,,87,,,,,")))
        html = lineups.lineup_row_html(lineup)
        self.assertIn("Came On", html)
        self.assertNotIn("el-sub-for", html)
        self.assertNotIn(" for ", html)

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


class OptionalPositionTest(unittest.TestCase):
    """Position is optional on every team-sheet tab, league and national.

    Both /report screens offer a blank position, and both save RPCs accept it.
    Requiring it in a parser therefore meant one reporter leaving a dropdown
    alone could stop every future build.
    """

    HEAD = ("match_id,team_id,player_name,player_id,shirt_number,position,role,"
            "captain,minute_on,minute_off,replaced_player,yellow_card,"
            "yellow_red_card,red_card\n")

    def test_league_tab_accepts_a_blank_position(self):
        self.assertEqual(
            dataset.parse_lineups(rows("M1,T,No Position,P1,,,starting,,,,,,,"))[0].position,
            "")

    def test_national_lineup_tab_accepts_a_blank_position(self):
        row = nt.parse_nt_lineups(
            self.HEAD + "36,MW_W,No Position,P1,1,,starting,,,,,,,\n")[0]
        self.assertEqual(row.position, "")

    def test_national_squad_tab_accepts_a_blank_position(self):
        head = ("squad_id,competition,match_id_range,team_id,announcement_date,"
                "competition_context,player_name,player_id,position,shirt_number,"
                "club,domestic_team_id,club_country,notes,coach\n")
        row = nt.parse_nt_squads(head + "1,WAFCON,,MW_W,2026-07-11,,No Position,P1,,,,,,,\n")[0]
        self.assertEqual(row.position, "")

    def test_a_wrong_position_is_still_rejected(self):
        with self.assertRaises(DataError):
            nt.parse_nt_lineups(
                self.HEAD + "36,MW_W,Bad,P1,1,SWEEPER,starting,,,,,,,\n")


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

    def test_an_unused_substitute_is_still_clickable(self):
        """They get a page — their name is a link on the team sheet — but the
        match counts as bench, never as an appearance."""
        career = hubs.Career(appearances=[], goals=0, assists=0, bench=[object()])
        self.assertEqual(len(career.appearances), 0)
        self.assertEqual(len(career.bench), 1)
        self.assertIn("without coming on", hubs._bench_note(career))
        # And a player who HAS played gets no such note.
        played = hubs.Career(appearances=[object()], goals=0, assists=0, bench=[object()])
        self.assertEqual(hubs._bench_note(played), "")

    def test_an_unidentified_row_earns_nobody_anything(self):
        self.assertFalse(hubs._identified(""))
        self.assertFalse(hubs._identified(dataset.UNKNOWN_PLAYER_ID))
        self.assertTrue(hubs._identified("CAF_MW_000001"))

    def test_outcome_reads_from_the_players_own_side(self):
        self.assertEqual(hubs._outcome(2, 1), ("2-1", "W"))
        self.assertEqual(hubs._outcome(1, 1), ("1-1", "D"))
        self.assertEqual(hubs._outcome(0, 3), ("0-3", "L"))
        self.assertEqual(hubs._outcome(None, None), ("", ""))


class ManOfTheMatchTest(unittest.TestCase):
    """One star, on one name, in the whole match (0028).

    The rule worth protecting is the one that differs from every other flag on
    this tab: the armband is one per SIDE and this is one per MATCH, so the
    count that catches a mistake has to be taken across both sheets. Postgres
    says it with a partial unique index; check 10 says it again because a row
    can also arrive by import.
    """

    STARRED = motm_rows(
        "M1,T_HOME,Mercy Sikelo,P1,1,GK,starting,,,,,,,,1",
        "M1,T_HOME,Adam Ali,P4,4,DF,starting,1,,,,,,,")

    def parsed(self, text=None):
        return dataset.parse_lineups(text if text is not None else self.STARRED)

    def test_the_column_parses(self):
        starred, plain = self.parsed()
        self.assertTrue(starred.motm)
        self.assertFalse(plain.motm)

    def test_a_sheet_without_the_column_is_nobody(self):
        """Every row written before 0028 reads as "nobody recorded one"."""
        self.assertFalse(any(r.motm for r in dataset.parse_lineups(SIDE)))

    def test_the_star_renders_beside_that_name_only(self):
        html = lineups.lineup_row_html(lineups.fold(self.parsed()))
        self.assertEqual(html.count("el-motm"), 1)
        self.assertLess(html.index("Mercy Sikelo"), html.index("el-motm"))

    def test_it_reaches_the_players_own_match_table(self):
        row = hubs._match_stat_row(hubs.Appearance(
            date="2026-08-15", competition="Super League", opponent="Creck",
            team_label="Silver Strikers", club_id="MW_SS", team_id="MW_SS_M1",
            national=False, home=True, started=True, minute_on="", minute_off="",
            captain=False, shirt_number="1", position="GK", goals=0, assists=0,
            yellow_card=False, yellow_red_card=False, red_card=False,
            scoreline="1-0", outcome="W", motm=True))
        self.assertIn("el-motm", row)

    def test_two_stars_in_one_match_is_an_error(self):
        """Across the two SIDES — the case a per-sheet count would miss."""
        text = motm_rows(
            "M1,T_HOME,Mercy Sikelo,P1,1,GK,starting,,,,,,,,1",
            "M1,T_AWAY,Zebron Kalima,P20,9,FW,starting,,,,,,,,1")
        errors = validate.check_lineups(_DS(text))
        self.assertTrue(any("one per match" in e for e in errors), errors)

    def test_one_star_across_two_sides_passes(self):
        text = motm_rows(
            "M1,T_HOME,Mercy Sikelo,P1,1,GK,starting,,,,,,,,1",
            "M1,T_AWAY,Zebron Kalima,P20,9,FW,starting,,,,,,,,")
        self.assertEqual(validate.check_lineups(_DS(text)), [])

    def test_the_national_tab_carries_it_too(self):
        """Both row types, because src/lineups.py draws the badge for both."""
        self.assertIn("motm", nt.NTLineupRow.__dataclass_fields__)
        self.assertIn("motm", dataset.LineupRow.__dataclass_fields__)


class BenchRowTest(unittest.TestCase):
    """An unused substitute's match belongs on their page, marked DNP.

    WHAT WAS WRONG. The match table listed appearances only, so a player who
    came on in one match and sat unused in another had a page showing one and
    no trace of the other — while the sheet for the missing match linked
    straight here. The tiles are untouched: a bench call is still not a game
    played, which is exactly what the DNP row says out loud.
    """

    def appearance(self, date, played=True, **kw):
        return hubs.Appearance(
            date=date, competition="Super League", opponent="Creck Sporting",
            team_label="Silver Strikers", club_id="MW_SS", team_id="MW_SS_M1",
            national=False, home=True, started=False, minute_off="",
            captain=False, shirt_number="", position="", goals=0, assists=0,
            yellow_card=False, yellow_red_card=False, red_card=False,
            scoreline="1-0", outcome="W", played=played,
            minute_on=kw.pop("minute_on", ""), **kw)

    def career(self):
        return hubs.Career(
            appearances=[self.appearance("2026-08-15", minute_on="87")],
            goals=1, assists=0,
            bench=[self.appearance("2026-08-08", played=False)])

    def test_the_bench_match_is_a_row(self):
        html = hubs._match_stats(self.career())
        self.assertEqual(html.count("pl-match-row"), 2)
        self.assertIn("DNP", html)

    def test_it_is_still_not_an_appearance(self):
        career = self.career()
        tiles = hubs._summary_tiles(career)
        # One app, not two: "games played" must not become "games named".
        self.assertIn('>1</span><span class="pl-tile-l">Apps', tiles)

    def test_the_rows_are_in_one_chronology(self):
        html = hubs._match_stats(self.career())
        self.assertLess(html.index("15 Aug"), html.index("8 Aug"))

    def test_a_player_with_neither_gets_no_table(self):
        self.assertEqual(
            hubs._match_stats(hubs.Career(appearances=[], goals=0, assists=0)), "")


class CanonicalNameTest(unittest.TestCase):
    """The name on a sheet is a label on an id, and a label can be repainted.

    This is what makes it safe to enter "A. Josephy" off a Facebook graphic on
    the night: the id is the person, and correcting the players row later moves
    every team sheet with it. Without this the profile would say "Andrew
    Josephy" while the line-up it was linked from still said "A. Josephy".
    """

    NAMES = {"P1": "Andrew Josephy", "P12": "Saulos Moyo Banda"}

    def name_of(self, player_id):
        return self.NAMES.get(player_id, "")

    def test_an_identified_row_takes_the_registry_name(self):
        rows_in = dataset.parse_lineups(
            rows("M1,T_HOME,A. Josephy,P1,4,MF,starting,,,,,,,"))
        out = lineups.with_canonical_names(rows_in, self.name_of)
        self.assertEqual(out[0].player_name, "Andrew Josephy")

    def test_an_unidentified_row_keeps_what_was_typed(self):
        """A blank id is "nobody has identified them yet", not an error."""
        rows_in = dataset.parse_lineups(
            rows("M1,T_HOME,A. Josephy,,4,MF,starting,,,,,,,"))
        out = lineups.with_canonical_names(rows_in, self.name_of)
        self.assertEqual(out[0].player_name, "A. Josephy")

    def test_an_id_from_another_namespace_keeps_its_name(self):
        """An opponent's national-team id resolves to no registry, ever."""
        rows_in = dataset.parse_lineups(
            rows("M1,T_HOME,Mass Kosiah,INT_LIB_KOSIAH,9,FW,starting,,,,,,,"))
        out = lineups.with_canonical_names(rows_in, self.name_of)
        self.assertEqual(out[0].player_name, "Mass Kosiah")

    def test_a_substitution_still_pairs_after_a_rename(self):
        """replaced_player is matched BY NAME, so it has to move too."""
        rows_in = dataset.parse_lineups(rows(
            "M1,T_HOME,A. Josephy,P1,4,MF,starting,,,63,,,,",
            "M1,T_HOME,S. Moyo,P12,12,FW,sub_on,,63,,A. Josephy,,,",
        ))
        lineup = lineups.fold(lineups.with_canonical_names(rows_in, self.name_of))
        sub = lineup.substitutions[0]
        self.assertEqual(sub.on.player_name, "Saulos Moyo Banda")
        self.assertIsNotNone(sub.off)
        self.assertEqual(sub.off_name, "Andrew Josephy")

    def test_nothing_to_rename_returns_the_rows_untouched(self):
        rows_in = dataset.parse_lineups(SIDE)
        self.assertEqual(
            lineups.with_canonical_names(rows_in, lambda _pid: ""), rows_in)

    def test_the_registry_lookup_ignores_the_reserved_row(self):
        ds = dataset.Dataset()
        ds.players["CAF_MW_000001"] = dataset.Player(
            "CAF_MW_000001", "Andrew Josephy", "", "", "", "", "active")
        ds.players[dataset.UNKNOWN_PLAYER_ID] = dataset.Player(
            dataset.UNKNOWN_PLAYER_ID, "Unknown", "", "", "", "", "")
        self.assertEqual(ds.registry_name("CAF_MW_000001"), "Andrew Josephy")
        self.assertEqual(ds.registry_name(dataset.UNKNOWN_PLAYER_ID), "")
        self.assertEqual(ds.registry_name(""), "")
        self.assertEqual(ds.registry_name("CAF_MW_999999"), "")


class PositionTagTest(unittest.TestCase):
    """The position labels one player, not everyone under a heading.

    It used to be a heading over a group, which on the sheets this site
    actually gets said the wrong thing: position is optional, so a graphic
    naming only the keeper rendered "Goalkeepers" and then all eleven names,
    ten of them under a heading that did not describe them.
    """

    def test_the_position_sits_beside_the_name(self):
        html = lineups.lineup_row_html(lineups.fold(dataset.parse_lineups(SIDE)))
        self.assertIn('<span class="el-pos-tag">GK</span>', html)
        self.assertIn('<span class="el-pos-tag">FW</span>', html)
        # And no heading anywhere.
        self.assertNotIn("el-pos-title", html)
        self.assertNotIn("Goalkeepers", html)

    def test_a_sheet_with_no_positions_reserves_no_column(self):
        """An empty tag on every row would indent eleven names for nothing."""
        lineup = lineups.fold(dataset.parse_lineups(rows(*[
            f"M1,T_HOME,Player {c},P{i},,,starting,,,,,,,"
            for i, c in enumerate("ABCDEFGHIJK")])))
        html = lineups.lineup_row_html(lineup)
        self.assertNotIn("el-pos-tag", html)
        for c in "ABCDEFGHIJK":
            self.assertIn(f"Player {c}", html)

    def test_a_positionless_player_keeps_the_column_aligned(self):
        lineup = lineups.fold(dataset.parse_lineups(rows(
            "M1,T_HOME,Keeper,P1,1,GK,starting,,,,,,,",
            "M1,T_HOME,Nobody Knows,P2,2,,starting,,,,,,,")))
        html = lineups.lineup_row_html(lineup)
        self.assertIn('<span class="el-pos-tag">GK</span>', html)
        # Empty, not absent: the names stay in one column.
        self.assertIn('<span class="el-pos-tag"></span>', html)
        self.assertIn("Nobody Knows", html)

    def test_one_list_in_reading_order(self):
        lineup = lineups.fold(dataset.parse_lineups(rows(
            "M1,T_HOME,Striker,P1,9,FW,starting,,,,,,,",
            "M1,T_HOME,Nobody Knows,P2,20,,starting,,,,,,,",
            "M1,T_HOME,Keeper,P3,1,GK,starting,,,,,,,",
            "M1,T_HOME,Stopper,P4,4,DF,starting,,,,,,,")))
        html = lineups.lineup_row_html(lineup)
        order = [html.index(n) for n in
                 ("Keeper", "Stopper", "Striker", "Nobody Knows")]
        self.assertEqual(order, sorted(order))
        # One list, not four.
        self.assertEqual(html.count('<ul class="el-players">'), 1)


class SwitchPlayerTest(unittest.TestCase):
    """Team-mates, one tap away — the thing every route to a profile wants next."""

    def appearance(self, team_id="T_HOME", shirt="7", date="2026-08-01"):
        return hubs.Appearance(
            date=date, competition="Super League", opponent="Someone",
            team_label="Blue Eagles", club_id="MW_BE", team_id=team_id,
            national=False, home=True, started=True, minute_on="",
            minute_off="", captain=False, shirt_number=shirt, position="MF",
            goals=0, assists=0, yellow_card=False, yellow_red_card=False,
            red_card=False, scoreline="2-1", outcome="W")

    def careers(self):
        return {
            "P1": hubs.Career(appearances=[self.appearance()], goals=0, assists=0),
            "P2": hubs.Career(appearances=[self.appearance(shirt="9")],
                              goals=0, assists=0),
            # Named and never used: still in the squad, still has a page.
            "P3": hubs.Career(appearances=[], goals=0, assists=0,
                              bench=[self.appearance(shirt="14")]),
            # Another club entirely.
            "P4": hubs.Career(appearances=[self.appearance(team_id="T_AWAY")],
                              goals=0, assists=0),
        }

    def ds_with(self, *ids):
        ds = dataset.Dataset()
        for i, pid in enumerate(ids, start=1):
            ds.players[pid] = dataset.Player(
                pid, f"Player {i}", "", "", "", "", "active")
        return ds

    def test_a_squad_is_everyone_with_a_page(self):
        squads = hubs.squads_by_team(self.careers(), {"P1", "P2", "P3", "P4"})
        self.assertEqual({pid for pid, _shirt in squads["T_HOME"]},
                         {"P1", "P2", "P3"})
        self.assertEqual({pid for pid, _shirt in squads["T_AWAY"]}, {"P4"})

    def test_a_player_with_no_page_is_never_listed(self):
        """A name here that has no page written for it is a 404."""
        squads = hubs.squads_by_team(self.careers(), {"P1", "P2"})
        self.assertEqual({pid for pid, _shirt in squads["T_HOME"]}, {"P1", "P2"})

    def test_the_block_lists_the_others_and_not_the_player(self):
        careers = self.careers()
        squads = hubs.squads_by_team(careers, {"P1", "P2", "P3", "P4"})
        html = hubs._switch_player(
            "P1", careers["P1"], squads, self.ds_with("P1", "P2", "P3"))
        self.assertIn("Switch Player", html)
        self.assertIn('href="P2.html"', html)
        self.assertIn('href="P3.html"', html)
        self.assertNotIn('href="P1.html"', html)
        self.assertNotIn('href="P4.html"', html)

    def test_a_squad_of_one_renders_nothing(self):
        careers = self.careers()
        squads = hubs.squads_by_team(careers, {"P4"})
        self.assertEqual(
            hubs._switch_player("P4", careers["P4"], squads,
                                self.ds_with("P4")), "")

    def test_a_player_with_no_side_at_all_renders_nothing(self):
        empty = hubs.Career(appearances=[], goals=0, assists=0)
        self.assertEqual(hubs._switch_player("P9", empty, {}, dataset.Dataset()), "")

    def test_the_back_link_falls_back_to_the_home_page(self):
        """No JavaScript, or arriving from Facebook: the href is the answer."""
        self.assertIn('href="../"', hubs.PLAYER_BACK)
        self.assertIn("history.back()", hubs.PLAYER_BACK)


class GoalBadgeTest(unittest.TestCase):
    """A ball beside the name, one per goal — and a red A per assist.

    The join is the point: a sheet said who played and a block above it said
    who scored, and nothing connected the two — which is the one thing a reader
    scanning eleven names is actually looking for. The assist moved here from
    brackets after the scorer on the result line, where it put two people
    inside what reads as one fact.
    """

    def counts(self, **by_key):
        def goals_of(player_id, player_name):
            return by_key.get(player_id) or by_key.get(player_name) or (0, 0, 0)
        return goals_of

    def test_two_goals_draw_two_balls(self):
        rows_in = dataset.parse_lineups(
            rows("M1,T_HOME,Zebron Kalima,P1,9,FW,starting,,,,,,,"))
        out = lineups.with_goals(rows_in, self.counts(P1=(2, 0, 0)))
        html = lineups.player_html(out[0])
        self.assertEqual(html.count('class="el-goal"'), 2)

    def test_nobody_scored_draws_nothing(self):
        rows_in = dataset.parse_lineups(SIDE)
        out = lineups.with_goals(rows_in, self.counts())
        self.assertNotIn("el-goal", lineups.lineup_row_html(lineups.fold(out)))

    def test_the_name_is_the_fallback_join(self):
        """An unidentified player still gets their ball."""
        rows_in = dataset.parse_lineups(
            rows("M1,T_HOME,A. Josephy,,4,MF,starting,,,,,,,"))
        out = lineups.with_goals(
            rows_in, self.counts(**{"A. Josephy": (1, 0, 0)}))
        self.assertEqual(out[0].goals, 1)

    def test_an_own_goal_is_marked_apart(self):
        """It is a ball at the wrong end, and must not read as a goal."""
        rows_in = dataset.parse_lineups(
            rows("M1,T_HOME,Own Goaler,P2,5,DF,starting,,,,,,,"))
        out = lineups.with_goals(rows_in, self.counts(P2=(0, 1, 0)))
        html = lineups.player_html(out[0])
        self.assertIn("el-goal-og", html)
        self.assertIn('title="Own goal"', html)

    def test_a_substitute_who_scores_gets_one_too(self):
        rows_in = dataset.parse_lineups(rows(
            "M1,T_HOME,Starter,P1,4,MF,starting,,,63,,,,",
            "M1,T_HOME,Emmanuel Allan,P2,12,FW,sub_on,,63,,Starter,,,",
        ))
        out = lineups.fold(lineups.with_goals(rows_in, self.counts(P2=(1, 0, 0))))
        html = lineups.lineup_body(out)
        self.assertIn("el-goal", html.split("Substitutions")[1])

    def test_the_rows_are_untouched_when_there_is_nothing_to_add(self):
        rows_in = dataset.parse_lineups(SIDE)
        self.assertEqual(lineups.with_goals(rows_in, self.counts()), rows_in)

    def test_the_nt_row_carries_the_same_fields(self):
        """One renderer serves both schemas, so both rows have to hold them."""
        for name in ("goals", "own_goals", "assists"):
            self.assertIn(name, nt.NTLineupRow.__dataclass_fields__)

    def test_an_assist_is_a_red_a_beside_the_name(self):
        rows_in = dataset.parse_lineups(
            rows("M1,T_HOME,Rahim Mtondera,P3,8,MF,starting,,,,,,,"))
        out = lineups.with_goals(rows_in, self.counts(P3=(0, 0, 2)))
        html = lineups.player_html(out[0])
        self.assertEqual(html.count('class="el-assist"'), 2)
        self.assertIn('title="Assist"', html)

    def test_a_goal_and_an_assist_can_be_the_same_player(self):
        rows_in = dataset.parse_lineups(
            rows("M1,T_HOME,Busy Person,P4,7,FW,starting,,,,,,,"))
        out = lineups.with_goals(rows_in, self.counts(P4=(1, 0, 1)))
        html = lineups.player_html(out[0])
        self.assertIn("el-goal", html)
        self.assertIn("el-assist", html)


if __name__ == "__main__":
    unittest.main()
