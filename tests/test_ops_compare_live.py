"""The Compare views (0039), exercised over the network with real identities.

The same two things test_ops_live.py proves, for the same two reasons.

  1. ACCESS. anon is refused outright; an ordinary reporter and an inactive one
     get a grant and zero rows, because the is_admin() predicate lives inside
     each view body and the football tables underneath all carry a public read
     policy from 0001. set_competition_level is admin-only and is checked by
     trying to move a real competition and then reading it back.

  2. ARITHMETIC. Every count is recomputed here in Python from raw matches,
     goals and lineups rows, then compared with what the view says. Re-running
     the view's SQL would prove only that Postgres is deterministic; the point
     is to catch a predicate that is subtly wrong. The ones that matter here:

       * a clean sheet belongs to a SIDE, so a 0-0 is two of them and the
         denominator is played*2 — an easy and invisible factor-of-two error;
       * source_type='placeholder' rows must contribute nothing, which is what
         keeps the demo U16 league out of every figure;
       * 'awarded' counts with its recorded score and 'postponed' does not;
       * these views span EVERY season, unlike every other ops_* view. The
         Women's Premiership's only season is complete, and scoping to the
         active one would silently drop the entire women's dataset from the
         screen built to compare women's football. That is asserted directly.

It reads production data and creates real auth users, so it is opt-in:

    RLS_LIVE=1 python3 -m unittest tests.test_ops_compare_live

Every fixture it creates is torn down again. It writes no football data.
"""

import os
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from src import supabase_client as sb  # noqa: E402
from tests import live_support  # noqa: E402
from tests.live_support import call as _call  # noqa: E402

COMPARE_VIEWS = ("ops_competition_stats", "ops_team_stats")

LEVELS = ("national", "regional", "district")
CATEGORIES = ("men", "women", "youth")


def category_of(comp):
    """Mirrors the CASE in both views. Women's beats youth deliberately."""
    if comp["gender"] == "w":
        return "women"
    if comp["age_group"] != "senior":
        return "youth"
    return "men"


def counts_as_played(m):
    """The rule the rest of the site uses, restated independently here."""
    return (m["status"] in ("played", "awarded")
            and m["source_type"] != "placeholder"
            and m["home_goals"] is not None
            and m["away_goals"] is not None)


@unittest.skipUnless(live_support.available(), "Supabase credentials not configured")
class OpsCompareTest(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        cls.identities = live_support.Identities(prefix="MW_CMPTEST").setup()
        cls.tokens = cls.identities.tokens

        # Read with the secret key so RLS cannot quietly narrow the raw
        # material the recomputation is done from.
        cls.competitions = {c["competition_id"]: c for c in sb.select(
            "competitions", columns="*", require_secret=True)}
        cls.comp_seasons = sb.select("competition_seasons", columns="*",
                                     require_secret=True)
        cls.matches = sb.select("matches", columns="*", require_secret=True)
        cls.entries = sb.select("entries", columns="*", require_secret=True)

        cls.goal_rows = {}
        for g in sb.select("goals", columns="match_id", require_secret=True):
            cls.goal_rows[g["match_id"]] = cls.goal_rows.get(g["match_id"], 0) + 1
        cls.sheet_rows = {
            r["match_id"] for r in sb.select("lineups", columns="match_id",
                                             require_secret=True)}

        cls.played = [m for m in cls.matches if counts_as_played(m)]

    @classmethod
    def tearDownClass(cls):
        cls.identities.teardown()

    # ── helpers ──────────────────────────────────────────────────────────────

    def admin_rows(self, path):
        status, body = _call(path, token=self.tokens["admin"])
        self.assertEqual(status, 200, f"admin was refused {path}: {body}")
        return body

    def comp_stats(self):
        return self.admin_rows("ops_competition_stats?select=*")

    def team_stats(self):
        return self.admin_rows("ops_team_stats?select=*")

    def played_in(self, competition_id, season_id):
        return [m for m in self.played
                if m["competition_id"] == competition_id
                and m["season_id"] == season_id]

    # ── 1. Access ────────────────────────────────────────────────────────────

    def test_anon_is_refused_both_views(self):
        """Not 'sees no rows' — refused. anon holds no grant on either."""
        for name in COMPARE_VIEWS:
            with self.subTest(object=name):
                status, body = _call(f"{name}?select=*", token=None)
                self.assertGreaterEqual(status, 400,
                                        f"anon read {name} and got {body!r}")

    def test_reporter_sees_nothing(self):
        for label in ("a", "inactive"):
            for name in COMPARE_VIEWS:
                with self.subTest(identity=label, object=name):
                    status, body = _call(f"{name}?select=*",
                                         token=self.tokens[label])
                    self.assertEqual(status, 200)
                    self.assertEqual(body, [], f"{label} saw rows in {name}")

    def test_admin_sees_data(self):
        for name in COMPARE_VIEWS:
            with self.subTest(object=name):
                self.assertTrue(self.admin_rows(f"{name}?select=*"),
                                f"admin saw nothing in {name}")

    def test_set_competition_level_is_admin_only(self):
        """A reporter must not be able to reclassify a competition. Checked by
        reading the row back: an RLS-shaped refusal can look like a success."""
        comp = "MW_SL"
        before = self.competitions[comp].get("level")

        status, _ = _call("rpc/set_competition_level", token=None, method="POST",
                          body={"p_competition_id": comp, "p_level": "district"})
        self.assertGreaterEqual(status, 400)

        status, body = _call("rpc/set_competition_level", token=self.tokens["a"],
                             method="POST",
                             body={"p_competition_id": comp, "p_level": "district"})
        self.assertGreaterEqual(status, 400,
                                f"a reporter set a competition's level: {body!r}")

        rows = self.admin_rows(f"competitions?competition_id=eq.{comp}&select=level")
        self.assertEqual(rows[0]["level"], before,
                         "the Super League's level changed during the test")

    def test_set_competition_level_rejects_a_word_that_is_not_a_level(self):
        status, body = _call("rpc/set_competition_level", token=self.tokens["admin"],
                             method="POST",
                             body={"p_competition_id": "MW_SL", "p_level": "planetary"})
        self.assertGreaterEqual(status, 400, body)

    # ── 2. Shape ─────────────────────────────────────────────────────────────

    def test_every_competition_season_has_a_row(self):
        """A competition that has played nothing still appears, at zero. It is
        entered in a season and someone is waiting on its first result; hiding
        it is how a league nobody has reported gets forgotten."""
        seen = {(r["competition_id"], r["season_id"]) for r in self.comp_stats()}
        expected = {(cs["competition_id"], cs["season_id"])
                    for cs in self.comp_seasons}
        self.assertEqual(seen, expected)

    def test_completed_seasons_are_included(self):
        """The whole reason these views do not scope to the active season."""
        rows = self.comp_stats()
        statuses = {r["season_status"] for r in rows}
        self.assertIn("complete", statuses,
                      "no completed season in the view — the Women's "
                      "Premiership would be invisible on the Compare tab")

    def test_the_womens_competition_is_present_and_has_matches(self):
        rows = [r for r in self.comp_stats() if r["category"] == "women"]
        self.assertTrue(rows, "no women's competition in ops_competition_stats")
        self.assertTrue(any(r["played"] for r in rows),
                        "the women's competition is present but shows no "
                        "played matches")

    def test_level_is_never_an_unknown_word(self):
        for r in self.comp_stats():
            with self.subTest(comp=r["competition_id"]):
                self.assertIn(r["level"], (None,) + LEVELS)

    def test_category_matches_gender_and_age_group(self):
        for r in self.comp_stats():
            with self.subTest(comp=r["competition_id"]):
                self.assertIn(r["category"], CATEGORIES)
                self.assertEqual(
                    r["category"], category_of(self.competitions[r["competition_id"]]))

    # ── 3. Arithmetic ────────────────────────────────────────────────────────

    def test_counts_match_an_independent_recomputation(self):
        for r in self.comp_stats():
            key = (r["competition_id"], r["season_id"])
            with self.subTest(comp=key):
                played = self.played_in(*key)
                self.assertEqual(r["played"], len(played))
                self.assertEqual(r["sides"], len(played) * 2)
                self.assertEqual(
                    r["goals_total"],
                    sum(m["home_goals"] + m["away_goals"] for m in played))
                self.assertEqual(
                    r["home_goals_total"], sum(m["home_goals"] for m in played))
                self.assertEqual(
                    r["home_wins"],
                    sum(1 for m in played if m["home_goals"] > m["away_goals"]))
                self.assertEqual(
                    r["draws"],
                    sum(1 for m in played if m["home_goals"] == m["away_goals"]))
                self.assertEqual(
                    r["away_wins"],
                    sum(1 for m in played if m["home_goals"] < m["away_goals"]))
                self.assertEqual(
                    r["big_wins"],
                    sum(1 for m in played
                        if abs(m["home_goals"] - m["away_goals"]) >= 3))
                self.assertEqual(
                    r["margin_total"],
                    sum(abs(m["home_goals"] - m["away_goals"]) for m in played))

    def test_a_clean_sheet_belongs_to_a_side_not_a_match(self):
        """A 0-0 is TWO clean sheets. Counting matches instead would make this
        column mean something different in a league with more goalless draws,
        which is the opposite of what it is being asked to compare."""
        for r in self.comp_stats():
            key = (r["competition_id"], r["season_id"])
            with self.subTest(comp=key):
                played = self.played_in(*key)
                expected = (sum(1 for m in played if m["away_goals"] == 0)
                            + sum(1 for m in played if m["home_goals"] == 0))
                self.assertEqual(r["clean_sheet_sides"], expected)
                self.assertLessEqual(r["clean_sheet_sides"], r["sides"])

    def test_placeholder_matches_contribute_nothing(self):
        """Placeholder rows parse and then render nowhere. A competition whose
        every match is one must show zero played, not its fixture count."""
        placeholders = [m for m in self.matches
                        if m["source_type"] == "placeholder"]
        self.assertTrue(placeholders, "no placeholder rows left to test with")
        by_key = {(r["competition_id"], r["season_id"]): r
                  for r in self.comp_stats()}
        for m in placeholders:
            key = (m["competition_id"], m["season_id"])
            with self.subTest(match=m["match_id"]):
                row = by_key[key]
                self.assertEqual(row["played"], len(self.played_in(*key)))
                self.assertNotIn(m["match_id"],
                                 [x["match_id"] for x in self.played_in(*key)])

    def test_postponed_and_scheduled_are_fixtures_not_results(self):
        for r in self.comp_stats():
            key = (r["competition_id"], r["season_id"])
            with self.subTest(comp=key):
                same = [m for m in self.matches
                        if (m["competition_id"], m["season_id"]) == key
                        and m["source_type"] != "placeholder"]
                self.assertEqual(r["fixtures_total"], len(same))
                self.assertEqual(
                    r["scheduled"],
                    sum(1 for m in same if m["status"] == "scheduled"))
                self.assertEqual(
                    r["postponed"],
                    sum(1 for m in same if m["status"] == "postponed"))
                self.assertLessEqual(r["played"], r["fixtures_total"])

    def test_coverage_counts_never_exceed_what_they_are_out_of(self):
        """goal_rows > goals_total would mean a match has more scorers than
        goals, which validate.py check 5 makes impossible — so this is really
        asserting the join did not fan out."""
        for r in self.comp_stats():
            with self.subTest(comp=r["competition_id"]):
                self.assertLessEqual(r["goal_rows"], r["goals_total"])
                self.assertLessEqual(r["matches_with_sheet"], r["played"])
                self.assertLessEqual(r["matches_with_venue"], r["played"])
                self.assertLessEqual(r["matches_with_source"], r["played"])
                self.assertLessEqual(r["matches_confirmed"], r["played"])

    def test_coverage_counts_match_an_independent_recomputation(self):
        for r in self.comp_stats():
            key = (r["competition_id"], r["season_id"])
            with self.subTest(comp=key):
                played = self.played_in(*key)
                self.assertEqual(
                    r["goal_rows"],
                    sum(self.goal_rows.get(m["match_id"], 0) for m in played))
                self.assertEqual(
                    r["matches_with_sheet"],
                    sum(1 for m in played if m["match_id"] in self.sheet_rows))
                self.assertEqual(
                    r["matches_with_venue"],
                    sum(1 for m in played if m["venue_id"]))

    def test_teams_counts_active_entries(self):
        for r in self.comp_stats():
            key = (r["competition_id"], r["season_id"])
            with self.subTest(comp=key):
                expected = sum(
                    1 for e in self.entries
                    if (e["competition_id"], e["season_id"]) == key
                    and e["status"] == "active")
                self.assertEqual(r["teams"], expected)

    # ── 4. The team leaderboard ──────────────────────────────────────────────

    def test_team_rows_reconcile_with_the_competition_totals(self):
        """Two sides per match, and the goals one side scored are the goals the
        other conceded. If either identity fails, the union in the sides CTE
        has drifted from the played predicate above it."""
        comps = {(r["competition_id"], r["season_id"]): r for r in self.comp_stats()}
        by_comp = {}
        for t in self.team_stats():
            by_comp.setdefault((t["competition_id"], t["season_id"]), []).append(t)

        for key, rows in by_comp.items():
            with self.subTest(comp=key):
                total = comps[key]
                self.assertEqual(sum(t["played"] for t in rows), total["played"] * 2)
                self.assertEqual(sum(t["gf"] for t in rows), total["goals_total"])
                self.assertEqual(sum(t["ga"] for t in rows), total["goals_total"])
                self.assertEqual(sum(t["clean_sheets"] for t in rows),
                                 total["clean_sheet_sides"])
                self.assertEqual(sum(t["won"] for t in rows),
                                 total["home_wins"] + total["away_wins"])
                self.assertEqual(sum(t["drawn"] for t in rows), total["draws"] * 2)

    def test_a_teams_own_record_adds_up(self):
        for t in self.team_stats():
            with self.subTest(team=t["team_id"], comp=t["competition_id"]):
                self.assertEqual(t["played"], t["won"] + t["drawn"] + t["lost"])
                self.assertEqual(t["gd"], t["gf"] - t["ga"])
                self.assertLessEqual(t["clean_sheets"], t["played"])
                self.assertLessEqual(t["failed_to_score"], t["played"])
                self.assertLessEqual(t["home_played"], t["played"])
                self.assertGreater(t["played"], 0,
                                   "a team with no matches has a row, which "
                                   "means the sides CTE is producing rows the "
                                   "played predicate did not")

    def test_team_rows_carry_the_same_dimensions_as_their_competition(self):
        """Denormalised so the leaderboard can filter without a join — which
        only helps if it agrees with the competition row it came from."""
        comps = {r["competition_id"]: r for r in self.comp_stats()}
        for t in self.team_stats():
            with self.subTest(team=t["team_id"]):
                comp = comps[t["competition_id"]]
                self.assertEqual(t["level"], comp["level"])
                self.assertEqual(t["category"], comp["category"])
                self.assertEqual(t["competition_tier"], comp["competition_tier"])

    def test_every_team_has_a_name(self):
        """team_name falls back club name, then to the id. A blank would render
        as an empty row at the top of a leaderboard."""
        for t in self.team_stats():
            with self.subTest(team=t["team_id"]):
                self.assertTrue((t["team_name"] or "").strip())


if __name__ == "__main__":
    unittest.main()
