"""The ideas board's tables (0075), exercised over the network with real
identities — test_ops_live's access sweep, for two tables with plain RLS.

The board is admin-only for READING as well as writing, so the reporter must
get nothing back, not a filtered subset, and anon must be refused outright
(this project's default privileges grant new objects to anon; 0048). Then
the two rules the CHECKs carry: an open idea always has a lane, so a lane
cannot be deleted under one; and a closed idea has an outcome.

    RLS_LIVE=1 python3 -m unittest tests.test_ideas_live

Every row it creates is named MW_IDEATEST and deleted again.
"""

import os
import sys
import unittest
import uuid

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from tests import live_support  # noqa: E402
from tests.live_support import call as _call  # noqa: E402

TAG = "MW_IDEATEST_" + uuid.uuid4().hex[:8]


@unittest.skipUnless(live_support.available(), "Supabase credentials not configured")
class IdeasBoardTest(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        cls.identities = live_support.Identities(prefix="MW_IDEATEST").setup()
        cls.tokens = cls.identities.tokens
        status, body = _call("idea_lanes", token=cls.tokens["admin"], method="POST",
                             body={"name": TAG[:30], "sort_order": 9999},
                             prefer="return=representation")
        assert status == 201, body
        cls.lane = body[0]["lane_id"]

    @classmethod
    def tearDownClass(cls):
        admin = cls.tokens["admin"]
        _call(f"ideas?title=like.{TAG}*", token=admin, method="DELETE")
        _call(f"idea_lanes?lane_id=eq.{cls.lane}", token=admin, method="DELETE")
        cls.identities.teardown()

    def add(self, token, **over):
        row = {"title": f"{TAG} {uuid.uuid4().hex[:6]}", "lane_id": self.lane, **over}
        return _call("ideas", token=token, method="POST", body=row,
                     prefer="return=representation")

    def test_anon_is_refused(self):
        for name in ("ideas", "idea_lanes"):
            with self.subTest(table=name):
                status, body = _call(f"{name}?select=*", token=None)
                self.assertGreaterEqual(status, 400, f"anon read {name}: {body!r}")
        status, _ = _call("ideas", token=None, method="POST",
                          body={"title": TAG, "lane_id": self.lane})
        self.assertGreaterEqual(status, 400)

    def test_reporter_sees_and_writes_nothing(self):
        status, row = self.add(self.tokens["admin"])
        self.assertEqual(status, 201, row)
        for label in ("a", "inactive"):
            token = self.tokens[label]
            for name in ("ideas", "idea_lanes"):
                with self.subTest(identity=label, table=name):
                    status, body = _call(f"{name}?select=*", token=token)
                    self.assertEqual(status, 200)
                    self.assertEqual(body, [], f"{label} saw rows in {name}")
            with self.subTest(identity=label, write="insert"):
                status, _ = self.add(token)
                self.assertGreaterEqual(status, 400)
            # RLS with no matching policy updates zero rows and says 200 or
            # 204, so the row itself is the evidence.
            _call(f"ideas?idea_id=eq.{row[0]['idea_id']}", token=token,
                  method="PATCH", body={"title": f"{TAG} tampered"})
        _, now = _call(f"ideas?idea_id=eq.{row[0]['idea_id']}&select=title",
                       token=self.tokens["admin"])
        self.assertEqual(now[0]["title"], row[0]["title"])

    def test_admin_round_trip(self):
        admin = self.tokens["admin"]
        status, row = self.add(admin, tags=["UI", "Data"], priority=1, size="small")
        self.assertEqual(status, 201, row)
        idea = row[0]
        self.assertTrue(idea["created_by"], "created_by should default to the admin")
        status, body = _call(f"ideas?idea_id=eq.{idea['idea_id']}", token=admin,
                             method="PATCH", prefer="return=representation",
                             body={"closed_at": "2026-11-18T12:00:00Z", "outcome": "done"})
        self.assertEqual(status, 200, body)
        self.assertEqual(body[0]["outcome"], "done")

    def test_closed_needs_an_outcome(self):
        status, _ = self.add(self.tokens["admin"], closed_at="2026-11-18T12:00:00Z")
        self.assertGreaterEqual(status, 400)
        status, _ = self.add(self.tokens["admin"], outcome="done")
        self.assertGreaterEqual(status, 400)

    def test_bad_tags_and_titles_are_refused(self):
        admin = self.tokens["admin"]
        for over in ({"tags": [" padded"]}, {"tags": ["x" * 25]},
                     {"tags": [f"t{i}" for i in range(13)]}, {"priority": 4},
                     {"size": "huge"}):
            with self.subTest(**{k: str(v)[:20] for k, v in over.items()}):
                status, _ = self.add(admin, **over)
                self.assertGreaterEqual(status, 400)
        status, _ = _call("ideas", token=admin, method="POST",
                          body={"title": "  ", "lane_id": self.lane})
        self.assertGreaterEqual(status, 400)

    def test_a_lane_cannot_be_deleted_under_an_open_idea(self):
        admin = self.tokens["admin"]
        status, body = _call("idea_lanes", token=admin, method="POST",
                             body={"name": "IDEATEST doomed", "sort_order": 9998},
                             prefer="return=representation")
        self.assertEqual(status, 201, body)
        lane = body[0]["lane_id"]
        try:
            status, row = self.add(admin, lane_id=lane)
            self.assertEqual(status, 201, row)
            status, _ = _call(f"idea_lanes?lane_id=eq.{lane}", token=admin, method="DELETE")
            self.assertGreaterEqual(status, 400, "a lane went with an open idea in it")
            # Closed, it may go — and the idea stays, with no lane.
            _call(f"ideas?idea_id=eq.{row[0]['idea_id']}", token=admin, method="PATCH",
                  body={"closed_at": "2026-11-18T12:00:00Z", "outcome": "done"})
            status, _ = _call(f"idea_lanes?lane_id=eq.{lane}", token=admin, method="DELETE")
            self.assertLess(status, 300)
            _, after = _call(f"ideas?idea_id=eq.{row[0]['idea_id']}&select=lane_id",
                             token=admin)
            self.assertIsNone(after[0]["lane_id"])
        finally:
            _call(f"ideas?lane_id=eq.{lane}", token=admin, method="DELETE")
            _call(f"idea_lanes?lane_id=eq.{lane}", token=admin, method="DELETE")


if __name__ == "__main__":
    unittest.main()
