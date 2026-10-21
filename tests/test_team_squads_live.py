"""A team's squad and staff, one person at a time (0057).

    RLS_LIVE=1 python3 -m unittest tests.test_team_squads_live

Three things worth holding still:

  * NULL LEAVES A FIELD ALONE. Adding someone already in the database to a
    squad from a pasted list must never clear the date of birth another
    reporter entered — that is the whole reason the save is per field rather
    than whole, and the one way this screen could quietly destroy data;
  * the scope is the authorization, 0046's rule: a reporter may write the
    squad of a team they report, and no other;
  * a squad list is evidence of a club, so search_players' hint names it.

Every player, official, registration and staff row created here is namespaced
and deleted in teardown. No real player is written to: the test mints its own.
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


# See test_import_matching_live for why None must keep meaning anon.
UNSET = object()


@unittest.skipUnless(live_support.available(), "Supabase credentials not configured")
class TeamSquadTest(unittest.TestCase):

    COMP = live_support.Identities.COMP_A     # 'a' is assigned
    OTHER = live_support.Identities.COMP_B    # 'a' is not

    @classmethod
    def setUpClass(cls):
        cls.identities = live_support.Identities(prefix="MW_SQUADTEST").setup()
        cls.tokens = cls.identities.tokens
        cls.suffix = cls.identities.suffix

        seasons = sb.select("seasons", columns="season_id",
                            params={"status": "eq.active"}, require_secret=True)
        if not seasons:
            raise unittest.SkipTest("no active season")
        cls.season = seasons[0]["season_id"]

        in_other = {e["team_id"] for e in sb.select(
            "entries", columns="team_id",
            params={"competition_id": f"eq.{cls.OTHER}"}, require_secret=True)}
        pool = [e["team_id"] for e in sb.select(
            "entries", columns="team_id",
            params={"competition_id": f"eq.{cls.COMP}"},
            order="ord.asc", require_secret=True) if e["team_id"] not in in_other]
        if not pool:
            raise unittest.SkipTest(f"{cls.COMP} has no team that is only in it")
        cls.team_id = pool[0]

        cls.player_id = f"CAF_MW_SQT{cls.suffix}"
        cls.player_name = f"Squadtest {cls.suffix} Banda"
        sb.upsert("players", [{
            "player_id": cls.player_id, "full_name": cls.player_name,
            "status": "active",
        }], on_conflict="player_id")
        cls.official_ids = []

    @classmethod
    def tearDownClass(cls):
        delete("squad_sources", f"team_id=eq.{cls.team_id}&updated_by=in.("
               + ",".join(cls.identities.ids.values()) + ")")
        delete("registrations", f"player_id=eq.{cls.player_id}")
        for oid in cls.official_ids:
            delete("team_staff", f"official_id=eq.{oid}")
            delete("aliases", f"entity_type=eq.official&entity_id=eq.{oid}")
            delete("officials", f"official_id=eq.{oid}")
        delete("players", f"player_id=eq.{cls.player_id}")
        cls.identities.teardown()

    def tearDown(self):
        delete("registrations", f"player_id=eq.{self.player_id}")
        sb._request("PATCH", "players", query=f"player_id=eq.{self.player_id}",
                    body={"dob": None, "birth_year": None, "position": "",
                          "photo_path": ""},
                    headers={"Prefer": "return=minimal"}, require_secret=True)

    # ── helpers ──────────────────────────────────────────────────────────────

    def save(self, token=UNSET, **fields):
        body = {"p_team_id": self.team_id, "p_season_id": None,
                "p_player_id": self.player_id}
        body.update({f"p_{k}": v for k, v in fields.items()})
        return rpc("save_squad_player", body,
                   token=self.tokens["a"] if token is UNSET else token)

    def coach(self, tag):
        status, body = rpc("create_official", {
            "p_full_name": f"Squadtest {self.suffix} Coach {tag}", "p_kind": "coach",
        }, token=self.tokens["a"])
        self.assertEqual(status, 200, body)
        oid = body[0]["official_id"]
        self.official_ids.append(oid)
        return oid

    # ── null leaves it alone ─────────────────────────────────────────────────

    def test_a_player_joins_the_squad_with_what_the_line_said(self):
        status, body = self.save(shirt_number="7", position="mf", birth_year="2003")
        self.assertEqual(status, 200, body)
        self.assertEqual(body["shirt_number"], "7")
        self.assertEqual(body["position"], "MF")
        self.assertEqual(body["birth_year"], 2003)
        self.assertEqual(body["season_id"], self.season)

    def test_adding_them_again_from_a_bare_name_clears_nothing(self):
        self.assertEqual(self.save(shirt_number="7", position="DF",
                                   dob="2002-05-04")[0], 200)
        status, body = self.save()
        self.assertEqual(status, 200, body)
        self.assertEqual(body["shirt_number"], "7")
        self.assertEqual(body["position"], "DF")
        self.assertEqual(body["dob"], "2002-05-04")

    def test_a_blank_clears_on_purpose(self):
        self.save(shirt_number="7", position="DF", dob="2002-05-04")
        status, body = self.save(shirt_number="", position="", dob="", birth_year="")
        self.assertEqual(status, 200, body)
        self.assertEqual((body["shirt_number"], body["position"], body["dob"],
                          body["birth_year"]), ("", "", None, None))

    def test_a_date_sets_the_year(self):
        status, body = self.save(dob="2002-05-04")
        self.assertEqual(status, 200, body)
        self.assertEqual(body["birth_year"], 2002)

    def test_an_age_that_contradicts_a_date_is_refused_and_names_it(self):
        self.save(dob="2002-05-04")
        status, body = self.save(birth_year="2005")
        self.assertEqual(status, 400, body)
        self.assertIn("2002", message(body))

    def test_a_bad_value_is_refused_in_words(self):
        for fields, words in [({"shirt_number": "12a"}, "shirt number"),
                              ({"position": "winger"}, "position must be"),
                              ({"dob": "2999-01-01"}, "not possible"),
                              ({"photo_path": "../elsewhere.jpg"}, "photo")]:
            status, body = self.save(**fields)
            self.assertEqual(status, 400, (fields, body))
            self.assertIn(words, message(body))

    def test_removing_takes_the_registration_and_nothing_else(self):
        self.save(dob="2002-05-04")
        status, body = rpc("remove_squad_player", {
            "p_team_id": self.team_id, "p_season_id": None,
            "p_player_id": self.player_id}, token=self.tokens["a"])
        self.assertEqual((status, body), (200, True))
        player = sb.select("players", columns="dob",
                           params={"player_id": f"eq.{self.player_id}"},
                           require_secret=True)
        self.assertEqual(player[0]["dob"], "2002-05-04")

    # ── the scope is the authorization ───────────────────────────────────────

    def test_a_reporter_cannot_write_another_regions_squad(self):
        status, body = self.save(token=self.tokens["b"])
        self.assertEqual(status, 403, body)
        self.assertIn("do not report a competition this team", message(body))

    def test_an_inactive_reporter_cannot(self):
        self.assertEqual(self.save(token=self.tokens["inactive"])[0], 403)

    def test_anon_cannot(self):
        self.assertIn(self.save(token=None)[0], (401, 403, 404))

    # ── a squad list is evidence of a club ───────────────────────────────────

    def test_the_picker_names_the_squad(self):
        self.save()
        status, body = rpc("search_players", {"p_term": self.player_name},
                           token=self.tokens["a"])
        self.assertEqual(status, 200, body)
        hit = next(r for r in body if r["player_id"] == self.player_id)
        self.assertIn(self.team_id, hit["team_ids"])

    # ── sources (0058) ───────────────────────────────────────────────────────

    def sources(self, text, token=UNSET):
        return rpc("set_squad_sources", {
            "p_team_id": self.team_id, "p_season_id": None, "p_source_ref": text,
        }, token=self.tokens["a"] if token is UNSET else token)

    def read_sources(self, token):
        return call(f"squad_sources?team_id=eq.{self.team_id}"
                    f"&season_id=eq.{self.season}&select=source_ref", token=token)

    def test_sources_are_written_whole_and_a_blank_clears_them(self):
        # The test team is a real one, so whatever is there is put back.
        before = sb.select("squad_sources", columns="source_ref,updated_by",
                           params={"team_id": f"eq.{self.team_id}",
                                   "season_id": f"eq.{self.season}"},
                           require_secret=True)
        try:
            status, body = self.sources("https://example.test/team   \n\nWhatsApp, team manager\n\n")
            self.assertEqual(status, 200, body)
            self.assertEqual(body, "https://example.test/team\n\nWhatsApp, team manager")
            status, rows = self.read_sources(self.tokens["a"])
            self.assertEqual(rows, [{"source_ref": body}])
            self.assertEqual(self.sources(""), (200, ""))
        finally:
            # updated_by too: the test reporter is deleted in teardown, and a
            # row still pointing at it would block that delete.
            if before:
                sb.upsert("squad_sources", [{
                    "team_id": self.team_id, "season_id": self.season,
                    **before[0]}], on_conflict="team_id,season_id")
            else:
                delete("squad_sources", f"team_id=eq.{self.team_id}"
                       f"&season_id=eq.{self.season}")

    def test_sources_are_not_public(self):
        status, rows = self.read_sources(None)
        # Refused, or an empty answer — never a row.
        self.assertTrue(status in (401, 403) or rows == [], (status, rows))

    def test_sources_follow_the_squad_scope(self):
        status, body = self.sources("x", token=self.tokens["b"])
        self.assertEqual(status, 403, body)

    def test_sources_too_long_are_refused_not_cut(self):
        status, body = self.sources("x" * 2001)
        self.assertEqual(status, 400, body)
        self.assertIn("too long", message(body))

    # ── staff ────────────────────────────────────────────────────────────────

    def test_staff_keep_their_role_when_saved_without_one(self):
        oid = self.coach("A")
        args = {"p_team_id": self.team_id, "p_season_id": None, "p_official_id": oid}
        status, body = rpc("save_squad_staff", {**args, "p_role": "Goalkeeper trainer"},
                           token=self.tokens["a"])
        self.assertEqual(status, 200, body)
        status, body = rpc("save_squad_staff", args, token=self.tokens["a"])
        self.assertEqual(body["role"], "Goalkeeper trainer")

    def test_a_referee_is_not_a_teams_staff(self):
        status, body = rpc("create_official", {
            "p_full_name": f"Squadtest {self.suffix} Referee", "p_kind": "referee",
        }, token=self.tokens["a"])
        self.assertEqual(status, 200, body)
        oid = body[0]["official_id"]
        self.official_ids.append(oid)
        status, body = rpc("save_squad_staff", {
            "p_team_id": self.team_id, "p_season_id": None, "p_official_id": oid,
        }, token=self.tokens["a"])
        self.assertEqual(status, 400, body)
        self.assertIn("referees", message(body))

    def test_merging_two_staff_rows_keeps_one(self):
        keep, lose = self.coach("K"), self.coach("L")
        for oid, role in ((keep, "Assistant coach"), (lose, "Physio")):
            self.assertEqual(rpc("save_squad_staff", {
                "p_team_id": self.team_id, "p_season_id": None,
                "p_official_id": oid, "p_role": role}, token=self.tokens["a"])[0], 200)
        status, body = rpc("merge_officials", {"p_loser": lose, "p_winner": keep},
                           token=self.tokens["admin"])
        self.assertEqual(status, 200, body)
        rows = sb.select("team_staff", columns="official_id,role",
                         params={"team_id": f"eq.{self.team_id}",
                                 "official_id": f"in.({keep},{lose})"},
                         require_secret=True)
        self.assertEqual(rows, [{"official_id": keep, "role": "Assistant coach"}])


if __name__ == "__main__":
    unittest.main()
