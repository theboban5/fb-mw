"""A small hand-built Dataset covering the cases captions have to get right.

Deliberately not a copy of real data: it holds a 0-0, a brace, an own goal, a
goal with no named scorer, a scorers tie across the cut, and the longest team
name we can plausibly hit — the five shapes that have broken this pipeline.
"""

import datetime
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from social import data                                        # noqa: E402
from src.dataset import (Club, Competition, CompetitionSeason,  # noqa: E402
                         Dataset, Entry, Goal, Match, Player, Season, Team,
                         Venue)

SEASON = "T_2026_27"
COMPETITION = "T_LEAGUE"
DATE = "2026-08-15"
LONG_NAME = "Goshen City Dedza Dynamos"


def _team(code, name):
    return code, Team(team_id=code, club_id=f"C_{code}", gender="m",
                      age_group="senior", squad_level=1, display_name=name,
                      legacy_code="", status="active")


TEAM_NAMES = {
    "T_AAA": "Alpha FC",
    "T_BBB": "Bravo United",
    "T_CCC": "Charlie Rovers",
    "T_DDD": "Delta Barracks",
    "T_EEE": LONG_NAME,
    "T_FFF": "Foxtrot FC",
}


def _match(mid, home, away, hg, ag, status="played", date=DATE, kickoff="15:00",
           matchday=1):
    return Match(
        match_id=mid, competition_id=COMPETITION, season_id=SEASON,
        stage=f"md_{matchday}", matchday=matchday, date=date, kickoff=kickoff,
        venue_id="T_V1", home_team_id=home, away_team_id=away,
        home_goals=hg, away_goals=ag, status=status, awarded_note="",
        source_type="reporter", source_ref="", reported_by="", reported_at="",
        confidence="confirmed", verified_by="", verified_at="",
    )


def _goal(gid, mid, team, player, minute, goal_type=""):
    return gid, Goal(
        goal_id=gid, match_id=mid, team_id=team, player_id=player,
        minute=minute, stoppage="", period="1h", goal_type=goal_type,
        assist_player_id="", source_type="reporter", source_ref="",
        reported_by="", reported_at="", confidence="confirmed",
        verified_by="", verified_at="",
    )


PLAYER_NAMES = {
    "P_1": "Temwa Ndhlovu",
    "P_2": "Gumbikani Banda",
    "P_3": "Chifuniro Mpinganjira",
    "P_4": "Lucky Mkandawire",
    "P_5": "Festus Duwe",
    "CAF_MW_UNKNOWN": "Unknown Player",
}


def dataset() -> Dataset:
    teams = dict(_team(code, name) for code, name in TEAM_NAMES.items())
    clubs = {
        f"C_{code}": Club(club_id=f"C_{code}", name=name, short_name=name,
                          city="Blantyre", region="South", founded="",
                          crest="", status="active", successor_club_id="",
                          notes="")
        for code, name in TEAM_NAMES.items()
    }
    matches = {
        # A goalless draw: no scorer lines at all.
        "T_M1": _match("T_M1", "T_AAA", "T_BBB", 0, 0),
        # Two scorers for one team, one of them a brace.
        "T_M2": _match("T_M2", "T_CCC", "T_DDD", 3, 1),
        # An own goal, credited to the team that benefits.
        "T_M3": _match("T_M3", "T_EEE", "T_FFF", 1, 0),
        # A goal nobody is named for: the score says 2, one scorer is known.
        "T_M4": _match("T_M4", "T_BBB", "T_AAA", 2, 0),
        # Still to be played — the fixtures post's material.
        "T_M5": _match("T_M5", "T_AAA", "T_CCC", None, None,
                       status="scheduled", date="2026-08-22", matchday=2),
    }
    goals = dict([
        _goal("T_G1", "T_M2", "T_CCC", "P_1", "12"),
        _goal("T_G2", "T_M2", "T_CCC", "P_1", "58"),
        _goal("T_G3", "T_M2", "T_CCC", "P_2", "77", goal_type="penalty"),
        _goal("T_G4", "T_M2", "T_DDD", "P_3", "90"),
        _goal("T_G5", "T_M3", "T_EEE", "P_4", "34", goal_type="own_goal"),
        _goal("T_G6", "T_M4", "T_BBB", "P_5", "22"),
        _goal("T_G7", "T_M4", "T_BBB", "CAF_MW_UNKNOWN", "66"),
    ])
    return Dataset(
        clubs=clubs,
        teams=teams,
        competitions={COMPETITION: Competition(
            competition_id=COMPETITION, country="mw", name="Test Premiership",
            type="league", tier=1, gender="m", age_group="senior", region="",
            governing_body="FAM", logo="")},
        seasons={SEASON: Season(season_id=SEASON, country="MW", label="2026/27",
                                start_date="2026-04-01", end_date="2027-06-30",
                                status="active")},
        competition_seasons={(COMPETITION, SEASON): CompetitionSeason(
            competition_id=COMPETITION, season_id=SEASON, sponsor_name="",
            format="round_robin_2x", teams_count=6, promotion_places=0,
            relegation_places=1, points_win=3, points_draw=1, status="active")},
        entries={
            f"E_{code}": Entry(entry_id=f"E_{code}", competition_id=COMPETITION,
                               season_id=SEASON, team_id=code, group="",
                               points_adjustment=0, adjustment_reason="",
                               status="active")
            for code in TEAM_NAMES
        },
        venues={"T_V1": Venue(venue_id="T_V1", name="Test Ground",
                              city="Blantyre", capacity="")},
        matches=matches,
        goals=goals,
        players={
            pid: Player(player_id=pid, full_name=name, known_as="", dob="",
                        position="", nationality="MW", status="active")
            for pid, name in PLAYER_NAMES.items()
        },
    )


def ctx(date: str = DATE, **options) -> "data.Ctx":
    ds = dataset()
    return data.Ctx(ds=ds, date=datetime.date.fromisoformat(date),
                    season=ds.active_season(), options=dict(options))
