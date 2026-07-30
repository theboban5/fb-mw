"""Tests for the national-team section: the nt_* tabs, filtering, and the page.

The end-to-end case is match 36 (Malawi 3-2 Nigeria, WAFCON 2026) — the one
match in the real data that has goals for both sides plus line-up rows, so it
exercises every rule at once.
"""

import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src import nt  # noqa: E402
from src import nt_page  # noqa: E402
from src.dataset import DataError  # noqa: E402
import validate  # noqa: E402

TEAMS = (
    "team_code,team_name,category\n"
    "MW_W,Malawi (Women's),senior\n"
    "MW_M,Malawi (Men's),senior\n"
)

MATCH_HEADER = (
    "match_id,team_code,date,competition,opponent,home_away,neutral,venue,"
    "city,country,team_score,opponent_score,status,coach,extra_time,"
    "penalty_shootout,extra_time_result\n"
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
    "points,last_update,wikipedia_url\n"
)

# ── The match-36 fixture, trimmed to what the page renders ───────────────────

M36_MATCHES = MATCH_HEADER + (
    "36,MW_W,2026-07-28,WAFCON,Nigeria,away,TRUE,Al Medina Stadium,Rabat,"
    "Morocco,3,2,played,Lovemore Fazili,FALSE,FALSE,\n"
    "37,MW_W,2026-08-01,WAFCON,Egypt,away,TRUE,Moulay El Hassan Stadium,"
    "Rabat,Morocco,,,scheduled,,FALSE,FALSE,\n"
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

M36_COMPS = COMP_HEADER + (
    "MW_W,2026 Women's Africa Cup of Nations,Group C,2,1,1,0,0,3,2026-07-30,"
    "https://en.wikipedia.org/wiki/2026\n"
    "MW_M,2026 Four Nations Tournament,,,,,,,,,\n"
)


def texts(teams=TEAMS, matches=M36_MATCHES, goals=M36_GOALS,
          squads=M36_SQUADS, comps=M36_COMPS, lineups=M36_LINEUPS):
    return {
        "nt_teams": teams, "nt_matches": matches, "nt_goals": goals,
        "nt_squads": squads, "nt_competitions": comps, "nt_lineups": lineups,
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


class PageTest(unittest.TestCase):
    """The rendered page — the match-36 sense check, end to end."""

    def setUp(self):
        self.html = nt_page.render_page(
            load(), FakeDataset(), club_hub_ids={"MW_CSU"},
            flag_url="../malawi_flag.svg")

    def test_header_shows_the_team_and_coach(self):
        self.assertIn("MALAWI SCORCHERS", self.html)
        self.assertIn("Lovemore Fazili", self.html)

    def test_next_match(self):
        self.assertIn("Next Match", self.html)
        self.assertIn("Egypt", self.html)
        self.assertIn("1 Aug 2026", self.html)

    def test_group_table(self):
        self.assertIn("2026 Women&#x27;s Africa Cup of Nations · Group C",
                      self.html)
        self.assertIn("As of 30 Jul 2026", self.html)
        self.assertIn("Full group table on Wikipedia", self.html)

    def test_scoreline_is_malawi_first(self):
        self.assertIn(">Malawi</td><td class=\"v2-res-score\">3:2</td>", self.html)

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
        self.assertIn("nt-card-y", self.html)
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
        html = nt_page.render_page(
            load(matches=matches, goals=GOAL_HEADER, lineups=LINEUP_HEADER),
            FakeDataset())
        self.assertIn("Date TBC", html)

    def test_empty_states(self):
        html = nt_page.render_page(
            load(matches=MATCH_HEADER, goals=GOAL_HEADER,
                 lineups=LINEUP_HEADER, squads=SQUAD_HEADER, comps=COMP_HEADER),
            FakeDataset())
        self.assertIn("No results yet.", html)
        self.assertIn("No upcoming fixtures.", html)
        self.assertIn("No squad announced yet.", html)

    def test_landing_meta_names_the_current_competition(self):
        self.assertEqual(
            nt_page.landing_meta(load()),
            "National Team &middot; 2026 Women&#x27;s Africa Cup of Nations")


if __name__ == "__main__":
    unittest.main()
