"""Reporter-entered scorers: names that reached the site, and names that did not.

The reporter app (static/report/) lets someone at the side of a pitch type a
scorer's name. Until this was fixed, such a goal was written correctly to
Postgres against the reserved CAF_MW_UNKNOWN player and then dropped by
adapt.league_data before anything rendered — so it appeared nowhere on
everyleague.co, and the only signal the reporter got was silence.

These tests pin the three cases apart:

  * an identified scorer (a real player_id) is unchanged in every respect;
  * an unresolved scorer WITH a reported name now renders and ranks under that
    name, carrying no player_id so nothing links to a player page that does
    not exist;
  * an unresolved scorer with NO name is still dropped — there is nothing to
    show — while still counting toward own-goal totals.
"""

import os
import sys
import unittest
from dataclasses import replace

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src import adapt, dataset, scorers  # noqa: E402
from tests import social_fixture_data as fixture  # noqa: E402

GOAL_HEADER = (
    "goal_id,match_id,team_id,player_name,reported_player_name,player_id,"
    "minute,stoppage,period,goal_type,assist_player_id,source_type,"
    "source_ref,reported_by,reported_at,confidence,verified_by,verified_at\n"
)


def parse_goal(row, header=GOAL_HEADER):
    return next(iter(dataset.parse_goals(header + row).values()))


def make_goal(gid="G1", player_id="CAF_MW_000001", reported="", minute="23",
              goal_type=""):
    return parse_goal(
        f"{gid},M1,T1,,{reported},{player_id},{minute},,1h,{goal_type},,"
        f"reporter,,,,unconfirmed,,"
    )


class ParseTest(unittest.TestCase):
    """reported_player_name has to survive the CSV, or nothing downstream can
    use it. It is optional so that a snapshot taken before the column existed
    still parses."""

    def test_reported_name_is_read(self):
        g = make_goal(player_id="CAF_MW_UNKNOWN", reported="Thandiwe Phiri")
        self.assertEqual(g.reported_player_name, "Thandiwe Phiri")

    def test_blank_when_absent(self):
        self.assertEqual(make_goal().reported_player_name, "")

    def test_missing_column_is_not_an_error(self):
        header = ("goal_id,match_id,team_id,player_id,minute,stoppage,period,"
                  "goal_type,assist_player_id,source_type,source_ref,"
                  "reported_by,reported_at,confidence,verified_by,"
                  "verified_at\n")
        g = parse_goal("G1,M1,T1,CAF_MW_000001,23,,1h,,,fa,,,,unconfirmed,,",
                       header=header)
        self.assertEqual(g.reported_player_name, "")


class GoalViewTest(unittest.TestCase):
    """adapt.league_data itself, run over the shared fixture league.

    T_M4 in that fixture is a 2-0 with one named scorer (P_5) and one goal
    nobody was named for (CAF_MW_UNKNOWN, blank reported name) — exactly the
    two cases that have to behave differently, plus a third built here by
    giving that unknown goal the name a reporter would have typed.
    """

    def league(self, reported_name=None):
        ds = fixture.dataset()
        if reported_name is not None:
            ds.goals["T_G7"] = replace(ds.goals["T_G7"],
                                       reported_player_name=reported_name)
        return adapt.league_data(ds, fixture.COMPETITION, fixture.SEASON)

    def goals_for(self, league, match_id):
        return [g for g in league.goals if g.match_id == match_id]

    def test_identified_scorer_keeps_its_id(self):
        [g] = [g for g in self.goals_for(self.league(), "T_M4")]
        self.assertEqual(g.player_id, "P_5")
        self.assertEqual(g.player_name, fixture.PLAYER_NAMES["P_5"])

    def test_nameless_unknown_is_dropped(self):
        # One goal view for a 2-0: the unnamed one has nothing to render.
        self.assertEqual(len(self.goals_for(self.league(), "T_M4")), 1)

    def test_reported_name_renders_without_an_id(self):
        views = self.goals_for(self.league("Thandiwe Phiri"), "T_M4")
        self.assertEqual(len(views), 2)
        named = [g for g in views if g.player_name == "Thandiwe Phiri"]
        self.assertEqual(len(named), 1)
        # Blank, not CAF_MW_UNKNOWN: render._player_link links an id, and
        # /players/CAF_MW_UNKNOWN.html is not a page that exists.
        self.assertEqual(named[0].player_id, "")
        self.assertEqual(named[0].annotation, "Thandiwe Phiri 66'")

    def test_own_goal_total_is_unaffected(self):
        """The own-goal count reads the Dataset, not these views, so naming an
        unresolved scorer must not change it in either direction."""
        self.assertEqual(self.league().own_goal_total,
                         self.league("Thandiwe Phiri").own_goal_total)


class RankingTest(unittest.TestCase):
    """A goal that shows on a match line should also count in the table the
    reader looks at next. scorers._tally keys a blank id by name, which is what
    makes that work without inventing player pages."""

    @staticmethod
    def goal_view(name, player_id="", team="AAA"):
        return adapt.GoalView(match_id="M1", team_code=team, player_name=name,
                              minute="12", player_id=player_id)

    def test_reported_name_reaches_the_top_scorer_table(self):
        top, _og, _more = scorers.top_scorers([
            self.goal_view("Thandiwe Phiri"),
            self.goal_view("Thandiwe Phiri"),
            self.goal_view("Blessings Nkhoma"),
        ])
        self.assertEqual([(t.player_name, t.goals) for t in top],
                         [("Thandiwe Phiri", 2), ("Blessings Nkhoma", 1)])

    def test_a_name_only_scorer_gets_no_player_link(self):
        top, _og, _more = scorers.top_scorers([self.goal_view("Thandiwe Phiri")])
        self.assertEqual(top[0].player_id, "")

    def test_identified_and_named_scorers_stay_separate(self):
        """Two rows, not one merged row: an id is a stronger claim than a
        string, and collapsing them would silently credit one player with
        another's goals."""
        top, _og, _more = scorers.top_scorers([
            self.goal_view("Thandiwe Phiri", player_id="CAF_MW_000042"),
            self.goal_view("Thandiwe Phiri"),
        ])
        self.assertEqual(len(top), 2)


if __name__ == "__main__":
    unittest.main()
