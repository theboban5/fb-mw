"""Referees, assistants, fourth officials and coaches (0023).

Three things are being protected:

  * **Blank is the answer on almost every match**, and blank renders nothing.
    A "Referee: —" line under every result would be six new ways for the site
    to look unfinished.
  * **An older snapshot still parses.** These columns post-date every CSV in
    data/canonical/, so an absent header has to read as every cell blank —
    the same rule extra_time and the pens columns already live by.
  * **Officials reach the page even with no team sheet.** They ride in the
    line-up block, and a match whose only entered detail is the referee must
    not throw that away for want of an eleven.
"""

import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src import adapt, dataset, lineups, source_supabase  # noqa: E402

HEADER = (
    "match_id,competition_id,season_id,stage,matchday,date,venue_id,"
    "home_team_id,away_team_id,home_goals,away_goals,status,"
    "source_type,confidence,referee,assistant_referee_1,"
    "assistant_referee_2,fourth_official,home_coach,away_coach\n"
)

BARE_HEADER = (
    "match_id,competition_id,season_id,stage,matchday,date,venue_id,"
    "home_team_id,away_team_id,home_goals,away_goals,status,"
    "source_type,confidence\n"
)


def parse_match(row, header=HEADER):
    return next(iter(dataset.parse_matches(header + row).values()))


FULL = parse_match(
    "M1,C1,S1,md_1,1,2026-08-01,,T1,T2,2,1,played,reporter,confirmed,"
    "H. Nkhoma,J. Banda,P. Mwale,S. Zulu,E. Kafoteka,B. Mwase")

NONE = parse_match(
    "M2,C1,S1,md_1,1,2026-08-01,,T1,T2,0,0,played,reporter,confirmed,"
    ",,,,,")


class ParseTest(unittest.TestCase):
    def test_every_column_reads(self):
        self.assertEqual(FULL.referee, "H. Nkhoma")
        self.assertEqual(FULL.assistant_referee_1, "J. Banda")
        self.assertEqual(FULL.assistant_referee_2, "P. Mwale")
        self.assertEqual(FULL.fourth_official, "S. Zulu")
        self.assertEqual(FULL.home_coach, "E. Kafoteka")
        self.assertEqual(FULL.away_coach, "B. Mwase")

    def test_has_officials_is_the_render_or_not_question(self):
        self.assertTrue(FULL.has_officials)
        self.assertFalse(NONE.has_officials)

    def test_a_coach_alone_counts(self):
        """The common case off a graphic: a head coach and nothing else."""
        m = parse_match(
            "M3,C1,S1,md_1,1,2026-08-01,,T1,T2,,,scheduled,fa,confirmed,"
            ",,,,E. Kafoteka,")
        self.assertTrue(m.has_officials)

    def test_an_absent_header_reads_as_blank(self):
        """Every snapshot in data/canonical/ predates these columns."""
        m = parse_match(
            "M4,C1,S1,md_1,1,2026-08-01,,T1,T2,,,scheduled,fa,confirmed",
            header=BARE_HEADER)
        self.assertEqual(m.referee, "")
        self.assertFalse(m.has_officials)


class SnapshotTest(unittest.TestCase):
    def test_the_columns_are_emitted(self):
        """source_supabase writes the tab the parser reads. Keep them in step."""
        cols = source_supabase.COLUMNS["matches"]
        for name in ("referee", "assistant_referee_1", "assistant_referee_2",
                     "fourth_official", "home_coach", "away_coach"):
            self.assertIn(name, cols)

    def test_a_row_with_no_officials_renders_as_empty_cells(self):
        csv = source_supabase.tab_csv("matches", [{
            "match_id": "M1", "referee": None, "home_coach": "",
        }])
        self.assertIn("M1", csv)
        self.assertNotIn("None", csv)


class CrewTest(unittest.TestCase):
    def test_the_crew_skips_what_nobody_entered(self):
        o = lineups.Officials(referee="H. Nkhoma")
        self.assertEqual(o.crew, [("Referee", [("H. Nkhoma", "")])])

    def test_two_assistants_share_one_label(self):
        """One label, two people — each keeps its own id and its own link."""
        o = lineups.Officials(assistant_referee_1="J. Banda",
                              assistant_referee_2="P. Mwale",
                              assistant_referee_2_id="MW_OFF_000002")
        self.assertEqual(o.crew, [("Assistants", [("J. Banda", ""),
                                                  ("P. Mwale", "MW_OFF_000002")])])

    def test_one_assistant_is_singular(self):
        o = lineups.Officials(assistant_referee_1="J. Banda")
        self.assertEqual(o.crew, [("Assistant", [("J. Banda", "")])])

    def test_coaches_are_not_officials(self):
        """They render under their own side, not in the referee line."""
        o = lineups.Officials(home_coach="E. Kafoteka",
                              home_coach_id="MW_OFF_000009")
        self.assertEqual(o.crew, [])
        self.assertFalse(o.any_officials)
        self.assertEqual(o.coach_for(True), ("E. Kafoteka", "MW_OFF_000009"))
        self.assertEqual(o.coach_for(False), ("", ""))


class MarkupTest(unittest.TestCase):
    def setUp(self):
        rows = dataset.parse_lineups(
            "match_id,team_id,player_name,player_id,shirt_number,position,"
            "role,captain,minute_on,minute_off,replaced_player,yellow_card,"
            "yellow_red_card,red_card\n"
            "M1,T1,Mercy Sikelo,P1,1,GK,starting,,,,,,,\n")
        self.sheet = lineups.fold(rows)
        self.officials = lineups.Officials(
            referee="H. Nkhoma", assistant_referee_1="J. Banda",
            home_coach="E. Kafoteka", away_coach="B. Mwase")

    def test_nothing_at_all_renders_nothing(self):
        self.assertEqual(
            lineups.two_sided_row_html(None, None, "A", "B", officials=None), "")
        self.assertEqual(
            lineups.two_sided_row_html(None, None, "A", "B",
                                       officials=lineups.Officials()), "")

    def test_the_coach_sits_under_their_own_side(self):
        html = lineups.two_sided_row_html(
            self.sheet, None, "Silver Strikers", "Ekhaya FC",
            officials=self.officials)
        self.assertIn("E. Kafoteka", html)
        self.assertIn("Head coach", html)
        # The away side has no sheet, but it does have a coach, so it still
        # gets a block — half a team sheet is worth reading, and so is this.
        self.assertIn("Ekhaya FC", html)
        self.assertIn("B. Mwase", html)

    def test_the_referee_sits_at_the_foot(self):
        html = lineups.two_sided_row_html(
            self.sheet, None, "A", "B", officials=self.officials)
        self.assertIn("el-officials", html)
        self.assertIn("H. Nkhoma", html)
        self.assertTrue(html.index("H. Nkhoma") > html.index("Mercy Sikelo"))

    def test_officials_without_a_sheet_still_open(self):
        html = lineups.two_sided_row_html(
            None, None, "A", "B",
            officials=lineups.Officials(referee="H. Nkhoma"))
        self.assertIn("H. Nkhoma", html)
        # Named for what is in it. Calling a referee a line-up would read as a
        # broken feature.
        self.assertIn("Match officials", html)
        self.assertNotIn("Line-ups", html)

    def test_a_sheet_is_still_called_a_line_up(self):
        html = lineups.two_sided_row_html(self.sheet, None, "A", "B")
        self.assertIn("Line-ups", html)

    def test_names_are_escaped(self):
        html = lineups.two_sided_row_html(
            None, None, "A", "B",
            officials=lineups.Officials(referee="<script>x</script>"))
        self.assertNotIn("<script>", html)


class AdaptTest(unittest.TestCase):
    """MatchView carries them, because that is what the renderers hold."""

    def _league(self, row):
        ds = dataset.Dataset()
        ds.clubs["MW_C"] = dataset.Club(
            "MW_C", "Club", "", "City", "", "", "", "", "", "")
        for tid in ("T1", "T2"):
            ds.teams[tid] = dataset.Team(
                tid, "MW_C", "m", "senior", 1, tid, tid, "")
            ds.entries[f"E_{tid}"] = dataset.Entry(
                f"E_{tid}", "C1", "S1", tid, "", 0, "", "active")
        ds.competitions["C1"] = dataset.Competition(
            "C1", "mw", "League", "league", 1, "m", "senior", "", "FAM", "")
        ds.seasons["S1"] = dataset.Season(
            "S1", "MW", "2026/27", "2026-04-01", "2027-06-30", "active")
        ds.competition_seasons[("C1", "S1")] = dataset.CompetitionSeason(
            "C1", "S1", "", "league", 2, 0, 0, 3, 1, "active")
        m = parse_match(row)
        ds.matches[m.match_id] = m
        return adapt.league_data(ds, "C1", "S1").matches[0]

    def test_officials_reach_the_match_view(self):
        view = self._league(
            "M1,C1,S1,md_1,1,2026-08-01,,T1,T2,2,1,played,reporter,confirmed,"
            "H. Nkhoma,,,,E. Kafoteka,")
        self.assertEqual(view.officials.referee, "H. Nkhoma")
        self.assertEqual(view.officials.coach_for(True), ("E. Kafoteka", ""))

    def test_a_match_with_none_carries_none(self):
        """Not an empty Officials: the renderers test for None and skip."""
        view = self._league(
            "M1,C1,S1,md_1,1,2026-08-01,,T1,T2,2,1,played,reporter,confirmed,"
            ",,,,,")
        self.assertIsNone(view.officials)


if __name__ == "__main__":
    unittest.main()
