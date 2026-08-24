"""Which club a profile says a player belongs to — /players/{player_id}.html.

WHAT WAS WRONG. Every fact about a player's side was read off a team sheet, and
1007 of the 1060 identified goals in this dataset belong to a match that has no
team sheet at all. So a ten-goal U20 scorer's page opened with his name, a goals
tile and a table saying "10 — Mzuzu District FCB Katswiri U20 League", and never
once named the club he scored them for. The evidence was in the goal rows the
same table was counting: goals.team_id, thrown away on the way in.

These tests pin the rule and its two inversions — an own goal names the side the
scorer was NOT on, and a team sheet still outranks a goal wherever there is one.
"""

import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src import dataset, hubs  # noqa: E402

MATCHES = (
    "match_id,competition_id,season_id,stage,matchday,date,venue_id,"
    "home_team_id,away_team_id,home_goals,away_goals,status,"
    "source_type,confidence\n"
    "M1,C1,S1,md_1,1,2026-08-15,,MW_SS_M1,MW_EK_M1,2,1,played,reporter,confirmed\n"
    "M2,C1,S1,md_2,2,2026-08-22,,MW_EK_M1,MW_SS_M1,0,1,played,reporter,confirmed\n"
    # The cup tie is the LATEST match, which is what makes it the wrong answer
    # to "what does this player play in".
    "M3,C2,S1,final,,2026-08-29,,MW_SS_M1,MW_EK_M1,3,0,played,reporter,confirmed\n"
)

# P1 scores for Silver Strikers with no team sheet anywhere — the ordinary case
# in this dataset. P2 puts one through his own net. P3 assists.
GOALS = (
    "goal_id,match_id,team_id,player_id,reported_player_name,minute,stoppage,"
    "period,goal_type,assist_player_id,source_type,source_ref,reported_by,"
    "reported_at,confidence,verified_by,verified_at\n"
    "G1,M1,MW_SS_M1,P1,,12,,,,P3,reporter,,,,unconfirmed,,\n"
    "G2,M1,MW_SS_M1,P2,,40,,,own_goal,,reporter,,,,unconfirmed,,\n"
    "G3,M1,MW_EK_M1,P4,,71,,,,,reporter,,,,unconfirmed,,\n"
    "G4,M3,MW_SS_M1,P1,,55,,,,,reporter,,,,unconfirmed,,\n"
)

LINEUPS = (
    "match_id,team_id,player_name,player_id,shirt_number,position,role,"
    "captain,minute_on,minute_off,replaced_player,yellow_card,"
    "yellow_red_card,red_card\n"
    "M2,MW_SS_M1,S. Nyirenda,P5,7,MF,starting,,,,,,,\n"
)


def a_dataset():
    ds = dataset.Dataset()
    for club_id, name in (("MW_SS", "Silver Strikers"), ("MW_EK", "Ekhaya FC")):
        ds.clubs[club_id] = dataset.Club(
            club_id, name, "", "Lilongwe", "", "", "", "", "", "")
        team_id = f"{club_id}_M1"
        ds.teams[team_id] = dataset.Team(
            team_id, club_id, "m", "senior", 1, name, "", "active")
    ds.competitions["C1"] = dataset.Competition(
        "C1", "mw", "Super League", "league", 1, "m", "senior", "", "FAM", "")
    ds.competitions["C2"] = dataset.Competition(
        "C2", "mw", "Airtel Top 8", "cup", None, "m", "senior", "", "FAM", "")
    ds.seasons["S1"] = dataset.Season(
        "S1", "MW", "2026/27", "2026-04-01", "2027-06-30", "active")
    ds.competition_seasons[("C1", "S1")] = dataset.CompetitionSeason(
        "C1", "S1", "FDH Bank Premiership", "league", 2, 0, 0, 3, 1, "active")
    for n, pid in enumerate(("P1", "P2", "P3", "P4", "P5"), start=1):
        ds.players[pid] = dataset.Player(
            pid, f"Player {n}", "", "", "", "", "active")
    ds.matches = dataset.parse_matches(MATCHES)
    ds.goals = dataset.parse_goals(GOALS)
    ds.lineups = dataset.parse_lineups(LINEUPS)
    return ds


def a_career(ds, player_id):
    return hubs.player_careers(ds)[player_id]


class SideFromAGoalTest(unittest.TestCase):
    """A goal says which side you were on. It always did; nothing read it."""

    def test_a_scorer_with_no_team_sheet_still_has_a_club(self):
        ds = a_dataset()
        side = a_career(ds, "P1").side
        self.assertEqual(side.team_label, "Silver Strikers")
        self.assertEqual(side.club_id, "MW_SS")

    def test_an_assist_says_it_too(self):
        """The assister is a teammate of the scorer by definition."""
        ds = a_dataset()
        self.assertEqual(a_career(ds, "P3").side.club_id, "MW_SS")

    def test_an_own_goal_files_the_scorer_on_the_other_side(self):
        """goals.team_id is the BENEFICIARY — the one row that inverts."""
        ds = a_dataset()
        side = a_career(ds, "P2").side
        self.assertEqual(side.club_id, "MW_EK")

    def test_an_own_goal_is_not_a_goal_on_the_table(self):
        credits, own_goals = hubs.player_goal_credits(a_dataset())
        self.assertNotIn("P2", credits)
        self.assertEqual(own_goals["P2"], 1)

    def test_a_team_sheet_still_wins(self):
        """Where there IS a sheet, nothing about this changed."""
        ds = a_dataset()
        side = a_career(ds, "P5").side
        self.assertEqual(side.club_id, "MW_SS")
        self.assertEqual(a_career(ds, "P5").latest.shirt_number, "7")

    def test_a_side_proved_by_a_goal_is_never_an_appearance(self):
        """It is a label for the header, not a game played."""
        career = a_career(a_dataset(), "P1")
        self.assertEqual(career.appearances, [])
        self.assertEqual(career.bench, [])
        self.assertEqual(career.goals, 2)


class HeaderLevelTest(unittest.TestCase):
    """The competition under the club is a level, so it prefers the league."""

    def test_the_cup_final_does_not_become_the_players_competition(self):
        ds = a_dataset()
        # P1's most recent match is the Top 8 final; his league is the answer.
        self.assertEqual(a_career(ds, "P1").side.competition,
                         "FDH Bank Premiership")

    def test_with_only_a_cup_the_cup_is_what_there_is(self):
        ds = a_dataset()
        ds.goals = {k: g for k, g in ds.goals.items() if g.goal_id == "G4"}
        self.assertEqual(a_career(ds, "P1").side.competition, "Airtel Top 8")

    def test_the_header_renders_club_and_level(self):
        ds = a_dataset()
        html = hubs._profile_header(
            ds.players["P1"], a_career(ds, "P1"), ds, {"MW_SS"})
        self.assertIn('href="../clubs/MW_SS.html"', html)
        self.assertIn("Silver Strikers", html)
        self.assertIn('<p class="pl-comp">FDH Bank Premiership</p>', html)

    def test_a_club_with_no_hub_is_a_name_and_not_a_broken_link(self):
        ds = a_dataset()
        html = hubs._profile_header(
            ds.players["P1"], a_career(ds, "P1"), ds, frozenset())
        self.assertIn("Silver Strikers", html)
        self.assertNotIn("clubs/MW_SS.html", html)


class GoalsByCompetitionTest(unittest.TestCase):
    """The table says which club the season's goals were scored for."""

    def test_the_credit_key_carries_the_team(self):
        credits, _own = hubs.player_goal_credits(a_dataset())
        self.assertEqual(credits["P1"], {("S1", "C1", "MW_SS_M1"): 1,
                                         ("S1", "C2", "MW_SS_M1"): 1})

    def test_a_transfer_mid_season_is_two_rows_and_not_one_total(self):
        ds = a_dataset()
        # The same player, the same season, the other club: previously these
        # merged into one line that named neither side.
        ds.goals.update(dataset.parse_goals(
            "goal_id,match_id,team_id,player_id,reported_player_name,minute,"
            "stoppage,period,goal_type,assist_player_id,source_type,"
            "source_ref,reported_by,reported_at,confidence,verified_by,"
            "verified_at\n"
            "G5,M2,MW_EK_M1,P1,,9,,,,,reporter,,,,unconfirmed,,\n"))
        credits, _own = hubs.player_goal_credits(ds)
        self.assertEqual(credits["P1"][("S1", "C1", "MW_SS_M1")], 1)
        self.assertEqual(credits["P1"][("S1", "C1", "MW_EK_M1")], 1)

    def test_the_team_renders_under_the_competition(self):
        ds = a_dataset()
        cell = hubs._goal_row_team(ds, "MW_SS_M1", {"MW_SS"})
        self.assertIn('class="pl-goal-team"', cell)
        self.assertIn('href="../clubs/MW_SS.html"', cell)
        self.assertIn("Silver Strikers", cell)

    def test_an_id_from_no_teams_row_renders_nothing(self):
        """Graceful degradation: silence beats a bare id under a heading."""
        self.assertEqual(hubs._goal_row_team(a_dataset(), "MW_ZZ_M1", set()), "")


class SwitchPlayerTest(unittest.TestCase):
    """The squad list works in a league that has never had a team sheet."""

    def test_a_goal_puts_a_player_in_the_squad_list(self):
        ds = a_dataset()
        careers = hubs.player_careers(ds)
        squads = hubs.squads_by_team(careers, set(careers))
        self.assertIn(("P1", ""), squads["MW_SS_M1"])
        self.assertIn(("P3", ""), squads["MW_SS_M1"])
        # And the own goal is filed with the side he was playing for.
        self.assertIn(("P2", ""), squads["MW_EK_M1"])

    def test_a_sheet_still_supplies_the_shirt(self):
        ds = a_dataset()
        careers = hubs.player_careers(ds)
        squads = hubs.squads_by_team(careers, set(careers))
        self.assertIn(("P5", "7"), squads["MW_SS_M1"])

    def test_it_lists_the_players_of_the_side_the_header_names(self):
        ds = a_dataset()
        careers = hubs.player_careers(ds)
        squads = hubs.squads_by_team(careers, set(careers))
        html = hubs._switch_player("P1", careers["P1"], squads, ds)
        self.assertIn("Player 3", html)      # the assister, same side
        self.assertIn("Player 5", html)      # the sheeted teammate
        self.assertNotIn("Player 4", html)   # Ekhaya's scorer
        self.assertNotIn("Player 1", html)   # never yourself


if __name__ == "__main__":
    unittest.main()
