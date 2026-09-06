"""Teaching the importer a team's other names (0046).

    RLS_LIVE=1 python3 -m unittest tests.test_team_aliases_live

WHY THIS IS WORTH LIVE TESTS. add_team_alias is the first thing in the portal
that lets an ordinary reporter change how a name RESOLVES, and a wrong alias
does not fail to match — it matches the wrong team and publishes a result
against it. MW_MB is Moyale Barracks and MW_MR is Moyale Reserve FC, two
different clubs; that is the failure this file exists to pin.

Three things worth holding still:

  * the scope IS the authorization, exactly as it is in 0043 — a reporter may
    name a team they are assigned to report, and no other;
  * a name another team already answers to is refused, and the refusal names
    that team, because the reporter cannot act on "no";
  * the alias actually reaches the matcher — the end-to-end claim, asserted
    through resolve_import_candidates rather than by reading the table back.

Every alias created here is deleted in teardown. NOTHING HERE RENAMES A REAL
TEAM: a display_name renders on the standings table and every fixture line, so
the rename_team tests assert its refusals, and its one success path is a rename
to the name the team already has.
"""

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


def insert(table, row):
    return sb._request("POST", table, body=[row],
                       headers={"Prefer": "return=representation"},
                       require_secret=True)


# See test_import_matching_live for why None must keep meaning anon.
UNSET = object()


@unittest.skipUnless(live_support.available(), "Supabase credentials not configured")
class TeamAliasTest(unittest.TestCase):

    COMP = live_support.Identities.COMP_A     # MW_NRFA — 'a' is assigned
    OTHER = live_support.Identities.COMP_B    # MW_SRFA — 'a' is not

    @classmethod
    def setUpClass(cls):
        cls.identities = live_support.Identities(prefix="MW_ALIASTEST").setup()
        cls.tokens = cls.identities.tokens
        cls.suffix = cls.identities.suffix
        cls.made = []                          # alias ids to remove afterwards

        entries = sb.select("entries", columns="team_id,season_id",
                            params={"competition_id": f"eq.{cls.COMP}"},
                            order="ord.asc", require_secret=True)
        if len(entries) < 2:
            raise unittest.SkipTest(f"{cls.COMP} has too few entries")
        cls.season = entries[0]["season_id"]

        # A team 'a' may name and 'b' may not. The second half is a fact about
        # the data rather than an assumption: a club can field sides in two
        # regions, and a team entered in COMP_B as well would make the scope
        # test pass for the wrong reason.
        in_other = {
            e["team_id"] for e in
            sb.select("entries", columns="team_id",
                      params={"competition_id": f"eq.{cls.OTHER}"},
                      require_secret=True)}
        pool = [e["team_id"] for e in entries if e["team_id"] not in in_other]
        if len(pool) < 2:
            raise unittest.SkipTest(
                f"{cls.COMP} has too few teams that are not also in {cls.OTHER}")
        cls.team_id, cls.other_team_id = pool[0], pool[1]

        names = sb.select("teams", columns="team_id,display_name",
                          params={"team_id":
                                  f"in.({cls.team_id},{cls.other_team_id})"},
                          require_secret=True)
        cls.names = {t["team_id"]: t["display_name"] for t in names}

    @classmethod
    def tearDownClass(cls):
        for alias_id in cls.made:
            delete("aliases", f"id=eq.{alias_id}")
        delete("aliases", f"context=eq.aliastest-{cls.suffix}")
        cls.identities.teardown()

    # ── helpers ──────────────────────────────────────────────────────────────

    def add(self, text, team_id=None, token=UNSET):
        status, body = rpc("add_team_alias", {
            "p_team_id": team_id or self.team_id,
            "p_alias_text": text,
        }, token=self.tokens["a"] if token is UNSET else token)
        if status == 200 and body:
            self.made.append(body[0]["id"])
        return status, body

    def word(self, tag=""):
        """An alias no real team could already answer to."""
        return f"ALIASTEST {self.suffix}{tag} FC"

    def candidates(self, raw, token=UNSET):
        status, body = rpc("import_team_candidates", {
            "p_raw": raw,
            "p_competitions": [self.COMP],
            "p_season_id": self.season,
        }, token=self.tokens["a"] if token is UNSET else token)
        self.assertEqual(status, 200, body)
        return body

    def resolve_one(self, home, away):
        status, body = rpc("resolve_import_candidates", {
            "p_items": [{"idx": 1, "home_team_raw": home, "away_team_raw": away}],
            "p_season_id": self.season,
        }, token=self.tokens["a"])
        self.assertEqual(status, 200, body)
        return body["items"][0]

    # ── the end-to-end claim ─────────────────────────────────────────────────

    def test_an_alias_makes_the_name_resolve(self):
        text = self.word()
        self.assertEqual(self.candidates(text), [],
                         "the test name resolved before it was recorded")

        status, body = self.add(text)
        self.assertEqual(status, 200, body)

        rows = self.candidates(text)
        self.assertEqual([r["team_id"] for r in rows], [self.team_id], rows)
        # Tier 2, not tier 5: an alias is an EXACT match on a name the database
        # holds, and the difference is what keeps the row out of yellow.
        self.assertEqual(rows[0]["rank"], 2, rows[0])
        self.assertEqual(rows[0]["method"], "team_alias")

    def test_the_matcher_stops_saying_the_team_is_unknown(self):
        text = self.word("B")
        before = self.resolve_one(text, self.names[self.other_team_id])
        self.assertIn("team_not_found", before["reasons"], before)

        status, body = self.add(text)
        self.assertEqual(status, 200, body)

        after = self.resolve_one(text, self.names[self.other_team_id])
        # The name resolves now. Whether the PAIRING has a fixture is a
        # different question and not this migration's business — what must be
        # gone is "that team is not in a competition you report".
        self.assertNotIn("team_not_found", after["reasons"], after)
        self.assertTrue(after["home_candidates"], after)
        self.assertEqual(after["home_candidates"][0]["team_id"], self.team_id)

    def test_case_and_punctuation_do_not_matter(self):
        text = self.word("C")
        self.assertEqual(self.add(text)[0], 200)
        rows = self.candidates(text.lower().replace(" ", ".") + ".")
        self.assertEqual([r["team_id"] for r in rows], [self.team_id], rows)

    # ── the scope is the authorization ───────────────────────────────────────

    def test_a_reporter_cannot_name_a_team_they_do_not_report(self):
        status, body = self.add(self.word("D"), token=self.tokens["b"])
        self.assertEqual(status, 403, body)
        self.assertIn("not in a competition you report", message(body))

    def test_an_inactive_reporter_cannot(self):
        status, body = self.add(self.word("E"), token=self.tokens["inactive"])
        self.assertEqual(status, 403, body)

    def test_anon_cannot(self):
        # 404 is a pass: EXECUTE is revoked from anon, so PostgREST cannot see
        # the function at all — which is the strongest of the three answers.
        status, body = self.add(self.word("F"), token=None)
        self.assertIn(status, (401, 403, 404), body)

    def test_an_admin_may_name_any_team(self):
        status, body = self.add(self.word("G"), token=self.tokens["admin"])
        self.assertEqual(status, 200, body)

    # ── the guard ────────────────────────────────────────────────────────────

    def test_a_name_another_team_answers_to_is_refused(self):
        other = self.names[self.other_team_id]
        status, body = self.add(other)
        self.assertEqual(status, 409, body)
        # NAMED, not merely refused: the reporter has to see who they were
        # about to collide with to know what to do instead.
        self.assertIn(other, message(body))

    def test_an_alias_of_another_team_is_refused(self):
        text = self.word("H")
        self.assertEqual(self.add(text, team_id=self.other_team_id)[0], 200)
        status, body = self.add(text, team_id=self.team_id)
        self.assertEqual(status, 409, body)
        self.assertIn(self.names[self.other_team_id], message(body))

    def test_the_teams_own_name_is_refused(self):
        status, body = self.add(self.names[self.team_id])
        self.assertEqual(status, 400, body)
        self.assertIn("already this team", message(body))

    def test_a_name_too_short_to_match_on_is_refused(self):
        # Two alphanumerics once normalized — "F.C." is two characters as far
        # as the matcher is concerned, and a two-character key is inside half
        # the country's team names.
        status, body = self.add("M.B.")
        self.assertEqual(status, 400, body)
        self.assertIn("too short", message(body))

    def test_a_blank_name_is_refused(self):
        status, body = self.add("   ")
        self.assertEqual(status, 400, body)

    def test_an_unknown_team_is_refused(self):
        # Status asserted loosely, message asserted exactly — the convention
        # test_reporting_live follows for "match not found", and for the same
        # reason: PostgREST has no mapping for P0002 and answers 500, so the
        # sentence is the part that is actually promised to anybody.
        status, body = self.add(self.word("I"), team_id="MW_NOT_A_TEAM")
        self.assertNotEqual(status, 200, body)
        self.assertIn("not in the database", message(body))

    def test_recording_the_same_name_twice_adds_one_row(self):
        text = self.word("J")
        first = self.add(text)
        second = self.add(text)
        self.assertEqual(first[0], 200, first[1])
        self.assertEqual(second[0], 200, second[1])
        # Idempotent on the name, like create_player: tapping twice on a weak
        # connection must not grow the table.
        self.assertEqual(first[1][0]["id"], second[1][0]["id"])
        rows = sb.select("aliases", columns="id",
                         params={"entity_type": "eq.team",
                                 "entity_id": f"eq.{self.team_id}",
                                 "alias_text": f"eq.{text}"},
                         require_secret=True)
        self.assertEqual(len(rows), 1, rows)

    # ── removing ─────────────────────────────────────────────────────────────

    def test_a_reporter_cannot_remove_a_name(self):
        text = self.word("K")
        status, body = self.add(text)
        self.assertEqual(status, 200, body)
        alias_id = body[0]["id"]
        status, body = rpc("remove_team_alias", {"p_alias_id": alias_id},
                           token=self.tokens["a"])
        self.assertEqual(status, 403, body)
        self.assertIn("administrator", message(body))

    def test_an_admin_can_remove_a_name(self):
        text = self.word("L")
        status, body = self.add(text)
        self.assertEqual(status, 200, body)
        alias_id = body[0]["id"]
        status, body = rpc("remove_team_alias", {"p_alias_id": alias_id},
                           token=self.tokens["admin"])
        # 204: the function returns void, so there is no body to send.
        self.assertIn(status, (200, 204), body)
        self.assertEqual(self.candidates(text), [])

    def test_removing_something_that_is_not_a_team_alias_is_refused(self):
        # The entity_type filter is what stops this becoming a way to delete a
        # player's old spelling — which merge_players depends on keeping.
        row = insert("aliases", {
            "alias_text": f"aliastest-{self.suffix}",
            "entity_type": "club", "entity_id": "MW_BULL",
            "context": f"aliastest-{self.suffix}", "ord": 0,
        })[0]
        try:
            status, body = rpc("remove_team_alias", {"p_alias_id": row["id"]},
                               token=self.tokens["admin"])
            self.assertNotEqual(status, 200, body)
            self.assertIn("not a team alias", message(body))
            # And it is still there, which is the half that matters.
            self.assertTrue(sb.select("aliases", columns="id",
                                      params={"id": f"eq.{row['id']}"},
                                      require_secret=True))
        finally:
            delete("aliases", f"id=eq.{row['id']}")

    # ── renaming ─────────────────────────────────────────────────────────────

    def test_a_reporter_cannot_rename_a_team(self):
        status, body = rpc("rename_team", {
            "p_team_id": self.team_id, "p_display_name": self.word("M"),
        }, token=self.tokens["a"])
        self.assertEqual(status, 403, body)
        self.assertIn("administrator", message(body))

    def test_renaming_into_another_teams_name_is_refused(self):
        status, body = rpc("rename_team", {
            "p_team_id": self.team_id,
            "p_display_name": self.names[self.other_team_id],
        }, token=self.tokens["admin"])
        self.assertEqual(status, 409, body)
        self.assertIn(self.names[self.other_team_id], message(body))

    def test_renaming_a_team_to_its_own_name_changes_nothing(self):
        # The one success path this file is willing to take on real data: a
        # display_name is on the standings table and every fixture line, so a
        # test that actually renamed a team would change the published site.
        name = self.names[self.team_id]
        status, body = rpc("rename_team", {
            "p_team_id": self.team_id, "p_display_name": name,
        }, token=self.tokens["admin"])
        self.assertEqual(status, 200, body)
        self.assertEqual(body[0]["display_name"], name)
        # And nothing was filed as an old spelling, because nothing is old.
        rows = sb.select("aliases", columns="id",
                         params={"entity_type": "eq.team",
                                 "entity_id": f"eq.{self.team_id}",
                                 "alias_text": f"eq.{name}"},
                         require_secret=True)
        self.assertEqual(rows, [], rows)

    # ── the picker's list ────────────────────────────────────────────────────

    def test_search_lists_teams_the_caller_reports(self):
        status, body = rpc("search_report_teams", {
            "p_term": "", "p_season_id": self.season, "p_limit": 200,
        }, token=self.tokens["a"])
        self.assertEqual(status, 200, body)
        self.assertIn(self.team_id, [t["team_id"] for t in body])

    def test_search_does_not_leak_teams_outside_the_callers_scope(self):
        status, body = rpc("search_report_teams", {
            "p_term": "", "p_season_id": self.season, "p_limit": 200,
        }, token=self.tokens["b"])
        self.assertEqual(status, 200, body)
        self.assertNotIn(self.team_id, [t["team_id"] for t in body])

    def test_search_returns_the_names_already_recorded(self):
        text = self.word("N")
        self.assertEqual(self.add(text)[0], 200)
        status, body = rpc("search_report_teams", {
            "p_term": text, "p_season_id": self.season, "p_limit": 40,
        }, token=self.tokens["a"])
        self.assertEqual(status, 200, body)
        row = next((t for t in body if t["team_id"] == self.team_id), None)
        self.assertIsNotNone(row, body)
        self.assertIn(text, [a["alias_text"] for a in row["aliases"]])
        # The fact that tells two sides of one club apart, which is the whole
        # reason the picker shows more than a name.
        self.assertTrue(row["competitions"], row)

    def test_search_finds_a_team_by_a_recorded_name(self):
        text = self.word("O")
        self.assertEqual(self.add(text)[0], 200)
        status, body = rpc("search_report_teams", {
            "p_term": text.lower(), "p_season_id": self.season, "p_limit": 40,
        }, token=self.tokens["a"])
        self.assertEqual(status, 200, body)
        self.assertIn(self.team_id, [t["team_id"] for t in body])

    def test_anon_cannot_list_teams(self):
        status, body = rpc("search_report_teams", {"p_term": ""}, token=None)
        self.assertIn(status, (401, 403, 404), body)


if __name__ == "__main__":
    unittest.main()
