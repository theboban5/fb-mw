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


@unittest.skipUnless(live_support.available(), "Supabase credentials not configured")
class TriggerRebuildCorsTest(unittest.TestCase):
    """The preflight — the check whose absence broke this twice.

    The only caller that matters is a browser on everyleague.co, and a
    cross-origin POST carrying Authorization and apikey is never sent on its
    own: the browser sends an OPTIONS preflight first and issues the real
    request only if the answer allows it.

    Both times this function broke, the fix was verified with curl. curl sends
    no preflight, so it passed from a terminal while every phone silently
    failed — results piling up in Postgres with no build dispatched and nothing
    anywhere saying so. THIS is the test that closes that gap, and it has to
    speak HTTP directly rather than through live_support.call, because what is
    being asserted is the CORS handshake itself.
    """

    ORIGIN = "https://everyleague.co"

    @staticmethod
    def _lower(headers):
        """Header names are case-insensitive, and here they genuinely differ:
        the gateway title-cases the ones it adds while the function's own come
        through lowercase. Comparing literal keys silently misses half of
        them."""
        return {k.lower(): v for k, v in dict(headers).items()}

    def preflight(self, headers="authorization,apikey,content-type,x-client-info"):
        import urllib.error
        import urllib.request

        url = f"{sb.url()}/functions/v1/trigger-rebuild"
        req = urllib.request.Request(url, method="OPTIONS")
        req.add_header("Origin", self.ORIGIN)
        req.add_header("Access-Control-Request-Method", "POST")
        req.add_header("Access-Control-Request-Headers", headers)
        try:
            with urllib.request.urlopen(req, timeout=sb.TIMEOUT) as resp:
                return resp.status, self._lower(resp.headers)
        except urllib.error.HTTPError as err:
            return err.code, self._lower(err.headers)

    def test_the_preflight_succeeds(self):
        status, _ = self.preflight()
        self.assertIn(status, (200, 204),
                      "the browser preflight was refused, so no phone can "
                      "reach this function however well it works from curl")

    def test_the_preflight_allows_this_origin(self):
        _, headers = self.preflight()
        allow = headers.get("access-control-allow-origin", "")
        self.assertTrue(allow in ("*", self.ORIGIN),
                        f"Allow-Origin was {allow!r}")

    def test_the_preflight_allows_every_header_supabase_js_sends(self):
        """A header missing from the allow-list fails the preflight just as
        surely as a missing Allow-Origin. supabase-js sends x-client-info
        without being asked."""
        _, headers = self.preflight()
        allowed = headers.get("access-control-allow-headers", "").lower()
        for header in ("authorization", "apikey", "content-type", "x-client-info"):
            self.assertIn(header, allowed)

    def test_the_preflight_allows_post(self):
        _, headers = self.preflight()
        self.assertIn("post",
                      headers.get("access-control-allow-methods", "").lower())

    def test_a_rejected_call_still_carries_cors_headers(self):
        """An error the browser cannot READ is an error nobody can diagnose.
        The 403 for a non-reporter has to be a real response, not an opaque
        network failure."""
        import json as _json
        import urllib.error
        import urllib.request

        anon = os.environ["SUPABASE_PUBLISHABLE_KEY"]
        req = urllib.request.Request(
            f"{sb.url()}/functions/v1/trigger-rebuild",
            data=_json.dumps({}).encode(), method="POST")
        req.add_header("Origin", self.ORIGIN)
        req.add_header("Authorization", f"Bearer {anon}")
        req.add_header("apikey", anon)
        req.add_header("Content-Type", "application/json")
        try:
            with urllib.request.urlopen(req, timeout=sb.TIMEOUT) as resp:
                headers = self._lower(resp.headers)
        except urllib.error.HTTPError as err:
            # 403: the publishable key is not a reporter. That is the point.
            self.assertEqual(err.code, 403)
            headers = self._lower(err.headers)
        self.assertIn("access-control-allow-origin", headers)


if __name__ == "__main__":
    unittest.main()
