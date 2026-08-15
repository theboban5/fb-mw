"""claim_rebuild: the debounce that stops a burst of reports deploying a burst.

The interesting case is the one that cannot be tested by reading the code: two
reporters publishing at the same instant. If the decision were made in the Edge
Function — read the timestamp, compare, then dispatch — both would read "no
recent dispatch" and both would fire. It is a single conditional UPDATE in
Postgres precisely so the second caller blocks on the row lock and then loses.
This file fires concurrent claims at a real database to prove it.

    RLS_LIVE=1 python3 -m unittest tests.test_rebuild_live
"""

import concurrent.futures
import os
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from src import supabase_client as sb  # noqa: E402
from tests import live_support  # noqa: E402
from tests.live_support import call  # noqa: E402


@unittest.skipUnless(live_support.available(), "Supabase credentials not configured")
class ClaimRebuildTest(unittest.TestCase):

    def setUp(self):
        self.reset()

    @classmethod
    def tearDownClass(cls):
        # Leave the trigger armed rather than mid-cooldown.
        cls.reset()

    @staticmethod
    def reset():
        # dispatch_count too: it accumulates across tests otherwise, and a test
        # asserting an absolute count then depends on execution order.
        sb._request("PATCH", "rebuild_state", query="id=is.true",
                    body={"last_dispatched_at": None, "pending": False,
                          "dispatch_count": 0},
                    headers={"Prefer": "return=minimal"}, require_secret=True)

    @staticmethod
    def claim(cooldown):
        return sb.rpc("claim_rebuild", {"p_cooldown_seconds": cooldown},
                      require_secret=True)

    def state(self):
        return sb.select("rebuild_state", require_secret=True)[0]

    def test_the_first_request_dispatches(self):
        self.assertTrue(self.claim(60))
        self.assertIsNotNone(self.state()["last_dispatched_at"])

    def test_a_second_request_inside_the_window_is_coalesced(self):
        self.assertTrue(self.claim(3600))
        self.assertFalse(self.claim(3600))
        self.assertFalse(self.claim(3600))

    def test_a_coalesced_request_is_recorded_as_pending(self):
        self.claim(3600)
        self.assertFalse(self.state()["pending"])
        self.claim(3600)
        self.assertTrue(self.state()["pending"])

    def test_a_dispatch_clears_pending(self):
        self.claim(3600)
        self.claim(3600)                      # sets pending
        self.assertTrue(self.state()["pending"])
        self.assertTrue(self.claim(0))        # window elapsed
        self.assertFalse(self.state()["pending"])

    def test_the_window_reopens(self):
        self.assertTrue(self.claim(3600))
        self.assertFalse(self.claim(3600))
        # A zero cooldown is "the window has passed".
        self.assertTrue(self.claim(0))

    def test_dispatch_count_only_rises_on_a_real_dispatch(self):
        before = self.state()["dispatch_count"]
        self.claim(3600)
        self.claim(3600)
        self.claim(3600)
        self.assertEqual(self.state()["dispatch_count"], before + 1)

    def test_concurrent_requests_dispatch_exactly_once(self):
        """The race the design exists to prevent.

        Eight simultaneous claims — the shape of a Saturday afternoon when a
        round of fixtures all finish together — must produce one deploy.
        """
        with concurrent.futures.ThreadPoolExecutor(8) as pool:
            results = list(pool.map(lambda _: self.claim(3600), range(8)))
        self.assertEqual(sum(1 for r in results if r), 1, results)
        self.assertEqual(self.state()["dispatch_count"], 1)
        self.assertTrue(self.state()["pending"])

    def test_a_reporter_cannot_dispatch_deploys_directly(self):
        """EXECUTE is service_role only. Build minutes are not a reporter's to
        spend, and the Edge Function is the only intended caller."""
        identities = live_support.Identities(prefix="MW_DEPLOYTEST").setup()
        try:
            for label in ("a", "admin"):
                status, body = call("rpc/claim_rebuild",
                                    token=identities.tokens[label],
                                    method="POST",
                                    body={"p_cooldown_seconds": 0})
                self.assertIn(status, (401, 403, 404), f"{label}: {body}")
        finally:
            identities.teardown()

    def test_anon_cannot_dispatch_deploys(self):
        status, body = call("rpc/claim_rebuild", token=None, method="POST",
                            body={"p_cooldown_seconds": 0})
        self.assertIn(status, (401, 403, 404), body)

    def test_the_state_table_is_invisible_to_everyone_else(self):
        status, rows = call("rebuild_state?select=*", token=None)
        # RLS is on with no policy: either filtered to nothing or refused.
        if status == 200:
            self.assertEqual(rows, [])
        else:
            self.assertIn(status, (401, 403, 404))


if __name__ == "__main__":
    unittest.main()
