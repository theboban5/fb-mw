"""The Supabase source must rebuild the exact CSV the spreadsheet published.

These run offline. The network half is proven by scripts/parity.py, which
builds the whole site twice and diffs it; what is worth guarding in a unit
test is the transform on either side of the database — the importer's typing
and the emitter's rendering — because a mistake there is silent. A blank that
becomes a NULL that becomes the string "None" would not crash anything; it
would just quietly change a page.
"""

import csv
import io
import os
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "scripts"))

import import_canonical  # noqa: E402
from src import dataset, nt, source_supabase  # noqa: E402

CANONICAL = os.path.join(ROOT, "data", "canonical")


def _strip_formulas(text):
    """Blank any cell still holding a spreadsheet formula.

    The one deliberate difference between the snapshot and the database:
    goals.verified_by carries an unevaluated =AI(...) prompt on 15 rows, which
    the importer drops and reports (see import_canonical._cell). Applying the
    same rule to the expected side keeps this test about the round trip
    instead of restating a known data defect — the drop itself is asserted by
    ImporterTypingTest.
    """
    reader = csv.DictReader(io.StringIO(text))
    columns = [c for c in (reader.fieldnames or [])]
    buf = io.StringIO()
    writer = csv.DictWriter(buf, fieldnames=columns)
    writer.writeheader()
    for row in reader:
        writer.writerow({c: ("" if (row.get(c) or "").strip().startswith("=")
                             else row.get(c)) for c in columns})
    return buf.getvalue()


def _round_trip(tab):
    """canonical CSV -> importer payloads -> emitter -> CSV text.

    Exactly the path a row takes through Postgres, minus Postgres itself.
    """
    path = os.path.join(CANONICAL, f"{tab}.csv")
    # A Supabase-only tab may not be in the snapshot yet. Its emitted header is
    # still worth asserting on, and an empty tab round-trips to itself.
    if not os.path.exists(path) and tab in dataset.SUPABASE_ONLY_TABS:
        return source_supabase.tab_csv(tab, [])
    spec = import_canonical.SPEC_BY_TABLE[tab]
    rows = import_canonical.read_rows(path, spec, artifacts=[])
    return source_supabase.tab_csv(tab, rows)


class ColumnCoverageTest(unittest.TestCase):
    """Every column a parser requires must be one the emitter writes."""

    def test_emitter_covers_every_required_column(self):
        # The parsers raise DataError naming any missing column, so the cheap
        # way to assert coverage is to parse the reconstructed text.
        for tab in dataset.TABS:
            with self.subTest(tab=tab):
                dataset._PARSERS[tab](_round_trip(tab))

    def test_emitter_covers_every_required_nt_column(self):
        for tab in dataset.NT_TABS:
            with self.subTest(tab=tab):
                nt._NT_PARSERS[tab](_round_trip(tab))

    def test_every_tab_has_a_column_list(self):
        self.assertEqual(
            set(source_supabase.COLUMNS),
            set(dataset.TABS) | set(dataset.NT_TABS))

    def test_every_tab_has_an_importer_spec(self):
        """The other half of the round trip, and a trap worth naming.

        A new tab without a Spec does not fail anything on the day it is
        added: _round_trip returns an empty tab whenever the snapshot has no
        file for it, so both tests above quietly pass. They start failing
        weeks later, on the first CI build that writes the file — by which
        time the change that caused it is far behind. Assert the invariant
        itself instead of waiting for the symptom.
        """
        self.assertEqual(
            set(import_canonical.SPEC_BY_TABLE),
            set(dataset.TABS) | set(dataset.NT_TABS))


class CompetitionLevelTest(unittest.TestCase):
    """competitions.level (0039) — national | regional | district, or blank.

    Deliberately a plain passthrough on the dataset side: no enum check, no
    validate.py rule. The database's CHECK constraint is the guard, and an
    unrecognised value must degrade to a string nobody renders rather than
    abort a build for everyone. What is worth pinning here is that the column
    is OPTIONAL — the snapshot predates it, and a competitions.csv without the
    header must still parse.
    """

    def test_level_is_read_when_present(self):
        text = ("competition_id,country,name,type,tier,gender,age_group,"
                "region,governing_body,logo,level\n"
                "MW_X,mw,Example League,league,3,m,senior,SRFA,SRFA,,district\n")
        comp = dataset.parse_competitions(text)["MW_X"]
        self.assertEqual(comp.level, "district")

    def test_a_snapshot_without_the_column_still_parses(self):
        """The trap this exists for: adding a column to the emitter and then
        parsing a CSV written before it. A required column would have made
        every offline build fail until the next real fetch."""
        text = ("competition_id,country,name,type,tier,gender,age_group,"
                "region,governing_body,logo\n"
                "MW_X,mw,Example League,league,3,m,senior,SRFA,SRFA,\n")
        comp = dataset.parse_competitions(text)["MW_X"]
        self.assertEqual(comp.level, "")

    def test_an_unknown_level_is_carried_not_refused(self):
        text = ("competition_id,country,name,type,tier,gender,age_group,"
                "region,governing_body,logo,level\n"
                "MW_X,mw,Example League,league,3,m,senior,,,,continental\n")
        self.assertEqual(
            dataset.parse_competitions(text)["MW_X"].level, "continental")

    def test_the_emitter_writes_the_column(self):
        self.assertIn("level", source_supabase.COLUMNS["competitions"])


class RoundTripTest(unittest.TestCase):
    """The parsed result must be identical, tab by tab, row for row."""

    def test_league_tabs_parse_identically(self):
        # A Supabase-only tab may not be in the snapshot yet; read_snapshot
        # gives it a header-only CSV, which is what an empty tab looks like on
        # both sides of the round trip.
        texts = dataset.read_snapshot(CANONICAL)
        self.assertIsNotNone(texts, "data/canonical/ snapshot not present")
        for tab in dataset.TABS:
            with self.subTest(tab=tab):
                before = dataset._PARSERS[tab](_strip_formulas(texts[tab]))
                after = dataset._PARSERS[tab](_round_trip(tab))
                self.assertEqual(before, after)

    def test_nt_tabs_parse_identically(self):
        for tab in dataset.NT_TABS:
            with self.subTest(tab=tab):
                with open(os.path.join(CANONICAL, f"{tab}.csv"),
                          encoding="utf-8") as fh:
                    before = nt._NT_PARSERS[tab](fh.read())
                after = nt._NT_PARSERS[tab](_round_trip(tab))
                self.assertEqual(before, after)

    def test_row_order_survives(self):
        """`ord` is load-bearing: adapt.py numbers MatchView.row by position."""
        with open(os.path.join(CANONICAL, "matches.csv"), encoding="utf-8") as fh:
            original = [r["match_id"] for _i, r in dataset._rows(
                fh.read(), "matches", {"match_id"})]
        rebuilt = [r["match_id"] for _i, r in dataset._rows(
            _round_trip("matches"), "matches", {"match_id"})]
        self.assertEqual(original, rebuilt)


class CellRenderingTest(unittest.TestCase):
    """NULL, True and False each have one correct spelling in a CSV cell."""

    def test_null_is_a_blank_cell_not_the_word_none(self):
        # The Dataset reads a blank cell as "" and treats it as meaningful —
        # no venue, no kickoff, fixture not yet scheduled to a day.
        self.assertEqual(source_supabase._cell(None), "")

    def test_booleans_use_the_sheets_checkbox_vocabulary(self):
        self.assertEqual(source_supabase._cell(True), "1")
        self.assertEqual(source_supabase._cell(False), "")

    def test_rendered_booleans_parse_back_to_the_same_flag(self):
        text = source_supabase.tab_csv("nt_lineups", [{
            "match_id": "M1", "team_id": "MW_W", "player_name": "A Player",
            "position": "GK", "role": "starting", "captain": True,
            "yellow_card": False, "ord": 1,
        }])
        row = next(iter(csv.DictReader(io.StringIO(text))))
        self.assertEqual(row["captain"], "1")
        self.assertEqual(row["yellow_card"], "")
        parsed = nt._NT_PARSERS["nt_lineups"](text)
        self.assertTrue(parsed[0].captain)
        self.assertFalse(parsed[0].yellow_card)

    def test_integer_zero_is_not_confused_with_blank(self):
        # A 0-0 draw and an unplayed fixture are different rows; rendering 0
        # as "" would turn one into the other.
        self.assertEqual(source_supabase._cell(0), "0")
        self.assertEqual(source_supabase._cell(None), "")


class ImporterTypingTest(unittest.TestCase):
    """The importer applies the parser's normalizations and nothing more."""

    def setUp(self):
        self.spec = import_canonical.SPEC_BY_TABLE["matches"]
        self.artifacts = []

    def cell(self, column, raw):
        return import_canonical._cell(self.spec, column, raw, "test",
                                      self.artifacts)

    def test_blank_source_type_becomes_unknown(self):
        self.assertEqual(self.cell("source_type", ""), "unknown")

    def test_blank_score_is_null_not_zero(self):
        self.assertIsNone(self.cell("home_goals", ""))
        self.assertEqual(self.cell("home_goals", "0"), 0)

    def test_blank_fk_is_null_but_blank_text_stays_blank(self):
        self.assertIsNone(self.cell("venue_id", ""))
        self.assertEqual(self.cell("awarded_note", ""), "")

    def test_checkbox_forms_all_parse(self):
        for raw, expected in (("", False), ("0", False), ("1", True),
                              ("TRUE", True), ("false", False)):
            self.assertIs(self.cell("extra_time", raw), expected)

    def test_a_stray_spreadsheet_formula_is_dropped_and_reported(self):
        # 15 goals rows shipped an unevaluated =AI(...) prompt in verified_by.
        value = self.cell("verified_by", '=AI("Fill an appropriate value")')
        self.assertIsNone(value)
        self.assertEqual(len(self.artifacts), 1)

    def test_points_adjustment_blank_is_zero_not_null(self):
        entries = import_canonical.SPEC_BY_TABLE["entries"]
        self.assertEqual(
            import_canonical._cell(entries, "points_adjustment", "", "t", []), 0)


if __name__ == "__main__":
    unittest.main()
