"""create_fixture and create_league: the two write paths added in 0008.

These attack the RPCs directly rather than through the UI, for the same reason
test_reporting_live does — the function is the security boundary, and the
client is not trusted.

    RLS_LIVE=1 python3 -m unittest tests.test_entry_live

WHAT THESE TESTS CREATE, AND WHY IT IS SAFE. create_league mints real
competitions, clubs, teams and entries, and unlike a match there is no
`source_type='placeholder'` escape hatch that would make a leaked competition
render nowhere. So:

  * every id is namespaced MW_ZZTEST*, which no real competition uses;
  * every club name starts "ZZ ";
  * teardown deletes in foreign-key order, and TeardownAuditTest (named to run
    last) fails loudly if anything survived — otherwise a failed teardown would
    put a fake league on everyleague.co at the next build and nothing would
    have complained.

The fixture tests run inside a real competition using teams genuinely entered
in it, because validate.py check 3 — and the composite foreign key behind it —
will not accept anything else.
"""

import os
import sys
import unittest
import uuid

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from src import supabase_client as sb  # noqa: E402
from tests import live_support  # noqa: E402
from tests.live_support import call  # noqa: E402


def rpc(name, body, *, token):
    """(status, body), never an exception — the shape live_support.call uses."""
    return call(f"rpc/{name}", token=token, method="POST", body=body)


def message(body):
    """The human sentence out of a PostgREST error payload."""
    if isinstance(body, dict):
        return body.get("message") or body.get("hint") or str(body)
    return str(body)


def delete(table, query):
    sb._request("DELETE", table, query=query,
                headers={"Prefer": "return=minimal"}, require_secret=True)


CREATED_COMPETITIONS = []


def drop_competition(competition_id):
    """Delete a test competition and everything minted underneath it.

    Order matters: matches reference entries (composite FK), entries reference
    teams, teams reference clubs. A club is removed only when nothing else
    points at it — create_league REUSES an existing club when the name matches,
    and deleting a real club because a test borrowed it would be far worse than
    leaving a stray row.
    """
    teams = [e["team_id"] for e in sb.select(
        "entries", columns="team_id",
        params={"competition_id": f"eq.{competition_id}"}, require_secret=True)]

    for match in sb.select("matches", columns="match_id",
                           params={"competition_id": f"eq.{competition_id}"},
                           require_secret=True):
        delete("match_change_log", f"match_id=eq.{match['match_id']}")
    delete("matches", f"competition_id=eq.{competition_id}")
    delete("entries", f"competition_id=eq.{competition_id}")
    delete("competition_seasons", f"competition_id=eq.{competition_id}")
    delete("competitions", f"competition_id=eq.{competition_id}")

    for team_id in teams:
        if sb.select("entries", columns="entry_id",
                     params={"team_id": f"eq.{team_id}"}, require_secret=True):
            continue
        club = sb.select("teams", columns="club_id",
                         params={"team_id": f"eq.{team_id}"}, require_secret=True)
        delete("teams", f"team_id=eq.{team_id}")
        if not club:
            continue
        club_id = club[0]["club_id"]
        if not sb.select("teams", columns="team_id",
                         params={"club_id": f"eq.{club_id}"}, require_secret=True):
            delete("clubs", f"club_id=eq.{club_id}")


@unittest.skipUnless(live_support.available(), "Supabase credentials not configured")
class CreateFixtureTest(unittest.TestCase):
    """Adding a fixture to a competition you cover."""

    COMP = live_support.Identities.COMP_A   # MW_NRFA — 'a' is assigned to it

    @classmethod
    def setUpClass(cls):
        cls.identities = live_support.Identities(prefix="MW_FIXTEST").setup()
        cls.tokens = cls.identities.tokens
        entries = sb.select(
            "entries", columns="team_id,season_id",
            params={"competition_id": f"eq.{cls.COMP}"},
            order="ord.asc", require_secret=True)
        if len(entries) < 3:
            raise unittest.SkipTest(f"{cls.COMP} has too few entries")
        cls.teams = [e["team_id"] for e in entries]
        cls.D = live_support.season_dates(14)
        cls.made = []

    @classmethod
    def tearDownClass(cls):
        for match_id in cls.made:
            delete("match_change_log", f"match_id=eq.{match_id}")
            live_support.drop_test_match(match_id)
        cls.identities.teardown()

    def add(self, token, **kwargs):
        body = {"p_competition_id": self.COMP}
        body.update(kwargs)
        return rpc("create_fixture", body, token=token)

    def add_ok(self, token, **kwargs):
        status, body = self.add(token, **kwargs)
        self.assertEqual(status, 200, body)
        self.made.append(body[0]["match_id"])
        return body[0]

    # ── authorization ────────────────────────────────────────────────────────

    def test_assigned_reporter_can_add_a_fixture(self):
        row = self.add_ok(self.tokens["a"],
                          p_home_team_id=self.teams[0],
                          p_away_team_id=self.teams[1],
                          p_date=self.D[0], p_kickoff="15:00", p_matchday=1)
        self.assertEqual(row["status"], "scheduled")
        self.assertIsNone(row["home_goals"])
        self.assertIsNone(row["away_goals"])
        self.assertEqual(row["stage"], "md_1")
        self.assertEqual(row["kickoff"], "15:00")
        self.assertTrue(row["match_id"].startswith(f"{self.COMP}_"), row["match_id"])

    def test_reporter_for_another_competition_cannot(self):
        status, body = self.add(self.tokens["b"], p_home_team_id=self.teams[0],
                                p_away_team_id=self.teams[1], p_date=self.D[1])
        self.assertEqual(status, 403, body)
        self.assertIn("not assigned", message(body))

    def test_inactive_reporter_cannot(self):
        status, body = self.add(self.tokens["inactive"],
                                p_home_team_id=self.teams[0],
                                p_away_team_id=self.teams[1], p_date=self.D[2])
        self.assertEqual(status, 403, body)
        self.assertIn("inactive", message(body))

    def test_anon_cannot(self):
        status, body = self.add(None, p_home_team_id=self.teams[0],
                                p_away_team_id=self.teams[1], p_date=self.D[3])
        self.assertGreaterEqual(status, 400, body)

    def test_admin_may_add_to_any_competition(self):
        row = self.add_ok(self.tokens["admin"], p_home_team_id=self.teams[0],
                          p_away_team_id=self.teams[2], p_date=self.D[4])
        self.assertEqual(row["competition_id"], self.COMP)

    # ── validation, mirroring validate.py ────────────────────────────────────

    def test_rejects_a_team_playing_itself(self):
        """validate.py check 4."""
        status, body = self.add(self.tokens["a"], p_home_team_id=self.teams[0],
                                p_away_team_id=self.teams[0], p_date=self.D[5])
        self.assertGreaterEqual(status, 400, body)
        self.assertIn("cannot play itself", message(body))

    def test_rejects_a_team_not_entered_in_the_competition(self):
        """validate.py check 3 — the rule that would otherwise fail the build."""
        other = sb.select(
            "entries", columns="team_id",
            params={"competition_id": f"eq.{live_support.Identities.COMP_B}"},
            order="ord.asc", require_secret=True)
        outsider = next((e["team_id"] for e in other
                         if e["team_id"] not in self.teams), None)
        if outsider is None:
            self.skipTest("no team outside the competition to test with")
        status, body = self.add(self.tokens["a"], p_home_team_id=self.teams[0],
                                p_away_team_id=outsider, p_date=self.D[6])
        self.assertGreaterEqual(status, 400, body)
        self.assertIn("not entered", message(body))

    def test_rejects_a_malformed_kickoff(self):
        status, body = self.add(self.tokens["a"], p_home_team_id=self.teams[0],
                                p_away_team_id=self.teams[1],
                                p_date=self.D[7], p_kickoff="3pm")
        self.assertGreaterEqual(status, 400, body)
        self.assertIn("kickoff", message(body))

    def test_rejects_the_same_fixture_twice_on_one_day(self):
        self.add_ok(self.tokens["a"], p_home_team_id=self.teams[1],
                    p_away_team_id=self.teams[2], p_date=self.D[8])
        status, body = self.add(self.tokens["a"], p_home_team_id=self.teams[1],
                                p_away_team_id=self.teams[2], p_date=self.D[8])
        self.assertGreaterEqual(status, 400, body)
        self.assertIn("already in the list", message(body))

    def test_a_fixture_with_no_date_is_allowed(self):
        """876 real rows have no date: a fixture list often precedes a calendar."""
        row = self.add_ok(self.tokens["a"], p_home_team_id=self.teams[2],
                          p_away_team_id=self.teams[0])
        self.assertIsNone(row["date"])

    def test_ids_do_not_collide(self):
        a = self.add_ok(self.tokens["a"], p_home_team_id=self.teams[0],
                        p_away_team_id=self.teams[1], p_date=self.D[10])
        b = self.add_ok(self.tokens["a"], p_home_team_id=self.teams[0],
                        p_away_team_id=self.teams[1], p_date=self.D[11])
        self.assertNotEqual(a["match_id"], b["match_id"])

    def test_rejects_a_date_outside_the_season(self):
        """validate.py check 6, and the one 0008 missed.

        A mistyped year is a single keystroke, and the row it produces fails
        every build until someone finds it — which blocks everyone else's
        results, not just this one.
        """
        status, body = self.add(self.tokens["a"], p_home_team_id=self.teams[0],
                                p_away_team_id=self.teams[1],
                                p_date=live_support.out_of_season_date())
        self.assertGreaterEqual(status, 400, body)
        self.assertIn("outside the", message(body))

    def test_a_new_fixture_is_immediately_reportable(self):
        """The whole point: add it, then score it through the normal path."""
        row = self.add_ok(self.tokens["a"], p_home_team_id=self.teams[2],
                          p_away_team_id=self.teams[1], p_date=self.D[9])
        status, published = rpc("submit_match_report", {
            "p_match_id": row["match_id"], "p_home_score": 2,
            "p_away_score": 0, "p_status": "played",
            "p_source_ref": "https://example.com/post/1",
        }, token=self.tokens["a"])
        self.assertEqual(status, 200, published)
        self.assertEqual(published[0]["home_goals"], 2)
        self.assertEqual(published[0]["source_ref"], "https://example.com/post/1")
        self.assertEqual(published[0]["confidence"], "unconfirmed")


@unittest.skipUnless(live_support.available(), "Supabase credentials not configured")
class SourceRefTest(unittest.TestCase):
    """Where a result came from, recorded on the result."""

    @classmethod
    def setUpClass(cls):
        cls.identities = live_support.Identities(prefix="MW_SRCTEST").setup()
        cls.tokens = cls.identities.tokens
        cls.match = live_support.make_test_match(
            live_support.Identities.COMP_A, cls.identities.suffix)

    @classmethod
    def tearDownClass(cls):
        delete("match_change_log", f"match_id=eq.{cls.match['match_id']}")
        live_support.drop_test_match(cls.match["match_id"])
        cls.identities.teardown()

    def publish(self, **kwargs):
        body = {"p_match_id": self.match["match_id"], "p_home_score": 1,
                "p_away_score": 1, "p_status": "played"}
        body.update(kwargs)
        status, rows = rpc("submit_match_report", body, token=self.tokens["a"])
        self.assertEqual(status, 200, rows)
        return rows[0]

    def test_stores_a_link(self):
        row = self.publish(p_source_ref="https://facebook.com/x/posts/1")
        self.assertEqual(row["source_ref"], "https://facebook.com/x/posts/1")

    def test_stores_free_text(self):
        row = self.publish(p_source_ref="Phoned in by the home club secretary")
        self.assertEqual(row["source_ref"], "Phoned in by the home club secretary")

    def test_trims_surrounding_whitespace(self):
        row = self.publish(p_source_ref="  https://example.com/a  ")
        self.assertEqual(row["source_ref"], "https://example.com/a")

    def test_source_type_stays_reporter(self):
        """source_type records HOW the row got here, and a reporter still typed
        it. The unconfirmed asterisk on the public site keys off this."""
        row = self.publish(p_source_ref="https://facebook.com/x/posts/2")
        self.assertEqual(row["source_type"], "reporter")

    def test_omitting_the_source_keeps_the_one_already_recorded(self):
        self.publish(p_source_ref="https://facebook.com/x/posts/3")
        row = self.publish(p_home_score=2)
        self.assertEqual(row["source_ref"], "https://facebook.com/x/posts/3")

    def test_a_four_argument_call_still_works(self):
        """A phone running a cached copy of the old app.js sends four
        arguments; dropping the old overload must not break it."""
        status, rows = rpc("submit_match_report", {
            "p_match_id": self.match["match_id"], "p_home_score": 3,
            "p_away_score": 0, "p_status": "played",
        }, token=self.tokens["a"])
        self.assertEqual(status, 200, rows)
        self.assertEqual(rows[0]["home_goals"], 3)

    def test_an_overlong_source_is_truncated_not_rejected(self):
        row = self.publish(p_source_ref="x" * 900)
        self.assertEqual(len(row["source_ref"]), 500)

    def test_the_source_change_is_audited(self):
        self.publish(p_source_ref="https://example.com/first")
        self.publish(p_source_ref="https://example.com/second")
        log = sb.select("match_change_log", columns="new_values",
                        params={"match_id": f"eq.{self.match['match_id']}"},
                        order="id.desc", require_secret=True)
        self.assertEqual(log[0]["new_values"]["source_ref"],
                         "https://example.com/second")


@unittest.skipUnless(live_support.available(), "Supabase credentials not configured")
class RescheduleMatchTest(unittest.TestCase):
    """Moving a fixture — and, just as much, what it must refuse to move.

    reschedule_match exists as a SEPARATE function rather than as new
    parameters on submit_match_report, so the narrow-update guarantee is
    tested twice over: publishing still cannot touch the date, and
    rescheduling still cannot touch anything else.
    """

    @classmethod
    def setUpClass(cls):
        cls.identities = live_support.Identities(prefix="MW_RSTEST").setup()
        cls.tokens = cls.identities.tokens
        cls.D = live_support.season_dates(6)
        cls.match = live_support.make_test_match(
            live_support.Identities.COMP_A, cls.identities.suffix)

    @classmethod
    def tearDownClass(cls):
        delete("match_change_log", f"match_id=eq.{cls.match['match_id']}")
        live_support.drop_test_match(cls.match["match_id"])
        cls.identities.teardown()

    def move(self, token, **kwargs):
        body = {"p_match_id": self.match["match_id"]}
        body.update(kwargs)
        return rpc("reschedule_match", body, token=token)

    def row(self):
        return sb.select("matches",
                         params={"match_id": f"eq.{self.match['match_id']}"},
                         require_secret=True)[0]

    def test_an_assigned_reporter_moves_a_fixture(self):
        status, rows = self.move(self.tokens["a"], p_date=self.D[0],
                                 p_kickoff="16:00")
        self.assertEqual(status, 200, rows)
        self.assertEqual(rows[0]["date"], self.D[0])
        self.assertEqual(rows[0]["kickoff"], "16:00")

    def test_the_date_can_be_cleared(self):
        """A postponed match with no new day yet is a fixture with no date —
        876 rows in the real data look like that."""
        self.move(self.tokens["a"], p_date=self.D[1], p_kickoff="15:00")
        status, rows = self.move(self.tokens["a"], p_date=None, p_kickoff="")
        self.assertEqual(status, 200, rows)
        self.assertIsNone(rows[0]["date"])
        self.assertEqual(rows[0]["kickoff"], "")

    def test_it_moves_nothing_else(self):
        """The whole reason this is its own function."""
        before = self.row()
        status, _ = self.move(self.tokens["a"], p_date=self.D[2],
                              p_kickoff="14:00")
        self.assertEqual(status, 200)
        after = self.row()
        for column in ("home_team_id", "away_team_id", "competition_id",
                       "season_id", "home_goals", "away_goals", "status",
                       "venue_id", "public_id", "stage"):
            self.assertEqual(before[column], after[column], column)

    def test_publishing_still_cannot_move_a_fixture(self):
        """The guarantee 0003 made, still true now that moving is possible."""
        self.move(self.tokens["a"], p_date=self.D[3], p_kickoff="15:00")
        status, _ = rpc("submit_match_report", {
            "p_match_id": self.match["match_id"], "p_home_score": 1,
            "p_away_score": 0, "p_status": "played"}, token=self.tokens["a"])
        self.assertEqual(status, 200)
        self.assertEqual(self.row()["date"], self.D[3])

    def test_rejects_a_date_outside_the_season(self):
        status, body = self.move(self.tokens["a"],
                                 p_date=live_support.out_of_season_date())
        self.assertGreaterEqual(status, 400, body)
        self.assertIn("outside the", message(body))

    def test_rejects_a_malformed_kickoff(self):
        status, body = self.move(self.tokens["a"], p_date=self.D[4],
                                 p_kickoff="half three")
        self.assertGreaterEqual(status, 400, body)
        self.assertIn("kickoff", message(body))

    def test_a_reporter_for_another_competition_cannot(self):
        status, body = self.move(self.tokens["b"], p_date=self.D[5])
        self.assertEqual(status, 403, body)
        self.assertIn("not assigned", message(body))

    def test_an_inactive_reporter_cannot(self):
        status, body = self.move(self.tokens["inactive"], p_date=self.D[5])
        self.assertEqual(status, 403, body)
        self.assertIn("inactive", message(body))

    def test_anon_cannot(self):
        status, body = self.move(None, p_date=self.D[5])
        self.assertGreaterEqual(status, 400, body)

    def test_the_move_is_audited(self):
        self.move(self.tokens["a"], p_date=self.D[0], p_kickoff="15:00")
        self.move(self.tokens["a"], p_date=self.D[1], p_kickoff="17:30")
        log = sb.select("match_change_log", columns="old_values,new_values",
                        params={"match_id": f"eq.{self.match['match_id']}"},
                        order="id.desc", require_secret=True)
        self.assertEqual(log[0]["new_values"],
                         {"date": self.D[1], "kickoff": "17:30"})
        self.assertEqual(log[0]["old_values"],
                         {"date": self.D[0], "kickoff": "15:00"})

    def test_an_unchanged_move_adds_no_log_row(self):
        self.move(self.tokens["a"], p_date=self.D[2], p_kickoff="15:00")
        before = len(sb.select("match_change_log", columns="id",
                               params={"match_id": f"eq.{self.match['match_id']}"},
                               require_secret=True))
        status, rows = self.move(self.tokens["a"], p_date=self.D[2],
                                 p_kickoff="15:00")
        self.assertEqual(status, 200, rows)
        after = len(sb.select("match_change_log", columns="id",
                              params={"match_id": f"eq.{self.match['match_id']}"},
                              require_secret=True))
        self.assertEqual(before, after)


@unittest.skipUnless(live_support.available(), "Supabase credentials not configured")
class CreateLeagueTest(unittest.TestCase):
    """Creating a competition — admin only, because it mints permanent ids."""

    @classmethod
    def setUpClass(cls):
        cls.identities = live_support.Identities(prefix="MW_LGTEST").setup()
        cls.tokens = cls.identities.tokens
        cls.suffix = uuid.uuid4().hex[:4].upper()
        cls.D = live_support.season_dates(4)

    @classmethod
    def tearDownClass(cls):
        for competition_id in list(CREATED_COMPETITIONS):
            drop_competition(competition_id)
        CREATED_COMPETITIONS.clear()
        cls.identities.teardown()

    def create(self, token, code, teams, **kwargs):
        body = {"p_name": f"ZZ Test League {code}",
                "p_short_code": f"ZZTEST{self.suffix}{code}",
                "p_teams": teams}
        body.update(kwargs)
        status, competition_id = rpc("create_league", body, token=token)
        if status == 200 and isinstance(competition_id, str):
            CREATED_COMPETITIONS.append(competition_id)
        return status, competition_id

    def create_ok(self, code, teams, **kwargs):
        status, competition_id = self.create(self.tokens["admin"], code,
                                             teams, **kwargs)
        self.assertEqual(status, 200, competition_id)
        return competition_id

    def entries_of(self, competition_id):
        return sb.select("entries", columns="team_id,entry_id",
                         params={"competition_id": f"eq.{competition_id}"},
                         require_secret=True)

    def test_admin_creates_a_whole_reportable_league(self):
        names = [f"ZZ Alpha {self.suffix}", f"ZZ Bravo {self.suffix}",
                 f"ZZ Charlie {self.suffix}"]
        competition_id = self.create_ok("A", names, p_tier=4, p_region="SRFA")
        self.assertTrue(competition_id.startswith("MW_ZZTEST"), competition_id)

        entries = self.entries_of(competition_id)
        self.assertEqual(len(entries), 3)

        season = sb.select("competition_seasons", columns="teams_count",
                           params={"competition_id": f"eq.{competition_id}"},
                           require_secret=True)
        self.assertEqual(season[0]["teams_count"], 3)

        # And it can hold a fixture straight away — that is the definition of
        # "created", and the reason this is one call rather than three screens.
        teams = [e["team_id"] for e in entries]
        status, rows = rpc("create_fixture", {
            "p_competition_id": competition_id, "p_home_team_id": teams[0],
            "p_away_team_id": teams[1], "p_date": self.D[0],
        }, token=self.tokens["admin"])
        self.assertEqual(status, 200, rows)
        self.assertEqual(rows[0]["status"], "scheduled")

    def test_a_plain_reporter_cannot(self):
        status, body = self.create(self.tokens["a"], "B",
                                   [f"ZZ Delta {self.suffix}",
                                    f"ZZ Echo {self.suffix}"])
        self.assertEqual(status, 403, body)
        self.assertIn("administrator", message(body))

    def test_anon_cannot(self):
        status, body = self.create(None, "C", ["ZZ Foxtrot", "ZZ Golf"])
        self.assertGreaterEqual(status, 400, body)

    def test_duplicate_names_in_the_pasted_list_are_collapsed(self):
        name = f"ZZ Hotel {self.suffix}"
        competition_id = self.create_ok(
            "D", [name, f"ZZ India {self.suffix}", name, "   "])
        self.assertEqual(len(self.entries_of(competition_id)), 2)

    def test_an_existing_club_is_reused_not_duplicated(self):
        """A club that already exists keeps its id. Minting a second one would
        split that club's identity across the site permanently."""
        existing = sb.select("clubs", columns="club_id,name",
                             params={"club_id": "eq.MW_BULL"}, require_secret=True)
        if not existing:
            self.skipTest("MW_BULL not present")
        competition_id = self.create_ok(
            "E", [existing[0]["name"], f"ZZ Juliet {self.suffix}"])
        rows = self.entries_of(competition_id)
        self.assertTrue(any(r["team_id"].startswith("MW_BULL") for r in rows),
                        f"expected a MW_BULL team, got {rows}")

    def test_fewer_than_two_teams_creates_nothing(self):
        """The competition and season rows inserted before the team loop must
        go too — a competition that cannot hold a fixture is not a thing to
        have created."""
        code = f"ZZTEST{self.suffix}F"
        status, body = rpc("create_league", {
            "p_name": "ZZ Lonely", "p_short_code": code,
            "p_teams": [f"ZZ Kilo {self.suffix}"]}, token=self.tokens["admin"])
        self.assertGreaterEqual(status, 400, body)
        self.assertIn("at least two", message(body))
        left = sb.select("competitions", columns="competition_id",
                         params={"competition_id": f"eq.MW_{code}"},
                         require_secret=True)
        self.assertEqual(left, [], "the failed create left a competition behind")

    def test_rejects_a_duplicate_competition_id(self):
        names = [f"ZZ Lima {self.suffix}", f"ZZ Mike {self.suffix}"]
        self.create_ok("G", names)
        status, body = self.create(self.tokens["admin"], "G", names)
        self.assertGreaterEqual(status, 400, body)
        self.assertIn("already exists", message(body))

    def test_rejects_an_invalid_age_group(self):
        status, body = self.create(self.tokens["admin"], "H",
                                   [f"ZZ November {self.suffix}",
                                    f"ZZ Oscar {self.suffix}"],
                                   p_age_group="u13")
        self.assertGreaterEqual(status, 400, body)
        self.assertIn("age group", message(body))

    def test_rejects_a_missing_short_code(self):
        status, body = rpc("create_league", {
            "p_name": "ZZ No Code", "p_short_code": "  ",
            "p_teams": ["ZZ Romeo", "ZZ Sierra"]}, token=self.tokens["admin"])
        self.assertGreaterEqual(status, 400, body)
        self.assertIn("short code", message(body))

    def test_a_cup_demands_a_round_on_its_fixtures(self):
        """validate.py check 7: stage vocabulary depends on competitions.type."""
        competition_id = self.create_ok(
            "I", [f"ZZ Papa {self.suffix}", f"ZZ Quebec {self.suffix}"],
            p_type="cup")
        teams = [e["team_id"] for e in self.entries_of(competition_id)]

        status, body = rpc("create_fixture", {
            "p_competition_id": competition_id, "p_home_team_id": teams[0],
            "p_away_team_id": teams[1], "p_date": self.D[1],
        }, token=self.tokens["admin"])
        self.assertGreaterEqual(status, 400, body)
        self.assertIn("round", message(body))

        status, rows = rpc("create_fixture", {
            "p_competition_id": competition_id, "p_home_team_id": teams[0],
            "p_away_team_id": teams[1], "p_date": self.D[1], "p_stage": "SF",
        }, token=self.tokens["admin"])
        self.assertEqual(status, 200, rows)
        self.assertEqual(rows[0]["stage"], "sf")


@unittest.skipUnless(live_support.available(), "Supabase credentials not configured")
class TeardownAuditTest(unittest.TestCase):
    """Named to sort last. Proves the test competitions really are gone.

    Without this, a failed teardown would put a fake league on everyleague.co
    at the next build and nothing would have complained.
    """

    def test_zz_no_test_competitions_survive(self):
        left = sb.select("competitions", columns="competition_id",
                         params={"competition_id": "like.MW_ZZTEST*"},
                         require_secret=True)
        self.assertEqual(left, [], f"test competitions left behind: {left}")

    def test_zz_no_test_clubs_survive(self):
        left = sb.select("clubs", columns="club_id,name",
                         params={"name": "like.ZZ *"}, require_secret=True)
        self.assertEqual(left, [], f"test clubs left behind: {left}")

    def test_zz_no_test_matches_survive(self):
        left = sb.select("matches", columns="match_id",
                         params={"match_id": "like.MW_ZZTEST*"},
                         require_secret=True)
        self.assertEqual(left, [], f"test matches left behind: {left}")


if __name__ == "__main__":
    unittest.main()
