"""report_imports and the matcher: everything about the importer that is not AI.

    RLS_LIVE=1 python3 -m unittest tests.test_import_matching_live

THE POINT OF TESTING THIS SEPARATELY. The model's job is to read names off a
picture. Everything that decides which FIXTURE those names are — and therefore
everything that could put a wrong result on the site — is ordinary SQL over
rows we already have, and it can be tested with no model, no network to
Anthropic, and no cost. So it is.

These attack the RPCs directly, for the same reason test_reporting_live does.
The three things worth pinning:

  * the scope IS the authorization — an import can never propose a fixture in
    a competition the caller could not publish to, so the review screen cannot
    show a row that submit_match_reports would refuse;
  * a close call is never resolved silently — the senior/reserve case is the
    one this feature would otherwise get wrong invisibly;
  * malformed model output degrades, and never aborts. A model that writes
    "Sat 5 Sep" into a date field must cost that field, not the import.

Every fixture is namespaced MW_IMPTEST* and deleted in teardown. Nothing here
publishes a result or touches a real match.
"""

import itertools
import os
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from src import supabase_client as sb  # noqa: E402
from tests import live_support  # noqa: E402
from tests.live_support import call  # noqa: E402


def rpc(name, body, *, token):
    return call(f"rpc/{name}", token=token, method="POST", body=body)


def message(body):
    if isinstance(body, dict):
        return body.get("message") or body.get("hint") or str(body)
    return str(body)


def delete(table, query):
    sb._request("DELETE", table, query=query,
                headers={"Prefer": "return=minimal"}, require_secret=True)


# `token=None` MEANS ANON — it is the identity live_support.call sends the
# publishable key for, and it is the one every "…cannot…" test turns on. So a
# default of None with `token or self.tokens["a"]` behind it is not a
# convenience, it is a test that silently checks the wrong identity: the anon
# case ran as an authorized reporter, passed the RPC, and reported a 200 as a
# failure of the schema rather than of itself. A sentinel keeps None meaning
# None.
UNSET = object()


def insert(table, row):
    """supabase_client has select/upsert/rpc but no plain insert, and `aliases`
    has an identity primary key we want back so teardown can remove the row."""
    return sb._request("POST", table, body=[row],
                       headers={"Prefer": "return=representation"},
                       require_secret=True)


@unittest.skipUnless(live_support.available(), "Supabase credentials not configured")
class ImportMatchingTest(unittest.TestCase):

    COMP = live_support.Identities.COMP_A     # MW_NRFA — 'a' is assigned
    OTHER = live_support.Identities.COMP_B    # MW_SRFA — 'a' is not

    @classmethod
    def setUpClass(cls):
        cls.identities = live_support.Identities(prefix="MW_IMPTEST").setup()
        cls.tokens = cls.identities.tokens
        cls.suffix = cls.identities.suffix
        cls.made = []
        cls.aliases = []

        entries = sb.select("entries", columns="team_id,season_id",
                            params={"competition_id": f"eq.{cls.COMP}"},
                            order="ord.asc", require_secret=True)
        if len(entries) < 4:
            raise unittest.SkipTest(f"{cls.COMP} has too few entries")
        cls.season = entries[0]["season_id"]

        # FOUR TEAMS THAT HAVE NEVER PLAYED EACH OTHER THIS SEASON.
        #
        # Taking the first four entries was wrong, and the resolver is what
        # caught it. MW_NRFA is a real competition with a real fixture list, so
        # a test fixture between two of its teams is the SECOND row for that
        # pairing — and two candidate fixtures for one pairing is genuinely
        # ambiguous, which resolve_import_candidates correctly refuses to
        # settle. The test was asserting 'green' on a pairing that had every
        # right to be red.
        #
        # It also has to hold for pairs the tests expect to find NOTHING for
        # (name(0) v name(2)), so the requirement is a quadruple with none of
        # its six pairings already played — found here rather than assumed,
        # because which teams those are depends on the real season and changes
        # as it is played.
        played = sb.select("matches", columns="home_team_id,away_team_id",
                           params={"competition_id": f"eq.{cls.COMP}",
                                   "season_id": f"eq.{cls.season}"},
                           require_secret=True)
        taken = {frozenset((m["home_team_id"], m["away_team_id"])) for m in played}
        pool = [e["team_id"] for e in entries]
        quad = next(
            (q for q in itertools.combinations(pool, 4)
             if all(frozenset(p) not in taken for p in itertools.combinations(q, 2))),
            None)
        if quad is None:
            raise unittest.SkipTest(
                f"{cls.COMP} has no four teams that have not met this season")
        cls.team_ids = list(quad)

        names = sb.select("teams", columns="team_id,display_name",
                          params={"team_id": f"in.({','.join(cls.team_ids)})"},
                          require_secret=True)
        cls.names = {t["team_id"]: t["display_name"] for t in names}
        cls.dates = live_support.season_dates(6)

        # Two fixtures in a competition 'a' may report, on different days.
        cls.fixture = cls.make_fixture(0, cls.team_ids[0], cls.team_ids[1],
                                       cls.dates[0], 7)
        cls.fixture2 = cls.make_fixture(1, cls.team_ids[2], cls.team_ids[3],
                                        cls.dates[0], 7)

        # ...and one in a competition they may NOT, for the scope test.
        other = sb.select("entries", columns="team_id,season_id",
                          params={"competition_id": f"eq.{cls.OTHER}"},
                          order="ord.asc", require_secret=True)
        cls.other_ok = len(other) >= 2
        if cls.other_ok:
            cls.other_names = {
                t["team_id"]: t["display_name"] for t in
                sb.select("teams", columns="team_id,display_name",
                          params={"team_id":
                                  f"in.({other[0]['team_id']},{other[1]['team_id']})"},
                          require_secret=True)}
            cls.other_fixture = cls.make_fixture(
                2, other[0]["team_id"], other[1]["team_id"], cls.dates[0], 7,
                competition=cls.OTHER, season=other[0]["season_id"])

    @classmethod
    def make_fixture(cls, i, home, away, date, matchday,
                     competition=None, season=None):
        match_id = f"MW_IMPTEST_{cls.suffix}_{i}"
        sb.upsert("matches", [{
            "match_id": match_id,
            "competition_id": competition or cls.COMP,
            "season_id": season or cls.season,
            "home_team_id": home, "away_team_id": away,
            "stage": f"md_{matchday}", "matchday": matchday, "date": date,
            "status": "scheduled",
            "source_type": "placeholder",   # renders nowhere even if one leaks
            "confidence": "unconfirmed", "ord": 0,
        }], on_conflict="match_id")
        cls.made.append(match_id)
        return match_id

    @classmethod
    def tearDownClass(cls):
        for alias_id in cls.aliases:
            delete("aliases", f"id=eq.{alias_id}")
        for match_id in cls.made:
            delete("match_change_log", f"match_id=eq.{match_id}")
            live_support.drop_test_match(match_id)
        delete("report_imports",
               f"reporter_id=eq.{cls.identities.ids['a']}")
        cls.identities.teardown()

    # ── helpers ──────────────────────────────────────────────────────────────

    def resolve(self, items, token=UNSET, season=None):
        status, body = rpc("resolve_import_candidates", {
            "p_items": items,
            "p_season_id": season or self.season,
        }, token=self.tokens["a"] if token is UNSET else token)
        self.assertEqual(status, 200, body)
        return body

    def one(self, home, away, **extra):
        item = {"idx": 1, "home_team_raw": home, "away_team_raw": away}
        item.update(extra)
        return self.resolve([item])["items"][0]

    def name(self, i):
        return self.names[self.team_ids[i]]

    def add_alias(self, entity_type, entity_id, text):
        rows = insert("aliases", {
            "alias_text": text, "entity_type": entity_type,
            "entity_id": entity_id, "context": "imptest", "ord": 0,
        })
        self.aliases.append(rows[0]["id"])
        return rows[0]["id"]

    # ── the ordinary case ────────────────────────────────────────────────────

    def test_two_exact_names_resolve_green(self):
        item = self.one(self.name(0), self.name(1))
        self.assertEqual(item["confidence"], "green", item["reasons"])
        self.assertEqual(item["match"]["match_id"], self.fixture)
        self.assertEqual(item["match"]["orientation"], "as_given")
        self.assertEqual(item["home_candidates"][0]["method"], "name")

    def test_case_and_punctuation_do_not_matter(self):
        item = self.one(self.name(0).upper() + " .", self.name(1).lower())
        self.assertEqual(item["confidence"], "green", item["reasons"])
        self.assertEqual(item["match"]["match_id"], self.fixture)

    def test_a_whole_matchday_resolves_together(self):
        body = self.resolve([
            {"idx": 1, "home_team_raw": self.name(0), "away_team_raw": self.name(1)},
            {"idx": 2, "home_team_raw": self.name(2), "away_team_raw": self.name(3)},
        ])
        self.assertEqual([i["confidence"] for i in body["items"]],
                         ["green", "green"])
        self.assertEqual(body["consensus"]["competition_id"], self.COMP)
        self.assertEqual(body["consensus"]["stage"], "md_7")

    # ── orientation ──────────────────────────────────────────────────────────

    def test_swapped_sides_are_found_but_never_silently(self):
        """A results graphic puts the winner first as often as the home side."""
        item = self.one(self.name(1), self.name(0))
        self.assertEqual(item["match"]["match_id"], self.fixture)
        self.assertEqual(item["confidence"], "yellow")
        self.assertIn("sides_swapped", item["reasons"])
        # The fixture is reported the way the DATABASE has it, not the graphic.
        self.assertEqual(item["match"]["home_team_id"], self.team_ids[0])

    # ── aliases ──────────────────────────────────────────────────────────────

    def test_a_recorded_alias_resolves(self):
        self.add_alias("team", self.team_ids[0], f"ZZ Imptest Alias {self.suffix}")
        item = self.one(f"ZZ Imptest Alias {self.suffix}", self.name(1))
        self.assertEqual(item["confidence"], "green", item["reasons"])
        self.assertEqual(item["home_candidates"][0]["method"], "team_alias")
        self.assertEqual(item["match"]["match_id"], self.fixture)

    # ── ambiguity is never resolved silently ─────────────────────────────────

    def test_two_teams_answering_to_one_name_is_never_green(self):
        """The senior/reserve case — the failure this whole design exists for.

        Both are real teams in the same competition, both match the typed name
        exactly as well as each other, and only a person knows which was meant.
        """
        self.add_alias("team", self.team_ids[0], f"ZZ Twin {self.suffix}")
        self.add_alias("team", self.team_ids[2], f"ZZ Twin {self.suffix}")
        item = self.one(f"ZZ Twin {self.suffix}", self.name(1))
        self.assertNotEqual(item["confidence"], "green")
        self.assertGreaterEqual(len(item["home_candidates"]), 2)

    def test_an_unknown_name_is_red_and_creates_nothing(self):
        before = sb.select("teams", columns="team_id", require_secret=True)
        item = self.one(f"ZZ Nobody {self.suffix}", self.name(1))
        self.assertEqual(item["confidence"], "red")
        self.assertIn("team_not_found", item["reasons"])
        self.assertIsNone(item["match"])
        after = sb.select("teams", columns="team_id", require_secret=True)
        self.assertEqual(len(before), len(after),
                         "resolving must never mint a team")

    def test_a_pairing_with_no_fixture_is_red(self):
        item = self.one(self.name(0), self.name(2))   # never scheduled together
        self.assertEqual(item["confidence"], "red")
        self.assertIn("no_fixture", item["reasons"])

    # ── the scope is the authorization ───────────────────────────────────────

    def test_a_competition_the_reporter_cannot_publish_is_not_proposed(self):
        """THE SECURITY PROPERTY. If this ever regressed, the review screen
        would offer a reporter rows that submit_match_reports refuses — a
        confusing dead end at best, and a hint about another league's fixture
        list at worst."""
        if not self.other_ok:
            self.skipTest(f"{self.OTHER} has too few entries")
        names = list(self.other_names.values())
        item = self.one(names[0], names[1])
        self.assertEqual(item["confidence"], "red")
        self.assertIn("team_not_found", item["reasons"])
        self.assertEqual(item["home_candidates"], [])

    def test_an_admin_sees_every_competition(self):
        if not self.other_ok:
            self.skipTest(f"{self.OTHER} has too few entries")
        names = list(self.other_names.values())
        status, body = rpc("resolve_import_candidates", {
            "p_items": [{"idx": 1, "home_team_raw": names[0],
                         "away_team_raw": names[1]}],
        }, token=self.tokens["admin"])
        self.assertEqual(status, 200, body)
        self.assertNotEqual(body["items"][0]["home_candidates"], [])

    def test_a_reporter_with_no_assignments_gets_nothing(self):
        status, body = rpc("resolve_import_candidates", {
            "p_items": [{"idx": 1, "home_team_raw": self.name(0),
                         "away_team_raw": self.name(1)}],
        }, token=self.tokens["b"])
        self.assertEqual(status, 200, body)
        self.assertEqual(body["items"][0]["confidence"], "red")

    def test_anon_cannot_resolve(self):
        status, body = rpc("resolve_import_candidates",
                           {"p_items": []}, token=None)
        self.assertIn(status, (401, 403, 404), body)

    def test_an_inactive_reporter_cannot_resolve(self):
        status, body = rpc("resolve_import_candidates", {
            "p_items": [{"idx": 1, "home_team_raw": "x", "away_team_raw": "y"}],
        }, token=self.tokens["inactive"])
        self.assertEqual(status, 403, body)
        self.assertIn("inactive", message(body))

    # ── malformed model output degrades, never aborts ────────────────────────

    def test_a_date_the_model_wrote_badly_costs_the_date_and_nothing_else(self):
        """One badly-copied date must not throw away the other seven results."""
        for bad in ("Sat 5 Sep", "unknown", "2026-13-45", "", "  "):
            with self.subTest(date=bad):
                item = self.one(self.name(0), self.name(1), date=bad)
                self.assertEqual(item["confidence"], "green", item["reasons"])
                self.assertEqual(item["match"]["match_id"], self.fixture)

    def test_a_matchday_written_as_text_still_reads(self):
        for md in ("MD7", "Matchday 7", "7", 7):
            with self.subTest(matchday=md):
                item = self.one(self.name(0), self.name(1), matchday=md)
                self.assertEqual(item["confidence"], "green", item["reasons"])

    def test_invented_fields_are_ignored(self):
        """The model must not be able to reach anything by making up a key —
        an id it hallucinated least of all."""
        item = self.one(self.name(0), self.name(1),
                        match_id="MW_SL_2627_001", competition_id=self.OTHER,
                        team_id="MW_NOPE", confidence=0.99)
        self.assertEqual(item["confidence"], "green", item["reasons"])
        self.assertEqual(item["match"]["match_id"], self.fixture)
        self.assertEqual(item["match"]["competition_id"], self.COMP)

    def test_missing_names_are_red_not_an_error(self):
        body = self.resolve([{"idx": 1}, {"idx": 2, "home_team_raw": None,
                                          "away_team_raw": None}])
        self.assertEqual([i["confidence"] for i in body["items"]],
                         ["red", "red"])

    def test_an_empty_list_resolves_to_nothing(self):
        body = self.resolve([])
        self.assertEqual(body["items"], [])

    def test_too_many_items_are_refused(self):
        status, body = rpc("resolve_import_candidates", {
            "p_items": [{"idx": i} for i in range(61)],
        }, token=self.tokens["a"])
        self.assertEqual(status, 400, body)
        self.assertIn("more than 60", message(body))

    # ── an existing result is flagged, never overwritten ─────────────────────

    def test_a_fixture_that_already_has_a_result_is_yellow(self):
        sb._request("PATCH", "matches", query=f"match_id=eq.{self.fixture2}",
                    body={"status": "played", "home_goals": 1, "away_goals": 1},
                    headers={"Prefer": "return=minimal"}, require_secret=True)
        try:
            item = self.one(self.name(2), self.name(3))
            self.assertEqual(item["confidence"], "yellow")
            self.assertIn("already_has_result", item["reasons"])
        finally:
            sb._request("PATCH", "matches", query=f"match_id=eq.{self.fixture2}",
                        body={"status": "scheduled", "home_goals": None,
                              "away_goals": None},
                        headers={"Prefer": "return=minimal"}, require_secret=True)


@unittest.skipUnless(live_support.available(), "Supabase credentials not configured")
class ReportImportsTest(unittest.TestCase):
    """The evidence table: who may open one, who may read it, who may close it."""

    @classmethod
    def setUpClass(cls):
        cls.identities = live_support.Identities(prefix="MW_IMPROW").setup()
        cls.tokens = cls.identities.tokens
        cls.ids = cls.identities.ids

    @classmethod
    def tearDownClass(cls):
        for reporter_id in cls.ids.values():
            delete("report_imports", f"reporter_id=eq.{reporter_id}")
        cls.identities.teardown()

    def open_import(self, token=UNSET, **kwargs):
        body = {"p_channel": "text", "p_pasted_text": "Blue Eagles 2-1 Silver"}
        body.update(kwargs)
        return rpc("create_report_import", body,
                   token=self.tokens["a"] if token is UNSET else token)

    def test_a_reporter_opens_an_import(self):
        status, import_id = self.open_import()
        self.assertEqual(status, 200, import_id)
        self.assertRegex(import_id, r"^[0-9a-f-]{36}$")
        rows = sb.select("report_imports", columns="*",
                         params={"import_id": f"eq.{import_id}"},
                         require_secret=True)
        self.assertEqual(rows[0]["status"], "pending")
        self.assertEqual(rows[0]["reporter_id"], self.ids["a"])
        self.assertIsNone(rows[0]["extracted"])

    def test_an_empty_submission_is_refused(self):
        status, body = self.open_import(p_pasted_text="", p_channel="text")
        self.assertEqual(status, 400, body)
        self.assertIn("screenshot", message(body))

    def test_a_non_http_link_is_refused(self):
        status, body = self.open_import(
            p_channel="url", p_pasted_text="", p_source_url="file:///etc/passwd")
        self.assertEqual(status, 400, body)
        self.assertIn("http", message(body))

    def test_an_unreadable_link_is_still_kept_as_the_source(self):
        """A Facebook URL that redirects to a login is still where the result
        came from. Losing it would be losing the provenance."""
        status, import_id = self.open_import(
            p_channel="url", p_pasted_text="",
            p_source_url="https://www.facebook.com/some/post/123")
        self.assertEqual(status, 200, import_id)
        rows = sb.select("report_imports", columns="source_url",
                         params={"import_id": f"eq.{import_id}"},
                         require_secret=True)
        self.assertEqual(rows[0]["source_url"],
                         "https://www.facebook.com/some/post/123")

    def test_an_inactive_reporter_cannot_open_one(self):
        status, body = self.open_import(token=self.tokens["inactive"])
        self.assertEqual(status, 403, body)

    def test_anon_cannot_open_one(self):
        status, body = self.open_import(token=None)
        self.assertIn(status, (401, 403, 404), body)

    def test_a_reporter_reads_only_their_own(self):
        status, mine = self.open_import()
        self.assertEqual(status, 200, mine)
        seen = call(f"report_imports?select=import_id&import_id=eq.{mine}",
                    token=self.tokens["b"])
        self.assertEqual(seen, (200, []))

    def test_an_admin_reads_everyones(self):
        status, mine = self.open_import()
        self.assertEqual(status, 200, mine)
        code, rows = call(f"report_imports?select=import_id&import_id=eq.{mine}",
                          token=self.tokens["admin"])
        self.assertEqual(code, 200, rows)
        self.assertEqual(len(rows), 1)

    def test_the_table_cannot_be_written_directly(self):
        """No INSERT/UPDATE/DELETE policy: the extraction record is not
        something the reporter it belongs to may rewrite afterwards."""
        status, mine = self.open_import()
        code, body = call("report_imports", token=self.tokens["a"],
                          method="POST",
                          body={"reporter_id": self.ids["a"], "channel": "text"})
        self.assertIn(code, (401, 403, 404, 405), body)
        code, body = call(f"report_imports?import_id=eq.{mine}",
                          token=self.tokens["a"], method="PATCH",
                          body={"extracted": {"faked": True}})
        self.assertIn(code, (401, 403, 404, 405), body)
        rows = sb.select("report_imports", columns="extracted",
                         params={"import_id": f"eq.{mine}"}, require_secret=True)
        self.assertIsNone(rows[0]["extracted"])

    def test_the_outcome_can_be_closed_by_its_owner(self):
        status, mine = self.open_import()
        code, body = rpc("set_import_outcome",
                         {"p_import_id": mine, "p_status": "published"},
                         token=self.tokens["a"])
        self.assertEqual(code, 200, body)
        self.assertEqual(body[0]["status"] if isinstance(body, list)
                         else body["status"], "published")

    def test_another_reporter_cannot_close_it(self):
        status, mine = self.open_import()
        code, body = rpc("set_import_outcome",
                         {"p_import_id": mine, "p_status": "discarded"},
                         token=self.tokens["b"])
        self.assertEqual(code, 403, body)
        self.assertIn("another reporter", message(body))

    def test_an_invalid_outcome_is_refused(self):
        status, mine = self.open_import()
        code, body = rpc("set_import_outcome",
                         {"p_import_id": mine, "p_status": "extracted"},
                         token=self.tokens["a"])
        self.assertEqual(code, 400, body)


if __name__ == "__main__":
    unittest.main()
