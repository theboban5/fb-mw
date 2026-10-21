"""add_match_source (0055): a match's sources are a list.

The property that matters is that adding one never loses another — the score
screen's source box replaces the whole column, and this must not.

    RLS_LIVE=1 python3 -m unittest tests.test_add_match_source_live
"""

import os
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from src import supabase_client as sb  # noqa: E402
from tests import live_support  # noqa: E402
from tests.live_support import call  # noqa: E402


@unittest.skipUnless(live_support.available(), "Supabase credentials not configured")
class AddMatchSourceTest(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        cls.identities = live_support.Identities(prefix="MW_SOURCETEST").setup()
        cls.tokens = cls.identities.tokens
        cls.match = live_support.make_test_match(
            live_support.Identities.COMP_A, cls.identities.suffix)

    @classmethod
    def tearDownClass(cls):
        live_support.drop_test_match(cls.match["match_id"])   # log cascades
        cls.identities.teardown()

    def setUp(self):
        self.set_sources("")

    def set_sources(self, value):
        sb._request("PATCH", "matches", query=f"match_id=eq.{self.match['match_id']}",
                    body={"source_ref": value},
                    headers={"Prefer": "return=minimal"}, require_secret=True)

    def sources(self):
        return sb.select("matches", params={"match_id": f"eq.{self.match['match_id']}"},
                         require_secret=True)[0]["source_ref"]

    def add(self, source, label="a"):
        return call("rpc/add_match_source", token=self.tokens.get(label),
                    method="POST", body={"p_match_id": self.match["match_id"],
                                         "p_source": source})

    def test_a_source_is_appended_beside_the_ones_already_there(self):
        self.set_sources("https://facebook.com/share/p/1")
        status, body = self.add("https://silverstrikers.mw/report")
        self.assertEqual(status, 200, body)
        self.assertEqual(self.sources(),
                         "https://facebook.com/share/p/1, https://silverstrikers.mw/report")

    def test_the_first_source_is_the_list(self):
        self.add("https://silverstrikers.mw/report")
        self.assertEqual(self.sources(), "https://silverstrikers.mw/report")

    def test_the_same_source_twice_lists_once(self):
        self.add("https://a.mw/x")
        self.add("https://a.mw/x")
        self.assertEqual(self.sources(), "https://a.mw/x")

    def test_a_prefix_of_a_listed_source_is_a_different_source(self):
        self.set_sources("https://a.mw/report-2")
        self.add("https://a.mw/report")
        self.assertEqual(self.sources(), "https://a.mw/report-2, https://a.mw/report")

    def test_a_source_that_would_not_fit_is_refused_whole(self):
        self.set_sources("x" * 480)
        status, body = self.add("https://silverstrikers.mw/a-long-report-url")
        self.assertNotEqual(status, 200)
        self.assertIn("as many sources as fit", str(body))
        self.assertEqual(self.sources(), "x" * 480)

    def test_it_is_logged(self):
        self.add("https://a.mw/x")
        log = sb.select("match_change_log",
                        params={"match_id": f"eq.{self.match['match_id']}"},
                        order="id.desc", require_secret=True)
        self.assertEqual(log[0]["new_values"], {"source_ref": "https://a.mw/x"})
        self.assertEqual(log[0]["source"], "single")

    def test_another_competitions_reporter_cannot(self):
        status, _ = self.add("https://a.mw/x", label="b")
        self.assertNotEqual(status, 200)
        self.assertEqual(self.sources(), "")

    def test_anon_is_refused_outright(self):
        status, _ = self.add("https://a.mw/x", label=None)
        self.assertIn(status, (401, 403))


if __name__ == "__main__":
    unittest.main()
