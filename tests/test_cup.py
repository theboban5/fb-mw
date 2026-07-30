"""Tests for knockout (cup) support: stage handling, shootout rules, bracket."""

import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src import adapt, dataset  # noqa: E402
from src.adapt import MatchView  # noqa: E402
import validate  # noqa: E402

MATCH_HEADER = (
    "match_id,competition_id,season_id,stage,matchday,date,venue_id,"
    "home_team_id,away_team_id,home_goals,away_goals,status,"
    "source_type,confidence,extra_time,home_pens,away_pens\n"
)


def parse_match(row, header=MATCH_HEADER):
    return next(iter(dataset.parse_matches(header + row).values()))


def make_match(mid="M1", comp="CUP", stage="sf", matchday="", hg="", ag="",
               status="scheduled", et="", hp="", ap="", date="2026-08-01"):
    return parse_match(
        f"{mid},{comp},S1,{stage},{matchday},{date},,T1,T2,{hg},{ag},"
        f"{status},fa,confirmed,{et},{hp},{ap}"
    )


def make_view(hg=None, ag=None, status="scheduled", et=False, hp=None, ap=None):
    if hg is not None and status == "scheduled":
        status = "played"
    return MatchView(2, 1, "2026-08-01", "AAA", "BBB", hg, ag, status=status,
                     extra_time=et, home_pens=hp, away_pens=ap)


class StageNormalisationTest(unittest.TestCase):
    def test_matchday_prefix_collapses_to_md(self):
        self.assertEqual(make_match(stage="matchday_3").stage, "md_3")

    def test_case_and_whitespace_normalised(self):
        self.assertEqual(make_match(stage=" SF ").stage, "sf")

    def test_md_form_unchanged(self):
        self.assertEqual(make_match(stage="md_7").stage, "md_7")

    def test_missing_pens_header_reads_as_blank(self):
        header = ("match_id,competition_id,season_id,stage,matchday,date,"
                  "venue_id,home_team_id,away_team_id,home_goals,away_goals,"
                  "status,source_type,confidence\n")
        m = parse_match("M1,C1,S1,sf,,2026-08-01,,T1,T2,,,scheduled,fa,confirmed",
                        header=header)
        self.assertFalse(m.extra_time)
        self.assertIsNone(m.home_pens)
        self.assertIsNone(m.away_pens)

    def test_bad_extra_time_rejected(self):
        with self.assertRaises(dataset.DataError):
            make_match(et="yes")

    def test_extra_time_checkbox_export_accepted(self):
        # A Sheets checkbox column exports TRUE/FALSE, not 0/1.
        self.assertTrue(make_match(et="TRUE").extra_time)
        self.assertFalse(make_match(et="FALSE").extra_time)


def cup_dataset(match_rows):
    """A minimal cup Dataset: one club, two teams, entered, plus match_rows."""
    ds = dataset.Dataset()
    ds.clubs["MW_C"] = dataset.Club("MW_C", "Club", "", "City", "", "", "", "", "", "")
    for tid, code in (("T1", "K_T1"), ("T2", "K_T2")):
        ds.teams[tid] = dataset.Team(tid, "MW_C", "m", "senior", 1, tid, code, "")
        ds.entries[f"E_{tid}"] = dataset.Entry(
            f"E_{tid}", "CUP", "S1", tid, "", 0, "", "active")
    ds.competitions["CUP"] = dataset.Competition(
        "CUP", "mw", "Cup", "cup", None, "m", "senior", "", "FAM", "")
    ds.seasons["S1"] = dataset.Season(
        "S1", "MW", "2026/27", "2026-04-01", "2027-06-30", "active")
    ds.competition_seasons[("CUP", "S1")] = dataset.CompetitionSeason(
        "CUP", "S1", "Sponsored Cup", "knockout", 4, 0, 0, 3, 1, "active")
    for row in match_rows:
        m = parse_match(row)
        ds.matches[m.match_id] = m
    return ds


class CupMatchdayTest(unittest.TestCase):
    def test_matchday_derived_from_stage_order_not_sheet(self):
        # Sheet matchdays are deliberately wrong (9/5): the cup must ignore
        # them and dense-rank the stages present, earliest round first.
        ds = cup_dataset([
            "F1,CUP,S1,final,9,2026-08-29,,T1,T2,,,scheduled,fa,confirmed,,,",
            "S1,CUP,S1,sf,5,2026-08-01,,T1,T2,,,scheduled,fa,confirmed,,,",
        ])
        league = adapt.league_data(ds, "CUP", "S1")
        md_of = {m.stage: m.matchday for m in league.matches}
        self.assertEqual(md_of, {"sf": 1, "final": 2})
        self.assertEqual(league.kind, "cup")
        self.assertEqual(league.stage_of_matchday, {1: "sf", 2: "final"})

    def test_league_keeps_sheet_matchday(self):
        ds = cup_dataset([
            "M1,CUP,S1,md_5,5,2026-08-01,,T1,T2,,,scheduled,fa,confirmed,,,",
        ])
        # Same dataset, but as a league: the sheet matchday must survive.
        ds.competitions["CUP"] = dataset.Competition(
            "CUP", "mw", "League", "league", 1, "m", "senior", "", "FAM", "")
        league = adapt.league_data(ds, "CUP", "S1")
        self.assertEqual(league.matches[0].matchday, 5)
        self.assertEqual(league.kind, "league")


class WinnerCodeTest(unittest.TestCase):
    def test_goals_decide(self):
        self.assertEqual(make_view(hg=2, ag=0).winner_code, "AAA")
        self.assertEqual(make_view(hg=0, ag=1).winner_code, "BBB")

    def test_level_score_falls_to_pens(self):
        self.assertEqual(make_view(hg=1, ag=1, hp=4, ap=3).winner_code, "AAA")
        self.assertEqual(make_view(hg=1, ag=1, hp=2, ap=4).winner_code, "BBB")

    def test_undecided(self):
        self.assertIsNone(make_view().winner_code)                    # unplayed
        self.assertIsNone(make_view(hg=1, ag=1).winner_code)          # no pens
        self.assertIsNone(make_view(hg=1, ag=1, hp=3, ap=3).winner_code)


class ScoreNoteTest(unittest.TestCase):
    def test_pens_only(self):
        self.assertEqual(make_view(hg=1, ag=1, hp=4, ap=3).score_note,
                         "(4–3 pens)")

    def test_aet_only(self):
        self.assertEqual(make_view(hg=2, ag=1, et=True).score_note, "(AET)")

    def test_pens_and_aet(self):
        self.assertEqual(make_view(hg=1, ag=1, et=True, hp=4, ap=3).score_note,
                         "(4–3 pens, AET)")

    def test_plain_result_has_no_note(self):
        self.assertEqual(make_view(hg=2, ag=1).score_note, "")


class CupRoundsTest(unittest.TestCase):
    def sf(self, **kw):
        return MatchView(2, 1, "2026-08-01", "AAA", "BBB", None, None,
                         status="scheduled", stage="sf", **kw)

    def test_placeholder_final_appended(self):
        rounds = adapt.cup_rounds([self.sf()])
        self.assertEqual([s for s, _ in rounds], ["sf", "final"])
        self.assertEqual(rounds[-1][1], [])

    def test_no_placeholder_once_final_exists(self):
        final = MatchView(3, 2, "2026-08-29", "AAA", "BBB", None, None,
                          status="scheduled", stage="final")
        rounds = adapt.cup_rounds([self.sf(), final])
        self.assertEqual([s for s, _ in rounds], ["sf", "final"])
        self.assertEqual(len(rounds[-1][1]), 1)

    def test_rounds_ordered_earliest_first(self):
        qf = MatchView(4, 1, "2026-07-20", "AAA", "BBB", None, None,
                       status="scheduled", stage="qf")
        rounds = adapt.cup_rounds([self.sf(), qf])
        self.assertEqual([s for s, _ in rounds], ["qf", "sf", "final"])


def leg(row, date, home, away, hg=None, ag=None, stage="qf", hp=None, ap=None):
    status = "played" if hg is not None else "scheduled"
    return MatchView(row, 1, date, home, away, hg, ag, status=status,
                     stage=stage, home_pens=hp, away_pens=ap)


class TwoLeggedTieTest(unittest.TestCase):
    def test_mirrored_matches_pair_into_one_tie(self):
        rounds = adapt.cup_rounds([
            leg(2, "2026-06-13", "AAA", "BBB", 0, 0),
            leg(3, "2026-07-11", "BBB", "AAA", 1, 1),
        ])
        ties = rounds[0][1]
        self.assertEqual(len(ties), 1)
        self.assertTrue(ties[0].two_legged)
        # Legs in date order; sides presented in first-leg order.
        self.assertEqual(ties[0].legs[0].date, "2026-06-13")
        self.assertEqual(ties[0].home_code, "AAA")

    def test_same_orientation_repeat_is_not_a_leg(self):
        # A replay keeps the same home team — it must stay its own tie.
        rounds = adapt.cup_rounds([
            leg(2, "2026-06-13", "AAA", "BBB", 1, 1),
            leg(3, "2026-06-20", "AAA", "BBB", 2, 0),
        ])
        self.assertEqual(len(rounds[0][1]), 2)

    def test_aggregate_decides(self):
        tie = adapt.TieView([
            leg(2, "2026-06-14", "AAA", "BBB", 2, 0),
            leg(3, "2026-07-12", "BBB", "AAA", 1, 1),
        ])
        self.assertEqual((tie.agg_home, tie.agg_away), (3, 1))
        self.assertEqual(tie.winner_code, "AAA")
        self.assertFalse(tie.decided_by_away_goals)

    def test_away_goals_break_level_aggregate(self):
        # 0-0 away, then 1-1 at home: AAA's away goal separates a 1-1 tie.
        tie = adapt.TieView([
            leg(2, "2026-06-13", "BBB", "AAA", 0, 0),
            leg(3, "2026-07-11", "AAA", "BBB", 1, 1),
        ])
        self.assertEqual((tie.agg_home, tie.agg_away), (1, 1))
        self.assertEqual(tie.winner_code, "BBB")
        self.assertTrue(tie.decided_by_away_goals)

    def test_pens_break_level_aggregate_and_away_goals(self):
        tie = adapt.TieView([
            leg(2, "2026-06-13", "AAA", "BBB", 1, 1),
            leg(3, "2026-07-11", "BBB", "AAA", 1, 1, hp=7, ap=6),
        ])
        self.assertEqual(tie.winner_code, "BBB")
        self.assertFalse(tie.decided_by_away_goals)

    def test_unfinished_tie_has_no_winner(self):
        tie = adapt.TieView([
            leg(2, "2026-06-13", "AAA", "BBB", 2, 0),
            leg(3, "2026-07-11", "BBB", "AAA"),
        ])
        self.assertIsNone(tie.winner_code)
        self.assertEqual((tie.agg_home, tie.agg_away), (2, 0))


class ValidatorCupTest(unittest.TestCase):
    def errors(self, row, comp_type="cup"):
        ds = cup_dataset([row])
        if comp_type != "cup":
            ds.competitions["CUP"] = dataset.Competition(
                "CUP", "mw", "League", comp_type, 1, "m", "senior", "", "FAM", "")
        return validate.check_cup_rules(ds)

    def test_pens_on_level_cup_score_pass(self):
        errs = self.errors(
            "M1,CUP,S1,sf,,2026-08-01,,T1,T2,1,1,played,fa,confirmed,1,4,3")
        self.assertEqual(errs, [])

    def test_pens_without_level_score_fail(self):
        errs = self.errors(
            "M1,CUP,S1,sf,,2026-08-01,,T1,T2,2,1,played,fa,confirmed,,4,3")
        self.assertTrue(any("not level" in e for e in errs))

    def test_pens_without_score_fail(self):
        errs = self.errors(
            "M1,CUP,S1,sf,,2026-08-01,,T1,T2,,,scheduled,fa,confirmed,,4,3")
        self.assertTrue(any("no score" in e for e in errs))

    def test_level_pens_fail(self):
        errs = self.errors(
            "M1,CUP,S1,sf,,2026-08-01,,T1,T2,1,1,played,fa,confirmed,,4,4")
        self.assertTrue(any("shootout has a winner" in e for e in errs))

    def test_one_sided_pens_fail(self):
        errs = self.errors(
            "M1,CUP,S1,sf,,2026-08-01,,T1,T2,1,1,played,fa,confirmed,,4,")
        self.assertTrue(any("only one of home_pens/away_pens" in e for e in errs))

    def test_pens_on_league_fail(self):
        errs = self.errors(
            "M1,CUP,S1,md_1,1,2026-08-01,,T1,T2,1,1,played,fa,confirmed,,4,3",
            comp_type="league")
        self.assertTrue(any("not a cup" in e for e in errs))

    def test_extra_time_on_league_fail(self):
        errs = self.errors(
            "M1,CUP,S1,md_1,1,2026-08-01,,T1,T2,2,1,played,fa,confirmed,1,,",
            comp_type="league")
        self.assertTrue(any("not a cup" in e for e in errs))

    def test_cup_stage_outside_vocabulary_fail(self):
        errs = self.errors(
            "M1,CUP,S1,md_1,1,2026-08-01,,T1,T2,,,scheduled,fa,confirmed,,,")
        self.assertTrue(any("not a knockout stage" in e for e in errs))

    def test_cup_stage_in_vocabulary_pass(self):
        for stage in sorted(dataset.KNOCKOUT_STAGES):
            errs = self.errors(
                f"M1,CUP,S1,{stage},,2026-08-01,,T1,T2,,,scheduled,fa,confirmed,,,")
            self.assertEqual(errs, [], stage)

    def errors_pair(self, row1, row2):
        ds = cup_dataset([row1, row2])
        return validate.check_cup_rules(ds)

    def test_pens_on_decisive_deciding_leg_with_level_aggregate_pass(self):
        # Leg 2 is 2-0 (not level) but the aggregate is 2-2: legal shootout.
        errs = self.errors_pair(
            "L1,CUP,S1,qf,,2026-06-13,,T1,T2,2,0,played,fa,confirmed,,,",
            "L2,CUP,S1,qf,,2026-07-11,,T2,T1,2,0,played,fa,confirmed,,4,3")
        self.assertEqual(errs, [])

    def test_pens_with_unlevel_aggregate_fail(self):
        errs = self.errors_pair(
            "L1,CUP,S1,qf,,2026-06-13,,T1,T2,2,0,played,fa,confirmed,,,",
            "L2,CUP,S1,qf,,2026-07-11,,T2,T1,1,2,played,fa,confirmed,,4,3")
        self.assertTrue(any("neither is the aggregate" in e for e in errs))

    def test_pens_on_first_leg_fail(self):
        errs = self.errors_pair(
            "L1,CUP,S1,qf,,2026-06-13,,T1,T2,1,1,played,fa,confirmed,,4,3",
            "L2,CUP,S1,qf,,2026-07-11,,T2,T1,,,scheduled,fa,confirmed,,,")
        self.assertTrue(any("non-deciding leg" in e for e in errs))

    def test_unknown_stage_on_league_tolerated(self):
        # The historic sheet mixes md_1/matchday_2 and worse; league stages
        # must never start failing retroactively.
        errs = self.errors(
            "M1,CUP,S1,whatever,1,2026-08-01,,T1,T2,,,scheduled,fa,confirmed,,,",
            comp_type="league")
        self.assertEqual(errs, [])


if __name__ == "__main__":
    unittest.main()
