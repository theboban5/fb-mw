"""0061 and 0062 over the network, with real identities.

  * skip_match_officials records "published without match officials" only for
    a match the caller may report and only while its officials are blank —
    on a throwaway placeholder fixture, never a real match.
  * stats_match_facts is any active reporter's: anon is refused, an inactive
    reporter sees nothing, and its counts agree with the raw tables.

Opt-in, like every *_live test:

    RLS_LIVE=1 python3 -m unittest tests.test_officials_stats_live
"""

import os
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from src import supabase_client as sb  # noqa: E402
from tests import live_support  # noqa: E402
from tests.live_support import call as _call  # noqa: E402


@unittest.skipUnless(live_support.available(), "Supabase credentials not configured")
class OfficialsSkipTest(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        cls.identities = live_support.Identities(prefix="MW_OFFTEST").setup()
        cls.tokens = cls.identities.tokens
        cls.match = live_support.make_test_match(
            live_support.Identities.COMP_A, cls.identities.suffix)

    @classmethod
    def tearDownClass(cls):
        # officials_skips cascades from matches.
        live_support.drop_test_match(cls.match["match_id"])
        cls.identities.teardown()

    def skip(self, label, channel="single", ids=None):
        return _call("rpc/skip_match_officials", token=self.tokens[label],
                     method="POST",
                     body={"p_match_ids": ids or [self.match["match_id"]],
                           "p_channel": channel})

    def skips(self):
        return sb.select("officials_skips",
                         params={"match_id": f"eq.{self.match['match_id']}"},
                         require_secret=True)

    def test_flow(self):
        # One test, in order, because each step is the state the next needs.
        status, body = _call("rpc/skip_match_officials", token=None, method="POST",
                             body={"p_match_ids": [self.match["match_id"]],
                                   "p_channel": "single"})
        self.assertGreaterEqual(status, 400, f"anon recorded a skip: {body!r}")

        # Assigned elsewhere: ignored quietly, never an error after a publish.
        status, body = self.skip("b")
        self.assertEqual((status, body), (200, 0))
        self.assertEqual(self.skips(), [])

        status, body = self.skip("a", channel="nonsense")
        self.assertGreaterEqual(status, 400)

        status, body = self.skip("a", channel="grid")
        self.assertEqual((status, body), (200, 1))
        [row] = self.skips()
        self.assertEqual(row["reporter_id"], self.identities.ids["a"])
        self.assertEqual(row["channel"], "grid")

        # A second answer replaces the first rather than adding a row.
        status, body = self.skip("a", channel="single")
        self.assertEqual((status, body), (200, 1))
        [row] = self.skips()
        self.assertEqual(row["channel"], "single")

        # Once the match has a referee, "published without officials" is no
        # longer true and the RPC refuses to say it.
        sb._request("PATCH", "matches",
                    query=f"match_id=eq.{self.match['match_id']}",
                    body={"referee": "Live Test Referee"},
                    headers={"Prefer": "return=minimal"}, require_secret=True)
        status, body = self.skip("a")
        self.assertEqual((status, body), (200, 0))


@unittest.skipUnless(live_support.available(), "Supabase credentials not configured")
class StatsViewTest(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        cls.identities = live_support.Identities(prefix="MW_STATTEST").setup()
        cls.tokens = cls.identities.tokens

    @classmethod
    def tearDownClass(cls):
        cls.identities.teardown()

    def test_access(self):
        status, body = _call("stats_match_facts?select=match_id&limit=1", token=None)
        self.assertGreaterEqual(status, 400, f"anon read stats: {body!r}")
        status, body = _call("stats_match_facts?select=match_id&limit=1",
                             token=self.tokens["inactive"])
        self.assertEqual((status, body), (200, []))
        # An ordinary reporter, assigned to one competition, sees all of them:
        # everything here is public on the site already.
        status, body = _call("stats_match_facts?select=competition_id",
                             token=self.tokens["a"])
        self.assertEqual(status, 200)
        self.assertGreater(len({r["competition_id"] for r in body}), 1)

    def test_counts_agree_with_the_raw_tables(self):
        """One competition-season, recomputed from matches and lineups: played
        (never awarded, never placeholder) matches, goals, and cards counted
        the way src/lineups.cards_html draws them."""
        season = [s for s in sb.select("seasons", columns="season_id,status")
                  if s["status"] == "active"][0]["season_id"]
        comp = "MW_SL"
        matches = [m for m in sb.select(
            "matches", columns="*",
            params={"competition_id": f"eq.{comp}", "season_id": f"eq.{season}"},
            require_secret=True)
            if m["status"] == "played" and m["source_type"] != "placeholder"
            and m["home_goals"] is not None and m["away_goals"] is not None]
        ids = {m["match_id"] for m in matches}
        lineups = [l for l in sb.select(
            "lineups", columns="match_id,yellow_card,yellow_red_card,red_card",
            require_secret=True) if l["match_id"] in ids]

        status, rows = _call(
            f"stats_match_facts?select=*&competition_id=eq.{comp}"
            f"&season_id=eq.{season}", token=self.tokens["a"])
        self.assertEqual(status, 200)
        self.assertEqual({r["match_id"] for r in rows}, ids)
        self.assertEqual(sum(r["home_goals"] + r["away_goals"] for r in rows),
                         sum(m["home_goals"] + m["away_goals"] for m in matches))
        self.assertEqual(
            sum(r["home_yellow"] + r["away_yellow"] for r in rows),
            sum(1 for l in lineups if l["yellow_card"] and not l["yellow_red_card"]))
        self.assertEqual(
            sum(r["home_red"] + r["away_red"] + r["home_second_yellow"]
                + r["away_second_yellow"] for r in rows),
            sum(1 for l in lineups if l["red_card"] or l["yellow_red_card"]))


if __name__ == "__main__":
    unittest.main()
