"""Referees and coaches as people (0024), and the pages they earn.

0023 put six names on the match and argued that a referee was not an entity.
This is the reversal, and three things have to hold for it to be safe:

  * **An unresolved name still renders.** Almost every referee on this site was
    typed before the registry existed and nobody has tapped them onto a row.
    Those matches must look exactly as they did — plain text, no link, no page
    — because the alternative is a feature that makes the old data look broken.
  * **An identified name renders through the registry.** Same rule as a player
    (0022): the column holds what was reported, the id holds who that turned
    out to be, and one rename has to move every page.
  * **Every link goes somewhere.** `official_page_ids` is the one function that
    decides who has a page; the pages, the search index and every link under a
    result ask it, because deriving that set twice is how a link 404s.
"""

import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src import (adapt, dataset, lineups, officials,  # noqa: E402
                 render, search, source_supabase)

HEADER = (
    "match_id,competition_id,season_id,stage,matchday,date,venue_id,"
    "home_team_id,away_team_id,home_goals,away_goals,status,"
    "source_type,confidence,referee,assistant_referee_1,"
    "assistant_referee_2,fourth_official,home_coach,away_coach,"
    "referee_id,assistant_referee_1_id,assistant_referee_2_id,"
    "fourth_official_id,home_coach_id,away_coach_id\n"
)

OFFICIALS_CSV = (
    "official_id,full_name,known_as,kind,status\n"
    "MW_OFF_000001,Hassan Nkhoma,,referee,active\n"
    "MW_OFF_000002,Patrick Mwale,,referee,active\n"
    "MW_OFF_000009,Enoch Kafoteka,,coach,active\n"
)


def a_dataset(*match_rows):
    """A minimal two-club, one-competition dataset with the officials tab."""
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
    ds.officials = dataset.parse_officials(OFFICIALS_CSV)
    ds.matches = dataset.parse_matches(HEADER + "".join(
        row + "\n" for row in match_rows))
    return ds


PLAYED = ("M1,C1,S1,md_1,1,2026-08-15,,MW_SS_M1,MW_EK_M1,2,1,played,"
          "reporter,confirmed,H. Nkhoma,,,,E. Kafoteka,,"
          "MW_OFF_000001,,,,MW_OFF_000009,")


class ParseTest(unittest.TestCase):
    def test_the_registry_parses(self):
        reg = dataset.parse_officials(OFFICIALS_CSV)
        self.assertEqual(reg["MW_OFF_000001"].display_name, "Hassan Nkhoma")
        self.assertEqual(reg["MW_OFF_000009"].kind, "coach")

    def test_a_known_as_wins_the_display_name(self):
        reg = dataset.parse_officials(
            "official_id,full_name,known_as,kind,status\n"
            "MW_OFF_000001,Hassan Nkhoma,Hass,referee,active\n")
        self.assertEqual(reg["MW_OFF_000001"].display_name, "Hass")

    def test_an_unknown_kind_is_refused(self):
        with self.assertRaises(dataset.DataError):
            dataset.parse_officials(
                "official_id,full_name,known_as,kind,status\n"
                "MW_OFF_000001,Hassan Nkhoma,,linesman,active\n")

    def test_an_absent_id_header_reads_as_blank(self):
        """Every snapshot in data/canonical/ predates these six columns."""
        bare = ("match_id,competition_id,season_id,stage,matchday,date,venue_id,"
                "home_team_id,away_team_id,home_goals,away_goals,status,"
                "source_type,confidence\n"
                "M1,C1,S1,md_1,1,2026-08-15,,T1,T2,2,1,played,reporter,confirmed\n")
        m = next(iter(dataset.parse_matches(bare).values()))
        self.assertEqual(m.referee_id, "")

    def test_the_snapshot_emits_the_registry_and_the_ids(self):
        self.assertIn("officials", source_supabase.COLUMNS)
        for name in ("referee_id", "home_coach_id", "away_coach_id"):
            self.assertIn(name, source_supabase.COLUMNS["matches"])

    def test_the_notes_column_is_not_published(self):
        """0025 is a working note about people; data/canonical/ is public."""
        self.assertNotIn("notes", source_supabase.COLUMNS["matches"])


class RegistryNameTest(unittest.TestCase):
    """One rename moves every page, exactly as it does for a player."""

    def test_an_identified_name_comes_from_the_registry(self):
        ds = a_dataset(PLAYED)
        view = adapt.league_data(ds, "C1", "S1").matches[0]
        self.assertEqual(view.officials.referee, "Hassan Nkhoma")
        self.assertEqual(view.officials.referee_id, "MW_OFF_000001")

    def test_an_unresolved_name_keeps_what_was_typed(self):
        ds = a_dataset(
            "M1,C1,S1,md_1,1,2026-08-15,,MW_SS_M1,MW_EK_M1,2,1,played,"
            "reporter,confirmed,H. Nkhoma,,,,,,,,,,,")
        view = adapt.league_data(ds, "C1", "S1").matches[0]
        self.assertEqual(view.officials.referee, "H. Nkhoma")
        self.assertEqual(view.officials.referee_id, "")

    def test_an_id_that_resolves_to_nobody_keeps_the_name(self):
        ds = a_dataset(
            "M1,C1,S1,md_1,1,2026-08-15,,MW_SS_M1,MW_EK_M1,2,1,played,"
            "reporter,confirmed,H. Nkhoma,,,,,,MW_OFF_999999,,,,,")
        view = adapt.league_data(ds, "C1", "S1").matches[0]
        self.assertEqual(view.officials.referee, "H. Nkhoma")

    def test_the_lookup_is_tolerant(self):
        ds = a_dataset()
        self.assertEqual(ds.official_name("MW_OFF_000001"), "Hassan Nkhoma")
        self.assertEqual(ds.official_name(""), "")
        self.assertEqual(ds.official_name("MW_OFF_999999"), "")


class MarkupTest(unittest.TestCase):
    def sheet_html(self, ds, **kwargs):
        view = adapt.league_data(ds, "C1", "S1").matches[0]
        return lineups.two_sided_row_html(
            None, None, "Silver Strikers", "Ekhaya FC",
            officials=view.officials, **kwargs)

    def test_an_identified_official_links_to_their_page(self):
        html = self.sheet_html(
            a_dataset(PLAYED),
            official_href=render.official_href_for("../", {"MW_OFF_000001",
                                                           "MW_OFF_000009"}))
        self.assertIn('href="../officials/MW_OFF_000001.html"', html)
        self.assertIn('href="../officials/MW_OFF_000009.html"', html)

    def test_an_unresolved_official_is_plain_text(self):
        html = self.sheet_html(a_dataset(
            "M1,C1,S1,md_1,1,2026-08-15,,MW_SS_M1,MW_EK_M1,2,1,played,"
            "reporter,confirmed,H. Nkhoma,,,,,,,,,,,"))
        self.assertIn("H. Nkhoma", html)
        self.assertNotIn("officials/", html)

    def test_an_official_with_no_page_is_never_linked(self):
        """The set is what stops a link landing on a 404."""
        html = self.sheet_html(
            a_dataset(PLAYED),
            official_href=render.official_href_for("../", set()))
        self.assertNotIn("officials/", html)

    def test_each_assistant_keeps_its_own_link(self):
        ds = a_dataset(
            "M1,C1,S1,md_1,1,2026-08-15,,MW_SS_M1,MW_EK_M1,2,1,played,"
            "reporter,confirmed,,J. Banda,P. Mwale,,,,,,MW_OFF_000002,,,")
        html = self.sheet_html(
            ds, official_href=render.official_href_for("../", {"MW_OFF_000002"}))
        self.assertIn("J. Banda", html)
        self.assertIn('href="../officials/MW_OFF_000002.html"', html)


class DutyTest(unittest.TestCase):
    def test_every_role_reaches_the_page(self):
        ds = a_dataset(PLAYED)
        by_official = officials.duties(ds)
        self.assertEqual([d.role for d in by_official["MW_OFF_000001"]],
                         ["Referee"])
        coach = by_official["MW_OFF_000009"][0]
        self.assertEqual(coach.role, "Head coach")
        self.assertTrue(coach.is_coach)

    def test_a_coach_gets_the_result_from_their_own_side(self):
        ds = a_dataset(PLAYED)
        coach = officials.duties(ds)["MW_OFF_000009"][0]
        self.assertEqual(coach.outcome, "W")
        self.assertEqual(coach.scoreline, "2-1")

        away = a_dataset(
            "M1,C1,S1,md_1,1,2026-08-15,,MW_SS_M1,MW_EK_M1,2,1,played,"
            "reporter,confirmed,,,,,,B. Mwase,,,,,,MW_OFF_000009")
        self.assertEqual(officials.duties(away)["MW_OFF_000009"][0].outcome, "L")

    def test_a_referee_has_no_result(self):
        """They are not on a side, and a W beside a referee would say they were."""
        ds = a_dataset(PLAYED)
        self.assertEqual(officials.duties(ds)["MW_OFF_000001"][0].outcome, "")

    def test_a_placeholder_match_counts_for_nothing(self):
        ds = a_dataset(
            "M1,C1,S1,md_1,1,2026-08-15,,MW_SS_M1,MW_EK_M1,2,1,played,"
            "placeholder,confirmed,H. Nkhoma,,,,,,MW_OFF_000001,,,,,")
        self.assertEqual(officials.duties(ds), {})

    def test_an_id_with_no_registry_row_is_not_a_person(self):
        ds = a_dataset(
            "M1,C1,S1,md_1,1,2026-08-15,,MW_SS_M1,MW_EK_M1,2,1,played,"
            "reporter,confirmed,H. Nkhoma,,,,,,MW_OFF_999999,,,,,")
        self.assertEqual(officials.duties(ds), {})

    def test_newest_first(self):
        ds = a_dataset(
            PLAYED,
            "M2,C1,S1,md_2,2,2026-08-22,,MW_EK_M1,MW_SS_M1,0,0,played,"
            "reporter,confirmed,H. Nkhoma,,,,,,MW_OFF_000001,,,,,")
        dates = [d.date for d in officials.duties(ds)["MW_OFF_000001"]]
        self.assertEqual(dates, ["2026-08-22", "2026-08-15"])

    def test_the_page_set_is_who_was_named_on_a_match(self):
        ds = a_dataset(PLAYED)
        self.assertEqual(officials.official_page_ids(ds),
                         {"MW_OFF_000001", "MW_OFF_000009"})
        # MW_OFF_000002 is in the registry and on no match: nothing to put on
        # a page, exactly as an unused players row has nothing.
        self.assertNotIn("MW_OFF_000002", officials.official_page_ids(ds))


class PageTest(unittest.TestCase):
    def page(self, ds, official_id):
        official = ds.officials[official_id]
        career = officials.duties(ds)[official_id]
        return (officials._header(official, career)
                + officials._tiles(official, career)
                + officials._matches_table(career))

    def test_a_referee_page_counts_the_roles_it_has(self):
        html = self.page(a_dataset(PLAYED), "MW_OFF_000001")
        self.assertIn("As referee", html)
        self.assertNotIn("As assistant", html)
        self.assertIn("MATCH OFFICIAL", html)

    def test_a_coach_page_says_won_drawn_lost(self):
        html = self.page(a_dataset(PLAYED), "MW_OFF_000009")
        self.assertIn("Won", html)
        self.assertNotIn("Lost", html)
        self.assertIn("HEAD COACH", html)
        self.assertIn("Silver Strikers", html)

    def test_the_match_row_names_both_sides(self):
        html = self.page(a_dataset(PLAYED), "MW_OFF_000001")
        self.assertIn("Silver Strikers", html)
        self.assertIn("Ekhaya FC", html)
        self.assertIn("2-1", html)

    def test_one_role_earns_no_role_column(self):
        """A third of a 390px table repeating one word helps nobody."""
        html = self.page(a_dataset(PLAYED), "MW_OFF_000001")
        self.assertNotIn("pl-th-role", html)

    def test_a_mixed_career_gets_the_column_back(self):
        ds = a_dataset(
            PLAYED,
            "M2,C1,S1,md_2,2,2026-08-22,,MW_EK_M1,MW_SS_M1,0,0,played,"
            "reporter,confirmed,,H. Nkhoma,,,,,,MW_OFF_000001,,,,")
        html = self.page(ds, "MW_OFF_000001")
        self.assertIn("pl-th-role", html)
        self.assertIn("Assistant referee", html)
        self.assertIn("As assistant", html)


class SearchTest(unittest.TestCase):
    def test_an_official_is_indexed_for_the_page_that_exists(self):
        ds = a_dataset(PLAYED)
        rows = search.build_index(ds, [])
        urls = {r[2] for r in rows}
        self.assertIn("officials/MW_OFF_000001.html", urls)
        self.assertNotIn("officials/MW_OFF_000002.html", urls)

    def test_the_type_list_was_appended_to_not_reordered(self):
        """The index stores the integer, so the order is the wire format."""
        self.assertEqual(search.TYPES[:5],
                         ("comp", "nt", "club", "team", "player"))
        self.assertEqual(search.TYPES[search.T_OFFICIAL], "official")


if __name__ == "__main__":
    unittest.main()
