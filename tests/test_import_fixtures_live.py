"""Reading a fixture list off a picture (0049).

    RLS_LIVE=1 python3 -m unittest tests.test_import_fixtures_live

WHAT IS WORTH PINNING HERE, beyond what test_import_matching_live already
pins for results:

  * "2:30 PM" is what a graphic prints and '^[0-9]{1,2}:[0-9]{2}$' is what
    insert_fixture accepts. Everything in import_kickoff is a small decision
    about ambiguous text, and the one that matters most is the refusal: a
    DOTTED DATE in the kick-off field must not become a confident 18:09.

  * A guess is flagged. A bare "2:30" is half past two in the afternoon in
    every league in Malawi, and that is still an inference rather than
    something the picture said.

  * Nothing is created. resolve_venue mints a ground it does not recognise —
    correct for a reporter typing it, wrong for a model reading a compressed
    screenshot — so import_venue_match matches and stops, and the venues table
    is counted before and after to prove it.

  * A fixture that is ALREADY THERE is detected here, not left to
    insert_fixture's duplicate guard. That is the commonest case on a top-flight
    MATCH DAY poster and it is not an error, so it must not arrive as one.

Every fixture is namespaced MW_FIXTEST* and deleted in teardown. Nothing here
publishes anything.
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


UNSET = object()


@unittest.skipUnless(live_support.available(), "Supabase credentials not configured")
class ImportFixturesTest(unittest.TestCase):

    COMP = live_support.Identities.COMP_A     # MW_NRFA — 'a' is assigned
    OTHER = live_support.Identities.COMP_B    # MW_SRFA — 'a' is not

    @classmethod
    def setUpClass(cls):
        cls.identities = live_support.Identities(prefix="MW_FIXTEST").setup()
        cls.tokens = cls.identities.tokens
        cls.suffix = cls.identities.suffix
        cls.made = []

        entries = sb.select("entries", columns="team_id,season_id",
                            params={"competition_id": f"eq.{cls.COMP}"},
                            order="ord.asc", require_secret=True)
        if len(entries) < 4:
            raise unittest.SkipTest(f"{cls.COMP} has too few entries")
        cls.season = entries[0]["season_id"]

        # FOUR TEAMS WITH NONE OF THEIR SIX PAIRINGS ALREADY PLAYED, for
        # test_import_matching_live's reason: MW_NRFA has a real fixture list,
        # and a pairing that already meets would make "this is new" false for
        # a reason that has nothing to do with the code under test.
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
        cls.dates = live_support.season_dates(4)

        # A real ground to match against, taken from the table rather than
        # named, so the test does not depend on any particular venue existing.
        venue = sb.select("venues", columns="venue_id,name", order="ord.asc",
                          require_secret=True)[0]
        cls.venue_id, cls.venue_name = venue["venue_id"], venue["name"]

        # ONE fixture that already exists, for the confirmation cases.
        cls.existing = cls.make_fixture(0, cls.team_ids[0], cls.team_ids[1],
                                        cls.dates[0], "14:30", cls.venue_id)

    @classmethod
    def make_fixture(cls, i, home, away, date, kickoff, venue_id):
        match_id = f"MW_FIXTEST_{cls.suffix}_{i}"
        sb.upsert("matches", [{
            "match_id": match_id,
            "competition_id": cls.COMP, "season_id": cls.season,
            "home_team_id": home, "away_team_id": away,
            "stage": "md_1", "matchday": 1, "date": date, "kickoff": kickoff,
            "venue_id": venue_id, "status": "scheduled",
            "source_type": "placeholder",   # renders nowhere even if one leaks
            "confidence": "unconfirmed", "ord": 0,
        }], on_conflict="match_id")
        cls.made.append(match_id)
        return match_id

    @classmethod
    def tearDownClass(cls):
        for match_id in cls.made:
            delete("match_change_log", f"match_id=eq.{match_id}")
            live_support.drop_test_match(match_id)
        cls.identities.teardown()

    # ── helpers ──────────────────────────────────────────────────────────────

    def name(self, i):
        return self.names[self.team_ids[i]]

    def resolve(self, items, token=UNSET, competition=None):
        status, body = rpc("resolve_import_fixtures", {
            "p_items": items,
            "p_competition_id": competition,
            "p_season_id": self.season,
        }, token=self.tokens["a"] if token is UNSET else token)
        self.assertEqual(status, 200, body)
        return body

    def one(self, home, away, **extra):
        item = {"idx": 1, "home_team_raw": home, "away_team_raw": away}
        item.update(extra)
        return self.resolve([item])["items"][0]

    def kickoff(self, text):
        status, body = rpc("import_kickoff", {"p_text": text},
                           token=self.tokens["a"])
        self.assertEqual(status, 200, body)
        return body

    def venue_count(self):
        return len(sb.select("venues", columns="venue_id", require_secret=True))

    # ── import_kickoff ───────────────────────────────────────────────────────

    def test_a_printed_kickoff_becomes_a_storable_one(self):
        for text, expected in [
            ("2:30 PM", "14:30"),      # the FDH Bank Premiership poster
            ("2.30pm", "14:30"),
            ("02:30 P.M.", "14:30"),
            ("14:30", "14:30"),
            ("9:00", "09:00"),         # bare and unambiguous — taken as printed
            ("12:30 PM", "12:30"),
            ("12:00 AM", "00:00"),
            ("KICK OFF 3:00 PM", "15:00"),
        ]:
            with self.subTest(text=text):
                got = self.kickoff(text)
                self.assertEqual(got["kickoff"], expected)
                self.assertFalse(got["guessed"], got)

    def test_a_bare_afternoon_time_is_read_as_one_and_flagged(self):
        got = self.kickoff("2:30")
        self.assertEqual(got["kickoff"], "14:30")
        # The flag is the whole point: no Malawian league kicks off at half
        # past two in the morning, and that is still an inference about
        # football rather than something the graphic said.
        self.assertTrue(got["guessed"], got)

    def test_a_dotted_date_in_the_kickoff_field_is_not_a_time(self):
        # "06.09.2026" would match ([0-9]{1,2})[.]([0-9]{2}) as 06.09 and
        # become a confident 18:09. The bare form requires a colon for exactly
        # this reason.
        self.assertIsNone(self.kickoff("06.09.2026")["kickoff"])

    def test_an_unreadable_kickoff_costs_its_own_field_only(self):
        for text in ["", "TBA", "later", "25:00", "14:99", "half two"]:
            with self.subTest(text=text):
                self.assertIsNone(self.kickoff(text)["kickoff"])

    # ── import_venue_match ───────────────────────────────────────────────────

    def test_a_known_ground_resolves(self):
        status, body = rpc("import_venue_match", {"p_name": self.venue_name},
                           token=self.tokens["a"])
        self.assertEqual(status, 200, body)
        self.assertEqual(body, self.venue_id)

    def test_case_and_punctuation_do_not_matter_for_a_ground(self):
        status, body = rpc("import_venue_match",
                           {"p_name": self.venue_name.upper() + " ."},
                           token=self.tokens["a"])
        self.assertEqual(status, 200, body)
        self.assertEqual(body, self.venue_id)

    def test_an_unknown_ground_creates_nothing(self):
        before = self.venue_count()
        status, body = rpc("import_venue_match",
                           {"p_name": f"Fixtest Ground {self.suffix}"},
                           token=self.tokens["a"])
        self.assertEqual(status, 200, body)
        self.assertIsNone(body)
        # resolve_venue would have minted one. This is the whole difference.
        self.assertEqual(self.venue_count(), before)

    def test_tba_is_not_a_ground(self):
        for text in ["TBA", "T.B.C.", "to be announced"]:
            with self.subTest(text=text):
                self.assertIsNone(
                    rpc("import_venue_match", {"p_name": text},
                        token=self.tokens["a"])[1])

    # ── the competition comes from the teams ─────────────────────────────────

    def test_two_teams_decide_which_competition_this_is(self):
        body = self.resolve([
            {"idx": 1, "home_team_raw": self.name(2), "away_team_raw": self.name(3),
             "date": self.dates[1]},
        ])
        self.assertEqual(body["competition_id"], self.COMP)
        # From `entries`, not from anything the model wrote.
        self.assertEqual(body["competition_source"], "teams")

    def test_a_competition_hint_does_not_overrule_the_teams(self):
        body = self.resolve([
            {"idx": 1, "home_team_raw": self.name(2), "away_team_raw": self.name(3),
             "date": self.dates[1],
             "competition_hint": "Super League of Malawi"},
        ])
        # The teams are entered in exactly one competition, so there is no tie
        # for the hint to break and it is ignored entirely.
        self.assertEqual(body["competition_id"], self.COMP)
        self.assertEqual(body["competition_source"], "teams")

    def test_a_chosen_competition_is_honoured_and_checked(self):
        body = self.resolve([
            {"idx": 1, "home_team_raw": self.name(2), "away_team_raw": self.name(3)},
        ], competition=self.COMP)
        self.assertEqual(body["competition_source"], "given")

        status, body = rpc("resolve_import_fixtures", {
            "p_items": [], "p_competition_id": self.OTHER,
            "p_season_id": self.season,
        }, token=self.tokens["a"])
        self.assertEqual(status, 403, body)
        self.assertIn("not assigned", message(body))

    def test_a_cup_graphic_is_not_given_up_on(self):
        """Every Airtel Top 8 side is also a Super League side.

        So every row of a Top 8 graphic shares two competitions, votes for
        neither, and the modal vote comes back empty. What the rows AGREE on is
        still exactly those two, and the hint chooses between them — from a
        shortlist `entries` wrote, never from the whole table.
        """
        cup = "MW_TOP8"
        entries = sb.select("entries", columns="team_id,season_id",
                            params={"competition_id": f"eq.{cup}"},
                            order="ord.asc", require_secret=True)
        if len(entries) < 2:
            self.skipTest(f"{cup} has too few entries")
        season = entries[0]["season_id"]
        names = {t["team_id"]: t["display_name"] for t in sb.select(
            "teams", columns="team_id,display_name",
            params={"team_id": f"in.({entries[0]['team_id']},{entries[1]['team_id']})"},
            require_secret=True)}
        label = sb.select("competition_seasons", columns="sponsor_name",
                          params={"competition_id": f"eq.{cup}",
                                  "season_id": f"eq.{season}"},
                          require_secret=True)[0]["sponsor_name"]

        item = {"idx": 1,
                "home_team_raw": names[entries[0]["team_id"]],
                "away_team_raw": names[entries[1]["team_id"]]}

        # An admin reports everything, so the shortlist is as wide as it gets.
        status, body = rpc("resolve_import_fixtures", {
            "p_items": [item], "p_season_id": season,
        }, token=self.tokens["admin"])
        self.assertEqual(status, 200, body)
        self.assertIsNone(body["competition_id"],
                          "two shared competitions is a tie, not an answer")

        status, body = rpc("resolve_import_fixtures", {
            "p_items": [dict(item, competition_hint=label)],
            "p_season_id": season,
        }, token=self.tokens["admin"])
        self.assertEqual(status, 200, body)
        self.assertEqual(body["competition_id"], cup)
        self.assertEqual(body["competition_source"], "hint")

    def test_the_hint_cannot_name_a_competition_the_teams_did_not(self):
        # The shortlist is the whole guard: a model that wrote "Super League of
        # Malawi" onto a graphic of northern-region fixtures must not move them.
        body = self.resolve([
            {"idx": 1, "home_team_raw": self.name(2), "away_team_raw": self.name(3),
             "date": self.dates[1], "competition_hint": "Super League of Malawi"},
        ])
        self.assertEqual(body["competition_id"], self.COMP)

    # ── new, already there, or disagreeing ───────────────────────────────────

    def test_a_fixture_that_is_not_there_is_new(self):
        item = self.one(self.name(2), self.name(3), date=self.dates[1],
                        kickoff="2:30 PM")
        self.assertEqual(item["state"], "new", item["reasons"])
        self.assertEqual(item["confidence"], "green", item["reasons"])
        self.assertEqual(item["home"]["team_id"], self.team_ids[2])
        self.assertEqual(item["away"]["team_id"], self.team_ids[3])
        self.assertEqual(item["kickoff"], "14:30")
        self.assertIsNone(item["existing"])

    def test_a_fixture_already_in_the_list_says_so(self):
        item = self.one(self.name(0), self.name(1), date=self.dates[0],
                        kickoff="2:30 PM", venue_raw=self.venue_name)
        # The commonest case on a top-flight MATCH DAY poster, and NOT an
        # error: the graphic agrees with the database.
        self.assertEqual(item["state"], "existing_agrees", item["reasons"])
        self.assertIn("already_listed", item["reasons"])
        self.assertEqual(item["existing"]["match_id"], self.existing)
        self.assertEqual(item["differs"], [])

    def test_a_disagreement_names_the_fields(self):
        item = self.one(self.name(0), self.name(1), date=self.dates[0],
                        kickoff="3:00 PM", venue_raw=self.venue_name)
        self.assertEqual(item["state"], "existing_differs", item["reasons"])
        self.assertEqual(item["differs"], ["kickoff"])
        self.assertEqual(item["kickoff"], "15:00")
        # What is stored, so the reporter can see both sides of the choice.
        self.assertEqual(item["existing"]["kickoff"], "14:30")
        self.assertEqual(item["confidence"], "yellow")

    def test_a_moved_fixture_disagrees_on_the_date(self):
        item = self.one(self.name(0), self.name(1), date=self.dates[2],
                        kickoff="2:30 PM", venue_raw=self.venue_name)
        self.assertEqual(item["state"], "existing_differs", item["reasons"])
        self.assertEqual(item["differs"], ["date"])

    def test_the_same_teams_the_other_way_round_is_not_a_new_fixture(self):
        # A graphic that lists the winner first would otherwise publish a
        # duplicate the duplicate guard cannot see — it checks the orientation
        # as given.
        item = self.one(self.name(1), self.name(0), date=self.dates[0])
        self.assertEqual(item["state"], "blocked", item["reasons"])
        self.assertIn("reversed_existing", item["reasons"])

    # ── what cannot be resolved ──────────────────────────────────────────────

    def test_a_name_that_matches_nothing_is_blocked(self):
        item = self.one(f"Nowhere United {self.suffix}", self.name(1),
                        date=self.dates[1])
        self.assertEqual(item["state"], "blocked")
        self.assertIn("team_not_found", item["reasons"])
        self.assertEqual(item["confidence"], "red")

    def test_a_team_cannot_play_itself(self):
        item = self.one(self.name(2), self.name(2), date=self.dates[1])
        self.assertEqual(item["state"], "blocked")
        self.assertIn("same_team", item["reasons"])

    def test_a_ground_that_is_not_in_the_table_is_flagged_not_invented(self):
        before = self.venue_count()
        item = self.one(self.name(2), self.name(3), date=self.dates[1],
                        venue_raw=f"Fixtest Ground {self.suffix}")
        self.assertIsNone(item["venue_id"])
        self.assertIn("venue_unknown", item["reasons"])
        self.assertEqual(item["confidence"], "yellow")
        # It still publishes — as a fixture with no ground, exactly as one
        # typed without a ground does.
        self.assertEqual(item["state"], "new")
        self.assertEqual(self.venue_count(), before)

    def test_a_missing_date_is_a_question_not_a_refusal(self):
        item = self.one(self.name(2), self.name(3))
        self.assertEqual(item["state"], "new")
        self.assertEqual(item["confidence"], "yellow")
        self.assertIn("no_date", item["reasons"])
        self.assertIsNone(item["date"])

    def test_a_guessed_kickoff_takes_the_certainty_off_the_row(self):
        item = self.one(self.name(2), self.name(3), date=self.dates[1],
                        kickoff="2:30")
        self.assertEqual(item["kickoff"], "14:30")
        self.assertTrue(item["kickoff_guessed"])
        self.assertEqual(item["confidence"], "yellow")
        self.assertIn("kickoff_guessed", item["reasons"])

    # ── the scope is the authorization ───────────────────────────────────────

    def test_a_reporter_is_offered_no_team_outside_their_competitions(self):
        body = self.resolve([
            {"idx": 1, "home_team_raw": self.name(0), "away_team_raw": self.name(1)},
        ], token=self.tokens["b"])
        item = body["items"][0]
        # 'b' reports MW_SRFA, so MW_NRFA's teams are not merely unmatched —
        # they are not in the pool at all.
        self.assertEqual(item["state"], "blocked", item["reasons"])
        self.assertEqual(item["home_candidates"], [])

    def test_an_inactive_reporter_is_refused(self):
        status, body = rpc("resolve_import_fixtures", {
            "p_items": [], "p_season_id": self.season,
        }, token=self.tokens["inactive"])
        self.assertEqual(status, 403, body)

    def test_anon_is_refused(self):
        status, body = rpc("resolve_import_fixtures", {"p_items": []}, token=None)
        self.assertIn(status, (401, 403, 404), body)

    # ── nothing is created ───────────────────────────────────────────────────

    def test_resolving_a_whole_graphic_writes_nothing(self):
        venues_before = self.venue_count()
        matches_before = len(sb.select(
            "matches", columns="match_id",
            params={"competition_id": f"eq.{self.COMP}",
                    "season_id": f"eq.{self.season}"}, require_secret=True))

        body = self.resolve([
            {"idx": 1, "home_team_raw": self.name(0), "away_team_raw": self.name(1),
             "date": self.dates[0], "kickoff": "2:30 PM",
             "venue_raw": self.venue_name},
            {"idx": 2, "home_team_raw": self.name(2), "away_team_raw": self.name(3),
             "date": self.dates[1], "kickoff": "2:30 PM",
             "venue_raw": f"Fixtest Ground {self.suffix}"},
            {"idx": 3, "home_team_raw": f"Nowhere {self.suffix}",
             "away_team_raw": self.name(1)},
        ])
        self.assertEqual([i["state"] for i in body["items"]],
                         ["existing_agrees", "new", "blocked"])
        self.assertEqual(self.venue_count(), venues_before)
        self.assertEqual(len(sb.select(
            "matches", columns="match_id",
            params={"competition_id": f"eq.{self.COMP}",
                    "season_id": f"eq.{self.season}"}, require_secret=True)),
            matches_before)


if __name__ == "__main__":
    unittest.main()
