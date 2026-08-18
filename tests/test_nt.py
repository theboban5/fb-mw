"""Tests for the national-team section: the nt_* tabs, filtering, and the page.

The end-to-end case is match 36 (Malawi 3-2 Nigeria, WAFCON 2026) — the one
match in the real data that has goals for both sides plus line-up rows, so it
exercises every rule at once.
"""

import os
import re
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src import flags  # noqa: E402
from src import nt  # noqa: E402
from src import nt_page  # noqa: E402
from src.dataset import DataError  # noqa: E402
import validate  # noqa: E402

STATIC = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                      "static")

TEAMS = (
    "team_code,team_name,category\n"
    "MW_W,Malawi (Women's),senior\n"
    "MW_M,Malawi (Men's),senior\n"
)

MATCH_HEADER = (
    "match_id,team_code,date,competition,opponent,home_away,neutral,venue,"
    "city,country,team_score,opponent_score,status,coach,extra_time,"
    "penalty_shootout,extra_time_result,kickoff\n"
)

GOAL_HEADER = (
    "goal_id,match_id,team_id,player_name,player_id,minute,stoppage,period,"
    "goal_type,source_ref\n"
)

SQUAD_HEADER = (
    "squad_id,competition,match_id_range,team_id,announcement_date,"
    "competition_context,player_name,player_id,position,shirt_number,club,"
    "domestic_team_id,club_country,notes,coach\n"
)

LINEUP_HEADER = (
    "match_id,team_id,player_name,player_id,shirt_number,position,role,"
    "captain,minute_on,minute_off,replaced_player,yellow_card,"
    "yellow_red_card,red_card\n"
)

COMP_HEADER = (
    "team_code,competition_name,group_name,position,played,won,drawn,lost,"
    "points,last_update,wikipedia_url,team_name,goals_for,goals_against\n"
)

# ── The match-36 fixture, trimmed to what the page renders ───────────────────

M36_MATCHES = MATCH_HEADER + (
    "36,MW_W,2026-07-28,WAFCON,Nigeria,away,TRUE,Al Medina Stadium,Rabat,"
    "Morocco,3,2,played,Lovemore Fazili,FALSE,FALSE,\n"
    "37,MW_W,2026-08-01,WAFCON,Egypt,away,TRUE,Moulay El Hassan Stadium,"
    "Rabat,Morocco,,,scheduled,,FALSE,FALSE,,20:00\n"
    # Another team's row: must never reach the women's page.
    "11,MW_M,2026-03-31,Four Nations,Botswana,home,TRUE,Obed Stadium,"
    "Francistown,Botswana,0,1,played,,FALSE,FALSE,\n"
)

M36_GOALS = GOAL_HEADER + (
    "65,36,MW_W,Temwa Chawinga,MW_W_013,73,,2h,,ref\n"
    "66,36,MW_W,Temwa Chawinga,MW_W_013,90,5,2h,,ref\n"
    "67,36,MW_W,Tabitha Chawinga,MW_W_001,79,,2h,,ref\n"
    "68,36,NIGERIA_W,Rasheedat Ajibade,W_INT_NI_001,90,2,2h,penalty,ref\n"
    "69,36,NIGERIA_W,Uchenna Kanu,W_INT_NI_002,90,9,2h,,ref\n"
    "12,11,BOTSWANA_M,Kamogelo Moloi,INT_BOT,66,,2h,,ref\n"
)

M36_LINEUPS = LINEUP_HEADER + (
    "36,MW_W,Mercy Sikelo,MW_W_014,1,GK,starting,FALSE,,,,FALSE,FALSE,FALSE\n"
    "36,MW_W,Chimwemwe Madise,MW_W_017,4,DF,starting,FALSE,,,,TRUE,FALSE,FALSE\n"
    "36,MW_W,Ireen Khumalo,MW_W_008,18,DF,starting,FALSE,,,,FALSE,FALSE,FALSE\n"
    "36,MW_W,Madyina Nguluwe,MW_W_018,12,MF,starting,FALSE,,50,,TRUE,FALSE,FALSE\n"
    "36,MW_W,Rose Kadzere,MW_W_002,6,FW,starting,FALSE,,90,,FALSE,FALSE,FALSE\n"
    "36,MW_W,Tabitha Chawinga,MW_W_001,11,FW,starting,TRUE,,,,FALSE,FALSE,FALSE\n"
    "36,MW_W,Temwa Chawinga,MW_W_013,10,FW,starting,FALSE,,,,FALSE,FALSE,FALSE\n"
    "36,MW_W,Sabinah Thom,MW_W_006,9,FW,sub_on,FALSE,50,,Madyina Nguluwe,"
    "FALSE,FALSE,FALSE\n"
    "36,MW_W,Vanessa Chikupila,MW_W_010,13,MF,sub_on,FALSE,90,,Rose Kadzere,"
    "FALSE,FALSE,FALSE\n"
    "36,MW_W,Esther Maulidi,MW_W_021,23,GK,unused_sub,FALSE,,,,FALSE,FALSE,FALSE\n"
    # Another team's line-up row for one of our matches: filtered out.
    "36,NIGERIA_W,Chiamaka Nnadozie,W_INT_NI_003,1,GK,starting,FALSE,,,,"
    "FALSE,FALSE,FALSE\n"
)

M36_SQUADS = SQUAD_HEADER + (
    "1,2026_WAFCON,36-38,MW_W,2026-07-11,2026 Women's Africa Cup of Nations,"
    "Tabitha Chawinga,MW_W_001,FW,11,Lyon,,France,captain,Lovemore Fazili\n"
    "1,2026_WAFCON,36-38,MW_W,2026-07-11,2026 Women's Africa Cup of Nations,"
    "Mercy Sikelo,MW_W_014,GK,1,Civil Service United,MW_CSUW_W1,Malawi,,"
    "Lovemore Fazili\n"
    # An older squad: must not be the "current" one.
    "0,2025_COSAFA,28-30,MW_W,2026-02-01,2025 COSAFA Championship,"
    "Someone Else,MW_W_099,MF,7,Old Club,,Malawi,,Old Coach\n"
    # Another team's squad row.
    "2,2026_FNT,,MW_M,2026-10-11,Four Nations,George Chikooka,,GK,1,"
    "Silver Strikers,,Malawi,,\n"
)

# The whole of Group C: our row plus one per rival. Rival codes resolve to
# nothing, which is why each carries a team_name.
M36_COMPS = COMP_HEADER + (
    "ZAMBIA_W,2026 Women's Africa Cup of Nations,Group C,1,1,1,0,0,3,"
    "2026-07-30,,Zambia,6,0\n"
    "MW_W,2026 Women's Africa Cup of Nations,Group C,2,1,1,0,0,3,2026-07-30,"
    "https://en.wikipedia.org/wiki/2026,,3,2\n"
    "NIGERIA_W,2026 Women's Africa Cup of Nations,Group C,3,1,0,0,1,0,"
    "2026-07-30,,Nigeria,2,3\n"
    "EGYPT_W,2026 Women's Africa Cup of Nations,Group C,4,1,0,0,1,0,"
    "2026-07-30,,Egypt,0,6\n"
    "MW_M,2026 Four Nations Tournament,,,,,,,,,,,,\n"
)


KO_HEADER = (
    "tie_id,competition_name,stage,slot,home_name,away_name,home_score,"
    "away_score,home_pens,away_pens,extra_time,date,kickoff,venue,city,"
    "status,nt_match_id\n"
)

# The competition M36_COMPS names — a bracket only reaches a page by matching it.
WAFCON = "2026 Women's Africa Cup of Nations"


def texts(teams=TEAMS, matches=M36_MATCHES, goals=M36_GOALS,
          squads=M36_SQUADS, comps=M36_COMPS, lineups=M36_LINEUPS,
          knockout=KO_HEADER):
    return {
        "nt_teams": teams, "nt_matches": matches, "nt_goals": goals,
        "nt_squads": squads, "nt_competitions": comps, "nt_lineups": lineups,
        "nt_knockout": knockout,
    }


def load(**kwargs):
    return nt.load_team(texts(**kwargs))


def parse_match(row):
    return next(iter(nt.parse_nt_matches(MATCH_HEADER + row).values()))


def parse_goal(row):
    return next(iter(nt.parse_nt_goals(GOAL_HEADER + row).values()))


class DateParsingTest(unittest.TestCase):
    def test_tbd_date_parses_to_blank(self):
        m = parse_match("14,MW_M,tbd,AFCON,Angola,home,FALSE,tbd,tbd,Malawi,"
                        ",,scheduled,,FALSE,FALSE,")
        self.assertEqual(m.date, "")

    def test_tbd_is_case_insensitive_and_tba_accepted(self):
        for value in ("TBD", "Tbd", "tba"):
            m = parse_match(f"14,MW_M,{value},AFCON,Angola,home,FALSE,,,Malawi,"
                            f",,scheduled,,FALSE,FALSE,")
            self.assertEqual(m.date, "", value)

    def test_real_date_kept(self):
        self.assertEqual(load().results[0].match.date, "2026-07-28")

    def test_malformed_date_still_fails_the_build(self):
        with self.assertRaises(DataError):
            parse_match("14,MW_W,28/07/2026,WAFCON,Nigeria,away,FALSE,,,,3,2,"
                        "played,,FALSE,FALSE,")

    def test_kickoff_parses_and_labels_in_malawi_time(self):
        m = parse_match("14,MW_W,2026-08-05,WAFCON,Zambia,away,TRUE,,,,,,"
                        "scheduled,,FALSE,FALSE,,20:00")
        self.assertEqual(m.kickoff, "20:00")
        self.assertEqual(m.kickoff_label, "20:00 CAT")

    def test_kickoff_with_seconds_normalises_to_hh_mm(self):
        m = parse_match("14,MW_W,2026-08-05,WAFCON,Zambia,away,TRUE,,,,,,"
                        "scheduled,,FALSE,FALSE,,20:00:00")
        self.assertEqual(m.kickoff, "20:00")

    def test_missing_or_tbd_kickoff_leaves_the_label_blank(self):
        for value in ("", "tbd", "TBA"):
            m = parse_match(f"14,MW_W,2026-08-05,WAFCON,Zambia,away,TRUE,,,,,,"
                            f"scheduled,,FALSE,FALSE,,{value}")
            self.assertEqual(m.kickoff_label, "", repr(value))

    def test_malformed_kickoff_fails_the_build(self):
        for value in ("8pm", "20.00", "25:00"):
            with self.assertRaises(DataError, msg=value):
                parse_match(f"14,MW_W,2026-08-05,WAFCON,Zambia,away,TRUE,,,,,,"
                            f"scheduled,,FALSE,FALSE,,{value}")

    def test_tbd_venue_dropped_from_the_venue_label(self):
        m = parse_match("14,MW_M,tbd,AFCON,Angola,home,FALSE,tbd,tbd,Malawi,"
                        ",,scheduled,,FALSE,FALSE,")
        self.assertEqual(m.venue_label, "")


class FlagAndEnumTest(unittest.TestCase):
    def test_checkbox_export_accepted(self):
        self.assertTrue(parse_match(
            "1,MW_W,2026-07-28,C,X,away,TRUE,,,,1,0,played,,FALSE,FALSE,").neutral)
        self.assertFalse(parse_match(
            "1,MW_W,2026-07-28,C,X,away,FALSE,,,,1,0,played,,FALSE,FALSE,").neutral)

    def test_bad_flag_rejected(self):
        with self.assertRaises(DataError):
            parse_match("1,MW_W,2026-07-28,C,X,away,yes,,,,1,0,played,,"
                        "FALSE,FALSE,")

    def test_unknown_status_rejected(self):
        with self.assertRaises(DataError):
            parse_match("1,MW_W,2026-07-28,C,X,away,FALSE,,,,1,0,postponed,,"
                        "FALSE,FALSE,")

    def test_negative_score_rejected(self):
        with self.assertRaises(DataError):
            parse_match("1,MW_W,2026-07-28,C,X,away,FALSE,,,,-1,0,played,,"
                        "FALSE,FALSE,")

    def test_position_is_case_insensitive_but_normalises_upper(self):
        rows = nt.parse_nt_lineups(
            LINEUP_HEADER
            + "36,MW_W,A Player,P1,1,gk,starting,FALSE,,,,FALSE,FALSE,FALSE\n")
        self.assertEqual(rows[0].position, "GK")

    def test_ground_label_prefers_neutral(self):
        away = parse_match("1,MW_W,2026-07-28,C,X,away,FALSE,,,,1,0,played,,"
                           "FALSE,FALSE,")
        neutral = parse_match("1,MW_W,2026-07-28,C,X,away,TRUE,,,,1,0,played,,"
                              "FALSE,FALSE,")
        self.assertEqual(away.ground_label, "Away")
        self.assertEqual(neutral.ground_label, "Neutral")


class GoalAnnotationTest(unittest.TestCase):
    def test_plain_minute(self):
        g = parse_goal("1,36,MW_W,Temwa Chawinga,P,73,,2h,,ref")
        self.assertEqual(g.annotation, "Temwa Chawinga 73'")

    def test_stoppage_time(self):
        g = parse_goal("1,36,MW_W,Temwa Chawinga,P,90,5,2h,,ref")
        self.assertEqual(g.annotation, "Temwa Chawinga 90+5'")

    def test_penalty_suffix(self):
        g = parse_goal("1,36,NIGERIA_W,Rasheedat Ajibade,P,90,2,2h,penalty,ref")
        self.assertEqual(g.annotation, "Rasheedat Ajibade 90+2' (P)")

    def test_own_goal_suffix(self):
        g = parse_goal("1,20,MOROCCO_W,Bernadette Mkandawire,P,40,,1h,own goal,ref")
        self.assertEqual(g.annotation, "Bernadette Mkandawire 40' (OG)")

    def test_underscored_own_goal_spelling_also_accepted(self):
        g = parse_goal("1,20,MOROCCO_W,B Mkandawire,P,40,,1h,own_goal,ref")
        self.assertTrue(g.is_own_goal)
        self.assertTrue(g.annotation.endswith("(OG)"))

    def test_missing_minute_leaves_a_bare_name(self):
        g = parse_goal("1,36,MW_W,Temwa Chawinga,P,,,2h,,ref")
        self.assertEqual(g.annotation, "Temwa Chawinga")

    def test_stoppage_sorts_between_its_base_and_the_next_minute(self):
        order = [
            parse_goal("1,36,MW_W,A,P,45,,1h,,ref"),
            parse_goal("2,36,MW_W,B,P,45,1,1h,,ref"),
            parse_goal("3,36,MW_W,C,P,46,,2h,,ref"),
        ]
        self.assertEqual([g.player_name for g in sorted(order, key=lambda g: g.minute_sort)],
                         ["A", "B", "C"])


class FilteringTest(unittest.TestCase):
    """Nothing belonging to another national team may reach the page."""

    def setUp(self):
        self.td = load()

    def test_only_our_matches(self):
        ids = ([r.match.match_id for r in self.td.results]
               + [m.match_id for m in self.td.fixtures])
        self.assertEqual(sorted(ids), ["36", "37"])

    def test_opponent_goals_are_kept_for_our_matches(self):
        r = self.td.results[0]
        self.assertEqual([g.player_name for g in r.their_goals],
                         ["Rasheedat Ajibade", "Uchenna Kanu"])

    def test_other_teams_goals_dropped(self):
        names = [g.player_name for r in self.td.results
                 for g in r.our_goals + r.their_goals]
        self.assertNotIn("Kamogelo Moloi", names)

    def test_opponent_lineup_rows_dropped(self):
        lineup = self.td.results[0].lineup
        names = [r.player_name for r in
                 lineup.starting + lineup.unused + [s.on for s in lineup.substitutions]]
        self.assertNotIn("Chiamaka Nnadozie", names)

    def test_other_teams_squad_rows_dropped(self):
        self.assertNotIn("George Chikooka",
                         [p.player_name for p in self.td.squad.players])

    def test_other_teams_group_rows_dropped(self):
        self.assertEqual([g.competition_name for g in self.td.groups],
                         ["2026 Women's Africa Cup of Nations"])

    def test_unknown_team_code_is_an_error(self):
        with self.assertRaises(DataError):
            nt.team_data(nt.parse_all(texts()), "MW_U20W")


class ResultsAndFixturesTest(unittest.TestCase):
    def test_results_are_reverse_chronological(self):
        matches = MATCH_HEADER + (
            "1,MW_W,2025-06-19,Friendly,Morocco,away,FALSE,,,,2,4,played,,"
            "FALSE,FALSE,\n"
            "2,MW_W,2026-07-28,WAFCON,Nigeria,away,TRUE,,,,3,2,played,,"
            "FALSE,FALSE,\n"
        )
        td = load(matches=matches, goals=GOAL_HEADER, lineups=LINEUP_HEADER)
        self.assertEqual([r.match.match_id for r in td.results], ["2", "1"])

    def test_fixtures_are_chronological_with_undated_last(self):
        matches = MATCH_HEADER + (
            "1,MW_W,tbd,AFCON,Kenya,home,FALSE,,,,,,scheduled,,FALSE,FALSE,\n"
            "2,MW_W,2026-08-05,WAFCON,Zambia,away,TRUE,,,,,,scheduled,,"
            "FALSE,FALSE,\n"
            "3,MW_W,2026-08-01,WAFCON,Egypt,away,TRUE,,,,,,scheduled,,"
            "FALSE,FALSE,\n"
        )
        td = load(matches=matches, goals=GOAL_HEADER, lineups=LINEUP_HEADER)
        self.assertEqual([m.match_id for m in td.fixtures], ["3", "2", "1"])
        self.assertEqual(td.next_match.match_id, "3")

    def test_no_next_match_when_nothing_is_scheduled(self):
        matches = MATCH_HEADER + (
            "1,MW_W,2025-06-19,Friendly,Morocco,away,FALSE,,,,2,4,played,,"
            "FALSE,FALSE,\n")
        td = load(matches=matches, goals=GOAL_HEADER, lineups=LINEUP_HEADER)
        self.assertIsNone(td.next_match)

    def test_awarded_match_counts_as_a_result(self):
        matches = MATCH_HEADER + (
            "1,MW_W,2025-10-09,WCQ,Equatorial Guinea,home,FALSE,,,,3,0,"
            "awarded,,FALSE,FALSE,\n")
        td = load(matches=matches, goals=GOAL_HEADER, lineups=LINEUP_HEADER)
        self.assertEqual(len(td.results), 1)

    def test_coach_comes_from_the_most_recent_match_that_names_one(self):
        self.assertEqual(load().coach, "Lovemore Fazili")

    def test_coach_falls_back_to_the_squad(self):
        matches = MATCH_HEADER + (
            "36,MW_W,2026-07-28,WAFCON,Nigeria,away,TRUE,,,,3,2,played,,"
            "FALSE,FALSE,\n")
        td = load(matches=matches, goals=GOAL_HEADER, lineups=LINEUP_HEADER)
        self.assertEqual(td.coach, "Lovemore Fazili")

    def test_shootout_note(self):
        m = parse_match("10,MW_M,2026-03-28,Four Nations,Zambia,away,TRUE,,,,"
                        "0,0,played,,TRUE,TRUE,loss")
        self.assertEqual(m.score_note, "(AET, lost on pens)")


class SquadTest(unittest.TestCase):
    def test_most_recent_announcement_wins(self):
        td = load()
        self.assertEqual(td.squad.announcement_date, "2026-07-11")
        self.assertNotIn("Someone Else",
                         [p.player_name for p in td.squad.players])

    def test_captain_from_notes(self):
        td = load()
        captains = [p.player_name for p in td.squad.players if p.is_captain]
        self.assertEqual(captains, ["Tabitha Chawinga"])

    def test_vice_captain_is_not_the_captain(self):
        squads = SQUAD_HEADER + (
            "1,C,,MW_W,2026-07-11,WAFCON,A Player,P1,MF,5,Club,,Malawi,"
            "vice-captain,Coach\n")
        td = load(squads=squads)
        player = td.squad.players[0]
        self.assertTrue(player.is_vice_captain)
        self.assertFalse(player.is_captain)

    def test_grouped_by_position_in_gk_df_mf_fw_order(self):
        labels = [label for label, _rows in load().squad.by_position()]
        self.assertEqual(labels, ["Goalkeepers", "Forwards"])

    def test_players_sort_by_shirt_number_numerically(self):
        squads = SQUAD_HEADER + "".join(
            f"1,C,,MW_W,2026-07-11,WAFCON,Player {n},P{n},MF,{n},Club,,"
            f"Malawi,,Coach\n" for n in (10, 2, 9))
        rows = load(squads=squads).squad.by_position()[0][1]
        self.assertEqual([r.shirt_number for r in rows], ["2", "9", "10"])

    def test_blank_domestic_team_id_is_not_an_error(self):
        # Foreign-based players have no domestic team; the club still shows.
        player = next(p for p in load().squad.players
                      if p.player_name == "Tabitha Chawinga")
        self.assertEqual(player.domestic_team_id, "")
        self.assertEqual(player.club, "Lyon")

    def test_undated_squad_falls_back_to_the_highest_squad_id(self):
        squads = SQUAD_HEADER + (
            "1,C,,MW_W,,WAFCON,Older,P1,MF,5,Club,,Malawi,,Coach\n"
            "2,C,,MW_W,,WAFCON,Newer,P2,MF,6,Club,,Malawi,,Coach\n")
        self.assertEqual([p.player_name for p in load(squads=squads).squad.players],
                         ["Newer"])


class LineupTest(unittest.TestCase):
    def setUp(self):
        self.lineup = load().results[0].lineup

    def test_roles_split(self):
        self.assertEqual(len(self.lineup.starting), 7)  # trimmed fixture
        self.assertEqual(len(self.lineup.substitutions), 2)
        self.assertEqual([r.player_name for r in self.lineup.unused],
                         ["Esther Maulidi"])

    def test_substitutions_pair_by_name_and_carry_the_minute(self):
        self.assertEqual(
            [(s.on.player_name, s.off_name, s.minute) for s in self.lineup.substitutions],
            [("Sabinah Thom", "Madyina Nguluwe", "50"),
             ("Vanessa Chikupila", "Rose Kadzere", "90")])

    def test_captain_flag(self):
        captains = [r.player_name for r in self.lineup.starting if r.captain]
        self.assertEqual(captains, ["Tabitha Chawinga"])

    def test_cards(self):
        booked = sorted(r.player_name for r in self.lineup.starting if r.yellow_card)
        self.assertEqual(booked, ["Chimwemwe Madise", "Madyina Nguluwe"])

    def test_unmatched_replaced_player_still_renders_the_substitution(self):
        lineups = LINEUP_HEADER + (
            "36,MW_W,Sabinah Thom,P1,9,FW,sub_on,FALSE,50,,Someone Not Listed,"
            "FALSE,FALSE,FALSE\n")
        sub = load(lineups=lineups).results[0].lineup.substitutions[0]
        self.assertIsNone(sub.off)
        self.assertEqual(sub.off_name, "Someone Not Listed")

    def test_matches_without_lineup_rows_have_none(self):
        td = load(lineups=LINEUP_HEADER)
        self.assertIsNone(td.results[0].lineup)


class ValidatorTest(unittest.TestCase):
    def errors(self, **kwargs):
        t = texts(**kwargs)
        return validate.check_nt(nt.parse_all(t))

    def test_clean_fixture_passes(self):
        self.assertEqual(self.errors(), [])

    def test_a_rival_group_row_must_carry_its_own_name(self):
        comps = COMP_HEADER + (
            "MW_W,2026 Women's Africa Cup of Nations,Group C,2,1,1,0,0,3,"
            "2026-07-30,,,3,2\n"
            "NIGERIA_W,2026 Women's Africa Cup of Nations,Group C,3,1,0,0,1,0,"
            "2026-07-30,,,2,3\n")
        errs = self.errors(comps=comps)
        self.assertTrue(any("needs a team_name" in e for e in errs), errs)

    def test_a_group_with_no_row_of_ours_is_orphaned(self):
        comps = COMP_HEADER + (
            "NIGERIA_W,2027 Africa Cup of Nations,Group A,1,1,1,0,0,3,"
            "2027-01-01,,Nigeria,2,0\n")
        errs = self.errors(comps=comps)
        self.assertTrue(any("belongs to no page" in e for e in errs), errs)

    def test_goal_rows_may_not_exceed_the_score(self):
        goals = GOAL_HEADER + "".join(
            f"{n},36,MW_W,Player {n},P{n},{n},,2h,,ref\n" for n in range(1, 6))
        errs = self.errors(goals=goals, lineups=LINEUP_HEADER)
        self.assertTrue(any("exceed its score of 3" in e for e in errs), errs)

    def test_opponent_goal_rows_checked_against_the_opponent_score(self):
        goals = GOAL_HEADER + "".join(
            f"{n},36,NIGERIA_W,Player {n},P{n},{n},,2h,,ref\n" for n in range(1, 5))
        errs = self.errors(goals=goals, lineups=LINEUP_HEADER)
        self.assertTrue(any("Nigeria" in e and "exceed" in e for e in errs), errs)

    def test_scheduled_match_with_a_score_fails(self):
        matches = MATCH_HEADER + (
            "36,MW_W,2026-07-28,WAFCON,Nigeria,away,TRUE,,,,3,2,scheduled,,"
            "FALSE,FALSE,\n")
        errs = self.errors(matches=matches, goals=GOAL_HEADER,
                           lineups=LINEUP_HEADER)
        self.assertTrue(any("status=scheduled but a score" in e for e in errs), errs)

    def test_played_match_without_a_score_fails(self):
        matches = MATCH_HEADER + (
            "36,MW_W,2026-07-28,WAFCON,Nigeria,away,TRUE,,,,,,played,,"
            "FALSE,FALSE,\n")
        errs = self.errors(matches=matches, goals=GOAL_HEADER,
                           lineups=LINEUP_HEADER)
        self.assertTrue(any("the score is blank" in e for e in errs), errs)

    def test_unresolvable_ids_fail(self):
        errs = self.errors(
            goals=GOAL_HEADER + "1,999,MW_W,A,P,10,,1h,,ref\n",
            lineups=LINEUP_HEADER
            + "999,MW_W,A,P,1,GK,starting,FALSE,,,,FALSE,FALSE,FALSE\n")
        self.assertEqual(sum("does not resolve" in e for e in errs), 2, errs)

    def test_twelve_starters_fail(self):
        lineups = LINEUP_HEADER + "".join(
            f"36,MW_W,Player {n},P{n},{n},MF,starting,FALSE,,,,"
            f"FALSE,FALSE,FALSE\n" for n in range(1, 13))
        errs = self.errors(lineups=lineups)
        self.assertTrue(any("starting rows" in e for e in errs), errs)

    def test_sub_on_without_a_minute_fails(self):
        lineups = LINEUP_HEADER + (
            "36,MW_W,Sabinah Thom,P1,9,FW,sub_on,FALSE,,,,FALSE,FALSE,FALSE\n")
        errs = self.errors(lineups=lineups)
        self.assertTrue(any("minute_on is blank" in e for e in errs), errs)

    def test_replaced_player_must_be_in_the_lineup(self):
        lineups = LINEUP_HEADER + (
            "36,MW_W,Sabinah Thom,P1,9,FW,sub_on,FALSE,50,,Ghost Player,"
            "FALSE,FALSE,FALSE\n")
        errs = self.errors(lineups=lineups)
        self.assertTrue(any("is not in this match's line-up" in e for e in errs),
                        errs)

    def test_duplicate_primary_keys_caught(self):
        matches = M36_MATCHES + (
            "36,MW_W,2026-07-28,WAFCON,Nigeria,away,TRUE,,,,3,2,played,,"
            "FALSE,FALSE,\n")
        errs = validate.check_primary_keys(texts(matches=matches),
                                          keys=validate.NT_PRIMARY_KEYS)
        self.assertTrue(any("duplicate primary key" in e for e in errs), errs)


class FakeTeam:
    def __init__(self, club_id):
        self.club_id = club_id


class FakeDataset:
    teams = {"MW_CSUW_W1": FakeTeam("MW_CSU")}


def pages(team_data=None, **kwargs):
    """The four rendered pages, keyed by filename — what build_page writes."""
    td = team_data if team_data is not None else load(**kwargs)
    fl = flags.Flags(STATIC, prefix="../")
    flag_url = "../malawi_flag.svg"
    return {
        "index.html": nt_page.render_home(td, fl, flag_url=flag_url),
        "results.html": nt_page.render_results(td, fl, flag_url=flag_url),
        "fixtures.html": nt_page.render_fixtures(td, fl, flag_url=flag_url),
        "squad.html": nt_page.render_squad(td, fl, FakeDataset(),
                                           club_hub_ids={"MW_CSU"},
                                           flag_url=flag_url),
    }


class PageTest(unittest.TestCase):
    """The rendered pages — the match-36 sense check, end to end."""

    def setUp(self):
        self.pages = pages()
        # Assertions about the section a piece of content lives in name the
        # page; the rest just ask whether the section rendered at all.
        self.html = "\n".join(self.pages.values())

    def test_header_shows_the_team_and_coach(self):
        self.assertIn("MALAWI SCORCHERS", self.html)
        self.assertIn("Lovemore Fazili", self.html)

    def test_next_match(self):
        self.assertIn("Next Match", self.html)
        self.assertIn("Egypt", self.html)
        self.assertIn("1 Aug 2026", self.html)
        self.assertIn("20:00 CAT", self.html)

    def test_landing_card_shows_the_kickoff_beside_the_date(self):
        card = nt_page.landing_next_match(load(), flags.Flags(STATIC))
        self.assertIn("1 Aug 2026 &middot; 20:00 CAT &middot; Neutral", card)

    def test_landing_card_omits_the_kickoff_when_it_is_unknown(self):
        matches = MATCH_HEADER + (
            "37,MW_W,2026-08-01,WAFCON,Egypt,away,TRUE,,,,,,scheduled,,"
            "FALSE,FALSE,,\n")
        card = nt_page.landing_next_match(
            load(matches=matches, goals=GOAL_HEADER, lineups=LINEUP_HEADER),
            flags.Flags(STATIC))
        self.assertIn("1 Aug 2026 &middot; Neutral", card)
        self.assertNotIn("CAT", card)

    def test_group_table(self):
        self.assertIn("2026 Women&#x27;s Africa Cup of Nations · Group C",
                      self.html)
        self.assertIn("As of 30 Jul 2026", self.html)
        self.assertIn("Full group table on Wikipedia", self.html)

    def test_group_table_lists_every_team_in_table_order(self):
        home = self.pages["index.html"]
        table = home[home.index("nt-table"):home.index("</table>")]
        order = [n for n in ("Zambia", "Malawi", "Nigeria", "Egypt")
                 if n in table]
        self.assertEqual(order, ["Zambia", "Malawi", "Nigeria", "Egypt"])
        self.assertLess(table.index("Zambia"), table.index("Malawi"))
        self.assertLess(table.index("Nigeria"), table.index("Egypt"))

    def test_group_table_highlights_our_row_and_only_ours(self):
        home = self.pages["index.html"]
        us = home.index('<tr class="nt-row-us">')
        row = home[us:home.index("</tr>", us)]
        self.assertIn("Malawi", row)
        self.assertEqual(home.count('<tr class="nt-row-us">'), 1)

    def test_group_table_shows_goals_and_difference(self):
        home = self.pages["index.html"]
        self.assertIn("<th>GOALS</th><th>DIFF</th>", home)
        self.assertIn(">6:0</td><td>+6</td>", home)
        self.assertIn(">0:6</td><td>-6</td>", home)

    def test_group_table_without_goal_columns_omits_them(self):
        comps = COMP_HEADER + (
            "MW_W,2026 Women's Africa Cup of Nations,Group C,2,1,1,0,0,3,"
            "2026-07-30,,,,\n")
        home = pages(comps=comps)["index.html"]
        self.assertIn("nt-table", home)
        self.assertNotIn("<th>GOALS</th>", home)

    def test_scoreline_is_malawi_first(self):
        self.assertIn('>Malawi<img class="nt-flag nt-flag-post" '
                      'src="../flags/mw.png"', self.html)
        self.assertIn('<td class="v2-res-score">3:2</td>', self.html)

    def test_our_scorers(self):
        for line in ("Temwa Chawinga 73&#x27;", "Temwa Chawinga 90+5&#x27;",
                     "Tabitha Chawinga 79&#x27;"):
            self.assertIn(line, self.html)

    def test_opponent_scorers(self):
        for line in ("Rasheedat Ajibade 90+2&#x27; (P)",
                     "Uchenna Kanu 90+9&#x27;"):
            self.assertIn(line, self.html)

    def test_scorers_are_split_left_and_right(self):
        ours = self.html.index("Temwa Chawinga 73")
        theirs = self.html.index("Rasheedat Ajibade")
        away_col = self.html.index('class="v2-ms-away"')
        self.assertLess(ours, away_col)
        self.assertLess(away_col, theirs)

    def test_lineup_substitutions(self):
        self.assertIn("Sabinah Thom", self.html)
        self.assertIn("Madyina Nguluwe", self.html)
        self.assertIn("Vanessa Chikupila", self.html)
        self.assertIn("Rose Kadzere", self.html)

    def test_lineup_cards_and_captain(self):
        self.assertIn("el-card-y", self.html)
        self.assertIn('title="Captain"', self.html)

    def test_unused_subs_listed_separately(self):
        self.assertIn("Unused substitutes", self.html)
        self.assertIn("Esther Maulidi (23)", self.html)

    def test_squad_club_links_to_the_hub_only_when_domestic(self):
        self.assertIn('href="../clubs/MW_CSU.html"', self.html)
        # A foreign club shows as text with its country, never as a dead link.
        self.assertIn("Lyon", self.html)
        self.assertNotIn('href="../clubs/.html"', self.html)

    def test_date_tbc_for_an_undated_fixture(self):
        matches = MATCH_HEADER + (
            "1,MW_W,tbd,AFCON Qualification,Kenya,home,FALSE,tbd,tbd,Malawi,"
            ",,scheduled,,FALSE,FALSE,\n")
        html = pages(matches=matches, goals=GOAL_HEADER,
                     lineups=LINEUP_HEADER)["fixtures.html"]
        self.assertIn("Date TBC", html)

    def test_empty_states(self):
        empty = pages(matches=MATCH_HEADER, goals=GOAL_HEADER,
                      lineups=LINEUP_HEADER, squads=SQUAD_HEADER,
                      comps=COMP_HEADER)
        self.assertIn("No results yet.", empty["results.html"])
        self.assertIn("No upcoming fixtures.", empty["fixtures.html"])
        self.assertIn("No squad announced yet.", empty["squad.html"])
        # With no matches at all there is no current competition either, so the
        # home page falls back to the same empty states rather than blank space.
        self.assertIn("No results yet.", empty["index.html"])

    def test_opponent_flags_render_and_unknown_countries_do_not(self):
        results = self.pages["results.html"]
        self.assertIn('src="../flags/ng.png"', results)
        matches = MATCH_HEADER + (
            "1,MW_W,2026-05-01,Friendly,Wakanda,home,FALSE,,,,1,0,played,,"
            "FALSE,FALSE,\n")
        html = pages(matches=matches, goals=GOAL_HEADER,
                     lineups=LINEUP_HEADER)["results.html"]
        self.assertIn("Wakanda", html)
        self.assertNotIn("nt-flag-pre", html)

    def test_landing_meta_names_the_current_competition(self):
        self.assertEqual(
            nt_page.landing_meta(load()),
            "National Team &middot; 2026 Women&#x27;s Africa Cup of Nations")


# The home page's whole job is to be about the tournament that is on, so it gets
# its own fixture: a tournament match, a friendly either side of it, and a
# tournament fixture still to play.
MIXED_MATCHES = MATCH_HEADER + (
    "35,MW_W,2026-07-16,Friendly,Morocco,away,FALSE,Al Medina Stadium,Rabat,"
    "Morocco,1,2,played,,FALSE,FALSE,\n"
    "36,MW_W,2026-07-28,WAFCON,Nigeria,away,TRUE,Al Medina Stadium,Rabat,"
    "Morocco,3,2,played,Lovemore Fazili,FALSE,FALSE,\n"
    "37,MW_W,2026-08-01,WAFCON,Egypt,away,TRUE,Moulay El Hassan Stadium,"
    "Rabat,Morocco,,,scheduled,,FALSE,FALSE,\n"
    "40,MW_W,2026-09-10,Friendly,Kenya,home,FALSE,Bingu Stadium,Lilongwe,"
    "Malawi,,,scheduled,,FALSE,FALSE,\n"
)


class HomePageTest(unittest.TestCase):
    """index.html shows the current tournament; the tabs show everything."""

    def setUp(self):
        self.pages = pages(matches=MIXED_MATCHES, goals=GOAL_HEADER,
                           lineups=LINEUP_HEADER)

    def test_current_competition_comes_from_the_next_match(self):
        self.assertEqual(
            load(matches=MIXED_MATCHES).current_competition, "WAFCON")

    def test_home_keeps_the_tournament_and_drops_the_friendlies(self):
        home = self.pages["index.html"]
        self.assertIn("WAFCON Results", home)
        self.assertIn("WAFCON Fixtures", home)
        self.assertIn("Nigeria", home)   # the tournament result
        self.assertIn("Egypt", home)     # the tournament fixture
        self.assertNotIn("Morocco", home)  # a friendly already played
        self.assertNotIn("Kenya", home)    # a friendly still to play

    def test_the_tabs_still_carry_everything(self):
        self.assertIn("Morocco", self.pages["results.html"])
        self.assertIn("Kenya", self.pages["fixtures.html"])

    def test_home_links_on_to_the_full_lists(self):
        home = self.pages["index.html"]
        self.assertIn('href="results.html"', home)
        self.assertIn('href="fixtures.html"', home)

    def test_friendlies_alone_are_not_a_tournament(self):
        matches = MATCH_HEADER + (
            "1,MW_W,2026-06-03,Friendly,Tanzania,away,FALSE,,,,0,1,played,,"
            "FALSE,FALSE,\n"
            "2,MW_W,2026-09-10,Friendly,Kenya,home,FALSE,,,,,,scheduled,,"
            "FALSE,FALSE,\n")
        td = load(matches=matches, goals=GOAL_HEADER, lineups=LINEUP_HEADER)
        self.assertEqual(td.current_competition, "")
        home = pages(td)["index.html"]
        self.assertIn("Recent Results", home)
        self.assertIn("Tanzania", home)

    def test_home_falls_back_to_a_capped_list_without_a_tournament(self):
        rows = "".join(
            f"{i},MW_W,2026-0{i}-01,Friendly,Kenya,home,FALSE,,,,1,0,played,,"
            "FALSE,FALSE,\n" for i in range(1, 8))
        td = load(matches=MATCH_HEADER + rows, goals=GOAL_HEADER,
                  lineups=LINEUP_HEADER)
        self.assertEqual(len(td.results), 7)
        home = pages(td)["index.html"]
        self.assertEqual(home.count("v2-res-row-compact"),
                         nt_page.HOME_FALLBACK_LIMIT)


class FlagsTest(unittest.TestCase):
    def test_known_country(self):
        self.assertEqual(flags.code_for("Nigeria"), "ng")

    def test_case_accents_and_punctuation_are_ignored(self):
        self.assertEqual(flags.code_for("CÔTE D'IVOIRE"), "ci")
        self.assertEqual(flags.code_for("Sao Tome and Principe"), "st")

    def test_alternative_names(self):
        self.assertEqual(flags.code_for("Ivory Coast"), "ci")
        self.assertEqual(flags.code_for("Swaziland"), "sz")
        self.assertEqual(flags.code_for("Cape Verde"), flags.code_for("Cabo Verde"))

    def test_a_trailing_qualifier_still_finds_the_country(self):
        self.assertEqual(flags.code_for("Nigeria U20"), "ng")

    def test_unknown_country_has_no_code_and_no_image(self):
        self.assertEqual(flags.code_for("Wakanda"), "")
        self.assertEqual(flags.Flags(STATIC).img_for("Wakanda"), "")

    def test_every_mapped_code_has_a_file(self):
        available = flags.Flags(STATIC).available
        self.assertEqual(sorted(set(flags.CODES) - available), [])

    def test_a_missing_flags_directory_degrades_to_no_flags(self):
        fl = flags.Flags(os.path.join(STATIC, "does-not-exist"))
        self.assertEqual(fl.img_for("Nigeria"), "")


# ── Knockout bracket ─────────────────────────────────────────────────────────

def ko(*rows):
    return KO_HEADER + "".join(rows)


def parse_tie(row):
    return nt.parse_nt_knockout(ko(row))[0]


# The seeded shape a bracket starts as: every slot present, no names yet.
SEEDED = ko(
    f"QF1,{WAFCON},qf,1,,,,,,,,,,,,,\n",
    f"QF2,{WAFCON},qf,2,,,,,,,,,,,,,\n",
    f"SF1,{WAFCON},sf,1,,,,,,,,,,,,,\n",
    f"F,{WAFCON},final,1,,,,,,,,,,,,,\n",
    f"TP,{WAFCON},3p,1,,,,,,,,,,,,,\n",
)


class KnockoutParsingTest(unittest.TestCase):
    def test_a_seeded_row_needs_only_its_place_in_the_tree(self):
        tie = parse_tie(f"QF1,{WAFCON},qf,1,,,,,,,,,,,,,\n")
        self.assertEqual((tie.tie_id, tie.stage, tie.slot), ("QF1", "qf", 1))
        self.assertEqual((tie.home_name, tie.away_name), ("", ""))
        self.assertTrue(tie.scheduled)
        self.assertFalse(tie.has_score)

    def test_stage_is_case_insensitive_and_normalises_down(self):
        self.assertEqual(parse_tie(f"QF1,{WAFCON},QF,1,,,,,,,,,,,,,\n").stage, "qf")

    def test_stage_outside_the_cup_vocabulary_is_rejected(self):
        with self.assertRaises(DataError):
            parse_tie(f"QF1,{WAFCON},qtr,1,,,,,,,,,,,,,\n")

    def test_unknown_status_rejected(self):
        with self.assertRaises(DataError):
            parse_tie(f"QF1,{WAFCON},qf,1,Malawi,Ghana,,,,,,,,,,postponed,\n")

    def test_duplicate_tie_id_is_a_duplicate_primary_key(self):
        with self.assertRaises(DataError):
            nt.parse_nt_knockout(ko(f"QF1,{WAFCON},qf,1,,,,,,,,,,,,,\n",
                                    f"QF1,{WAFCON},qf,2,,,,,,,,,,,,,\n"))


class KnockoutWinnerTest(unittest.TestCase):
    def tie(self, tail):
        return parse_tie(f"QF1,{WAFCON},qf,1,Malawi,Ghana,{tail}\n")

    def test_home_win(self):
        self.assertEqual(self.tie("2,1,,,,,,,,played,").winner_name, "Malawi")

    def test_away_win(self):
        self.assertEqual(self.tie("0,3,,,,,,,,played,").winner_name, "Ghana")

    def test_a_level_score_with_no_shootout_has_no_winner(self):
        self.assertEqual(self.tie("1,1,,,,,,,,played,").winner_name, "")

    def test_shootout_decides_a_level_tie(self):
        t = self.tie("1,1,4,3,,,,,,played,")
        self.assertEqual(t.winner_name, "Malawi")
        self.assertEqual(t.score_note, "(4–3 pens)")

    def test_an_unplayed_tie_has_no_winner(self):
        self.assertEqual(self.tie(",,,,,,,,,scheduled,").winner_name, "")

    def test_extra_time_notes_without_a_shootout(self):
        self.assertEqual(self.tie("2,1,,,TRUE,,,,,played,").score_note, "(AET)")


class KnockoutLinkTest(unittest.TestCase):
    """`nt_match_id`: our own tie is in both tabs, and nt_matches wins."""

    def bracket(self, knockout):
        return load(knockout=knockout).brackets[0]

    def test_the_match_row_supplies_the_result(self):
        # Match 36 is Malawi 3-2 Nigeria, away, at Al Medina Stadium.
        tie = self.bracket(ko(f"QF1,{WAFCON},qf,1,Malawi,Nigeria,,,,,,,,,,,36\n")).ties[0]
        self.assertEqual((tie.home_score, tie.away_score), (3, 2))
        self.assertEqual(tie.date, "2026-07-28")
        self.assertEqual(tie.winner_name, "Malawi")
        self.assertTrue(tie.is_ours)

    def test_the_sides_may_read_either_way_round(self):
        """Our side is found via NTMatch.opponent, so order is free."""
        tie = self.bracket(ko(f"QF1,{WAFCON},qf,1,Nigeria,Malawi,,,,,,,,,,,36\n")).ties[0]
        self.assertEqual((tie.home_score, tie.away_score), (2, 3))
        self.assertEqual(tie.winner_name, "Malawi")

    def test_a_dangling_match_id_leaves_the_tie_alone_rather_than_crashing(self):
        tie = self.bracket(ko(f"QF1,{WAFCON},qf,1,Malawi,Ghana,,,,,,,,,,,999\n")).ties[0]
        self.assertFalse(tie.has_score)
        self.assertFalse(tie.is_ours)

    def test_a_tie_with_no_match_id_keeps_its_own_columns(self):
        tie = self.bracket(
            ko(f"QF2,{WAFCON},qf,2,Zambia,Egypt,2,0,,,,2026-08-09,,,,played,\n")
        ).ties[0]
        self.assertEqual((tie.home_score, tie.away_score), (2, 0))
        self.assertEqual(tie.winner_name, "Zambia")


class KnockoutBracketTest(unittest.TestCase):
    def test_ties_are_ordered_by_slot_within_a_round(self):
        """Round order is presentation; the data layer only promises slots."""
        b = load(knockout=ko(
            f"QF2,{WAFCON},qf,2,,,,,,,,,,,,,\n",
            f"QF1,{WAFCON},qf,1,,,,,,,,,,,,,\n",
            f"QF3,{WAFCON},qf,3,,,,,,,,,,,,,\n",
        )).brackets[0]
        self.assertEqual([t.tie_id for t in b.ties if t.stage == "qf"],
                         ["QF1", "QF2", "QF3"])

    def test_a_bracket_matching_no_group_table_is_dropped(self):
        b = load(knockout=ko("X,2030 Some Other Cup,qf,1,,,,,,,,,,,,,\n")).brackets
        self.assertEqual(b, [])

    def test_no_knockout_rows_means_no_bracket(self):
        self.assertEqual(load().brackets, [])


class KnockoutPageTest(unittest.TestCase):
    def home(self, knockout):
        return pages(knockout=knockout)["index.html"]

    def test_rounds_read_earliest_first_and_3p_sits_outside_the_tree(self):
        html = self.home(SEEDED)
        tree = html[html.index("Knockout Stage"):html.index("Third-place")]
        # Match the whole title, since "FINAL" is a substring of the other two.
        titles = re.findall(r'<h3 class="v2-br-title">([^<]+)</h3>', tree)
        self.assertEqual(titles, ["QUARTER-FINALS", "SEMI-FINALS", "FINAL"])
        # The play-off is a card under the tree, never a fourth column.
        self.assertNotIn("THIRD-PLACE", tree.upper())

    def test_an_empty_slot_renders_as_a_dashed_tbd_card(self):
        html = self.home(SEEDED)
        self.assertIn("v2-br-tie v2-br-tie-tbd", html)
        self.assertIn('<span class="v2-br-team">&mdash;</span>', html)

    def test_a_named_side_gets_its_flag_and_the_winner_is_marked(self):
        html = self.home(
            ko(f"QF1,{WAFCON},qf,1,Zambia,Egypt,2,0,,,,2026-08-09,,,,played,\n"))
        self.assertIn("flags/zm.png", html)
        self.assertIn('<div class="v2-br-side v2-br-win"><span class="v2-br-team">'
                      '<img class="nt-flag nt-flag-pre" src="../flags/zm.png"', html)

    def test_an_unmapped_country_renders_no_flag_but_still_its_name(self):
        html = self.home(ko(f"QF1,{WAFCON},qf,1,Wakanda,Ghana,,,,,,,,,,,\n"))
        self.assertIn("Wakanda", html)

    def test_no_bracket_section_when_the_sheet_has_no_ties(self):
        self.assertNotIn("Knockout Stage", self.home(KO_HEADER))


# The feed columns are optional, so they get their own header — the rows above
# exercise a sheet that predates them and must keep parsing.
KO_FEED_HEADER = (
    "tie_id,competition_name,stage,slot,home_name,away_name,home_from,"
    "away_from,home_score,away_score,home_pens,away_pens,extra_time,date,"
    "kickoff,venue,city,status,nt_match_id\n"
)


def kof(*rows):
    return KO_FEED_HEADER + "".join(rows)


# Two quarter-finals feeding a semi, which feeds a final; the play-off takes
# the semi's loser. The shape of a real bracket, small enough to reason about.
TREE = kof(
    f"Q1,{WAFCON},qf,1,Morocco,South Africa,,,2,0,,,,2026-08-08,,,,played,\n",
    f"Q2,{WAFCON},qf,2,Ghana,Mali,,,,,,,,2026-08-08,,,,scheduled,\n",
    f"S1,{WAFCON},sf,1,,,winner:Q1,winner:Q2,,,,,,2026-08-12,,,,scheduled,\n",
    f"TP,{WAFCON},3p,1,,,loser:S1,loser:S1,,,,,,2026-08-15,,,,scheduled,\n",
    f"FI,{WAFCON},final,1,,,winner:S1,winner:S1,,,,,,2026-08-16,,,,scheduled,\n",
)


class KnockoutFeedTest(unittest.TestCase):
    def ties(self, knockout):
        return {t.tie_id: t for t in load(knockout=knockout).brackets[0].ties}

    def test_feed_syntax_parses(self):
        self.assertEqual(nt.parse_feed("winner:Q1"), ("winner", "Q1"))
        self.assertEqual(nt.parse_feed("Loser: Q1 "), ("loser", "Q1"))
        self.assertIsNone(nt.parse_feed(""))

    def test_a_malformed_feed_fails_the_build(self):
        for bad in ("Q1", "winner:", "runnerup:Q1"):
            with self.assertRaises(DataError, msg=bad):
                nt.parse_nt_knockout(
                    kof(f"S1,{WAFCON},sf,1,,,{bad},,,,,,,,,,,,\n"))

    def test_a_decided_tie_promotes_its_winner(self):
        self.assertEqual(self.ties(TREE)["S1"].home_name, "Morocco")

    def test_an_undecided_tie_leaves_the_slot_blank(self):
        self.assertEqual(self.ties(TREE)["S1"].away_name, "")

    def test_promotion_follows_a_chain_of_rounds(self):
        """Winning the semi must reach the final in the same pass."""
        tree = TREE.replace(
            f"S1,{WAFCON},sf,1,,,winner:Q1,winner:Q2,,,,,,2026-08-12,,,,scheduled,\n",
            f"S1,{WAFCON},sf,1,Morocco,Ghana,winner:Q1,winner:Q2,3,1,,,,"
            f"2026-08-12,,,,played,\n")
        self.assertEqual(self.ties(tree)["FI"].home_name, "Morocco")

    def test_the_play_off_is_fed_by_the_loser(self):
        tree = TREE.replace(
            f"S1,{WAFCON},sf,1,,,winner:Q1,winner:Q2,,,,,,2026-08-12,,,,scheduled,\n",
            f"S1,{WAFCON},sf,1,Morocco,Ghana,winner:Q1,winner:Q2,3,1,,,,"
            f"2026-08-12,,,,played,\n")
        self.assertEqual(self.ties(tree)["TP"].home_name, "Ghana")

    def test_a_name_already_in_the_sheet_is_never_overwritten(self):
        tree = TREE.replace(f"S1,{WAFCON},sf,1,,,winner:Q1",
                            f"S1,{WAFCON},sf,1,Egypt,,winner:Q1")
        self.assertEqual(self.ties(tree)["S1"].home_name, "Egypt")

    def test_the_loser_of_a_shootout_is_the_side_that_lost_it(self):
        t = nt.parse_nt_knockout(
            kof(f"Q1,{WAFCON},qf,1,Ghana,Mali,,,1,1,3,5,,2026-08-08,,,,played,\n")
        )[0]
        self.assertEqual((t.winner_name, t.loser_name), ("Mali", "Ghana"))


class KnockoutFeedPageTest(unittest.TestCase):
    def test_an_unfilled_slot_names_the_tie_it_waits_on(self):
        html = pages(knockout=TREE)["index.html"]
        self.assertIn("Winner QF2", html)
        self.assertIn("Winner SF1", html)
        self.assertIn("Loser SF1", html)

    def test_a_promoted_name_replaces_the_label(self):
        html = pages(knockout=TREE)["index.html"]
        self.assertNotIn("Winner QF1", html)   # Morocco won it
        self.assertIn("flags/ma.png", html)

    def test_a_slot_with_no_feed_still_falls_back_to_a_dash(self):
        self.assertIn('<span class="v2-br-team">&mdash;</span>',
                      pages(knockout=SEEDED)["index.html"])


class KnockoutValidatorTest(unittest.TestCase):
    def errors(self, knockout):
        return validate.check_nt(nt.parse_all(texts(knockout=knockout)))

    def assertError(self, knockout, fragment):
        errs = self.errors(knockout)
        self.assertTrue(any(fragment in e for e in errs), errs)

    def test_the_seeded_bracket_passes(self):
        self.assertEqual(self.errors(SEEDED), [])

    def test_a_bracket_for_an_unknown_competition_is_orphaned(self):
        self.assertError(ko("X,2030 Some Other Cup,qf,1,,,,,,,,,,,,,\n"),
                         "belongs to no page")

    def test_two_ties_in_one_slot_would_overlay(self):
        self.assertError(ko(f"A,{WAFCON},qf,1,,,,,,,,,,,,,\n",
                            f"B,{WAFCON},qf,1,,,,,,,,,,,,,\n"),
                         "overlay in the bracket")

    def test_a_one_sided_score_is_rejected(self):
        self.assertError(ko(f"A,{WAFCON},qf,1,Zambia,Egypt,2,,,,,,,,,played,\n"),
                         "only one of home_score/away_score")

    def test_played_without_a_score(self):
        self.assertError(ko(f"A,{WAFCON},qf,1,Zambia,Egypt,,,,,,,,,,played,\n"),
                         "the score is blank")

    def test_scheduled_with_a_score(self):
        self.assertError(
            ko(f"A,{WAFCON},qf,1,Zambia,Egypt,2,0,,,,,,,,scheduled,\n"),
            "status=scheduled but a score is present")

    def test_a_shootout_cannot_end_level(self):
        self.assertError(
            ko(f"A,{WAFCON},qf,1,Zambia,Egypt,1,1,3,3,,,,,,played,\n"),
            "cannot end level")

    def test_pens_on_a_tie_that_was_not_level(self):
        self.assertError(
            ko(f"A,{WAFCON},qf,1,Zambia,Egypt,2,0,4,3,,,,,,played,\n"),
            "is not level")

    def test_a_dangling_nt_match_id(self):
        self.assertError(ko(f"A,{WAFCON},qf,1,Malawi,Ghana,,,,,,,,,,,999\n"),
                         "does not resolve")

    def test_neither_side_names_the_linked_match_opponent(self):
        self.assertError(ko(f"A,{WAFCON},qf,1,Malawi,Ghana,,,,,,,,,,,36\n"),
                         "cannot be matched up to it")

    def test_a_linked_tie_may_not_carry_its_own_result(self):
        self.assertError(ko(f"A,{WAFCON},qf,1,Malawi,Nigeria,3,2,,,,,,,,,36\n"),
                         "owns the result")

    def test_a_linked_tie_may_repeat_fixture_details_that_agree(self):
        """Writing the venue in the bracket too is natural; only clashes fail."""
        self.assertEqual(
            self.errors(ko(f"A,{WAFCON},qf,1,Malawi,Nigeria,,,,,,2026-07-28,,"
                           f"Al Medina Stadium,Rabat,,36\n")),
            [])

    def test_a_linked_tie_may_not_contradict_the_match_row(self):
        self.assertError(
            ko(f"A,{WAFCON},qf,1,Malawi,Nigeria,,,,,,2026-07-29,,,,,36\n"),
            "contradicts nt_matches 36")

    def test_a_feed_naming_a_tie_that_does_not_exist(self):
        self.assertError(kof(f"S1,{WAFCON},sf,1,,,winner:NOPE,,,,,,,,,,,,\n"),
                         "does not exist")

    def test_a_tie_may_not_feed_itself(self):
        self.assertError(kof(f"S1,{WAFCON},sf,1,,,winner:S1,,,,,,,,,,,,\n"),
                         "feeds the tie from itself")

    def test_a_feed_may_not_cross_competitions(self):
        comps = M36_COMPS + (
            "MW_W,2027 Other Cup,Group A,1,1,1,0,0,3,2027-01-01,,,1,0\n")
        errs = validate.check_nt(nt.parse_all(texts(
            comps=comps,
            knockout=kof(f"Q1,2027 Other Cup,qf,1,Ghana,Mali,,,,,,,,,,,,,\n",
                         f"S1,{WAFCON},sf,1,,,winner:Q1,,,,,,,,,,,,\n"))))
        self.assertTrue(any("different competition" in e for e in errs), errs)


if __name__ == "__main__":
    unittest.main()
