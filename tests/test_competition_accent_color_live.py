"""set_competition_accent_color (0040), exercised over the network with real
identities.

Same access shape as set_competition_level (test_ops_compare_live.py):
anon and an ordinary reporter must both be refused, checked by reading the
row back rather than trusting the HTTP status alone, since an RLS-shaped
refusal can look like a success. Admin succeeds and round-trips. A value
that is not a 6-digit hex colour is rejected; a blank string clears it.

It reads and writes one real competition's accent_color and restores it
afterwards, so it is opt-in:

    RLS_LIVE=1 python3 -m unittest tests.test_competition_accent_color_live
"""

import os
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from tests import live_support  # noqa: E402
from tests.live_support import call as _call  # noqa: E402

COMP = "MW_SL"


@unittest.skipUnless(live_support.available(), "Supabase credentials not configured")
class CompetitionAccentColorTest(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        cls.identities = live_support.Identities(prefix="MW_ACCTEST").setup()
        cls.tokens = cls.identities.tokens

        rows = cls.admin_rows(
            f"competitions?competition_id=eq.{COMP}&select=accent_color")
        cls.original_color = rows[0]["accent_color"]

    @classmethod
    def tearDownClass(cls):
        _call("rpc/set_competition_accent_color", token=cls.tokens["admin"],
              method="POST",
              body={"p_competition_id": COMP,
                    "p_accent_color": cls.original_color or ""})
        cls.identities.teardown()

    @classmethod
    def admin_rows(cls, path):
        status, body = _call(path, token=cls.identities.tokens["admin"])
        assert status == 200, f"admin was refused {path}: {body}"
        return body

    def current_color(self):
        rows = self.admin_rows(
            f"competitions?competition_id=eq.{COMP}&select=accent_color")
        return rows[0]["accent_color"]

    def test_anon_is_refused(self):
        status, body = _call("rpc/set_competition_accent_color", token=None,
                             method="POST",
                             body={"p_competition_id": COMP,
                                   "p_accent_color": "#112233"})
        self.assertGreaterEqual(status, 400, body)

    def test_reporter_is_refused_and_value_is_unchanged(self):
        before = self.current_color()
        status, body = _call("rpc/set_competition_accent_color",
                             token=self.tokens["a"], method="POST",
                             body={"p_competition_id": COMP,
                                   "p_accent_color": "#112233"})
        self.assertGreaterEqual(status, 400,
                                f"a reporter set a competition's accent colour: {body!r}")
        self.assertEqual(self.current_color(), before,
                         "the competition's accent colour changed during the test")

    def test_admin_sets_and_reads_back_a_colour(self):
        status, body = _call("rpc/set_competition_accent_color",
                             token=self.tokens["admin"], method="POST",
                             body={"p_competition_id": COMP,
                                   "p_accent_color": "#3fb37a"})
        self.assertEqual(status, 200, body)
        self.assertEqual(self.current_color(), "#3fb37a")

    def test_admin_clears_a_colour_with_a_blank_string(self):
        _call("rpc/set_competition_accent_color", token=self.tokens["admin"],
              method="POST",
              body={"p_competition_id": COMP, "p_accent_color": "#3fb37a"})
        status, body = _call("rpc/set_competition_accent_color",
                             token=self.tokens["admin"], method="POST",
                             body={"p_competition_id": COMP,
                                   "p_accent_color": ""})
        self.assertEqual(status, 200, body)
        self.assertIsNone(self.current_color())

    def test_a_non_hex_value_is_rejected(self):
        status, body = _call("rpc/set_competition_accent_color",
                             token=self.tokens["admin"], method="POST",
                             body={"p_competition_id": COMP,
                                   "p_accent_color": "not-a-colour"})
        self.assertGreaterEqual(status, 400, body)

    def test_an_unknown_competition_is_rejected(self):
        status, body = _call("rpc/set_competition_accent_color",
                             token=self.tokens["admin"], method="POST",
                             body={"p_competition_id": "MW_NOPE",
                                   "p_accent_color": "#112233"})
        self.assertGreaterEqual(status, 400, body)


if __name__ == "__main__":
    unittest.main()
