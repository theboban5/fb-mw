"""The club hub's result rows — /clubs/{club_id}.html.

WHAT WAS WRONG. The hub is the page a club's own supporters share, and it grew
the team-sheet block without ever growing the scorer block that sits above it
on every other page. So a match here listed all twenty-two names and did not
say who scored, while the same match one page over said both. A reader cannot
tell that from "the feature is somewhere else"; it reads as broken.
"""

import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src import adapt, dataset, hubs, scorers  # noqa: E402

MATCHES = (
    "match_id,competition_id,season_id,stage,matchday,date,venue_id,"
    "home_team_id,away_team_id,home_goals,away_goals,status,"
    "source_type,confidence\n"
    "M1,C1,S1,md_1,1,2026-08-15,,MW_SS_M1,MW_EK_M1,2,1,played,reporter,confirmed\n"
)

GOALS = (
    "goal_id,match_id,team_id,player_id,reported_player_name,minute,stoppage,"
    "period,goal_type,assist_player_id,source_type,source_ref,reported_by,"
    "reported_at,confidence,verified_by,verified_at\n"
    "G1,M1,MW_SS_M1,CAF_MW_000001,,59,,,,,reporter,,,,unconfirmed,,\n"
    "G2,M1,MW_SS_M1,CAF_MW_000001,,60,,,,,reporter,,,,unconfirmed,,\n"
    "G3,M1,MW_EK_M1,CAF_MW_000002,,71,,,,,reporter,,,,unconfirmed,,\n"
)

LINEUPS = (
    "match_id,team_id,player_name,player_id,shirt_number,position,role,"
    "captain,minute_on,minute_off,replaced_player,yellow_card,"
    "yellow_red_card,red_card\n"
    "M1,MW_SS_M1,Z. Kalima,CAF_MW_000001,9,FW,starting,,,,,,,\n"
)


def a_dataset():
    ds = dataset.Dataset()
    for club_id, name in (("MW_SS", "Silver Strikers"), ("MW_EK", "Ekhaya FC")):
        ds.clubs[club_id] = dataset.Club(
            club_id, name, "", "Lilongwe", "", "", "", "", "", "")
        team_id = f"{club_id}_M1"
        ds.teams[team_id] = dataset.Team(
            team_id, club_id, "m", "senior", 1, name, "", "active")
        ds.entries[f"E_{team_id}"] = dataset.Entry(
            f"E_{team_id}", "C1", "S1", team_id, "", 0, "", "active")
    ds.competitions["C1"] = dataset.Competition(
        "C1", "mw", "Super League", "league", 1, "m", "senior", "", "FAM", "")
    ds.seasons["S1"] = dataset.Season(
        "S1", "MW", "2026/27", "2026-04-01", "2027-06-30", "active")
    ds.competition_seasons[("C1", "S1")] = dataset.CompetitionSeason(
        "C1", "S1", "", "league", 2, 0, 0, 3, 1, "active")
    ds.players["CAF_MW_000001"] = dataset.Player(
        "CAF_MW_000001", "Zebron Kalima", "", "", "", "", "active")
    ds.players["CAF_MW_000002"] = dataset.Player(
        "CAF_MW_000002", "Blessings Singini", "", "", "", "", "active")
    ds.matches = dataset.parse_matches(MATCHES)
    ds.goals = dataset.parse_goals(GOALS)
    ds.lineups = dataset.parse_lineups(LINEUPS)
    return ds


def a_hub():
    ds = a_dataset()
    league = adapt.league_data(ds, "C1", "S1")
    team = ds.teams["MW_SS_M1"]
    club_teams = [(team, league, None, None, 1, league.matches, "MW_SS_M1")]
    goals_by_slug = {league.slug: scorers.goals_by_match(league.goals)}
    return hubs.render_club_hub(
        ds.clubs["MW_SS"], club_teams, "", goals_by_slug)


class ScorerRowTest(unittest.TestCase):
    def test_the_scorers_show_under_the_result(self):
        html = a_hub()
        self.assertIn("v2-scorers-row", html)
        self.assertIn("Zebron Kalima", html)
        self.assertIn("Blessings Singini", html)

    def test_it_is_the_same_block_the_league_page_draws(self):
        """Not a second implementation — render._scorers_block, both times."""
        html = a_hub()
        self.assertIn("v2-match-scorers", html)
        self.assertIn("v2-ms-home", html)
        self.assertIn("v2-ms-away", html)

    def test_a_competition_with_no_goals_adds_nothing(self):
        ds = a_dataset()
        ds.goals = {}
        league = adapt.league_data(ds, "C1", "S1")
        team = ds.teams["MW_SS_M1"]
        html = hubs.render_club_hub(
            ds.clubs["MW_SS"],
            [(team, league, None, None, 1, league.matches, "MW_SS_M1")], "")
        self.assertNotIn("v2-scorers-row", html)


class GoalBadgeTest(unittest.TestCase):
    """The team sheet on this page carries the balls too — same LeagueData."""

    def test_the_sheet_marks_the_scorer(self):
        html = a_hub()
        self.assertIn("el-goal", html)
        self.assertEqual(html.count('class="el-goal"'), 2)


if __name__ == "__main__":
    unittest.main()
