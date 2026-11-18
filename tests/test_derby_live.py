"""Bring your friends to the Derby against the real database (0072).

    RLS_LIVE=1 python3 -m unittest tests.test_derby_live

tests/test_derby.py holds the page to the seed; this holds the database to
the page's promises:

  * a sign-up returns a code, and the same phone again gets the SAME code
    back rather than a second row: that is how a lost link is recovered;
  * a friend through the link counts once, a second sign-up from the same
    phone does not count again, and an unknown code still signs them up;
  * someone already on the list cannot be "referred" onto it;
  * the follow and the phone are required here, and 0071's box is unchanged;
  * anon can call the two public functions and nothing else: not the
    tables, not the admin screen. A reporter who is not an admin cannot
    either.

The counting runs inside a test campaign whose window is opened around now
by the secret key, so these tests do not depend on the real race's dates.
Every phone number is in Ofcom's drama range (+44 7700 900xxx), which no
real subscriber holds, and every row is deleted in teardown.
"""

import os
import random
import sys
import unittest
import urllib.parse
import uuid
from datetime import datetime, timedelta, timezone

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from src import supabase_client as sb  # noqa: E402
from tests import live_support  # noqa: E402
from tests.live_support import call  # noqa: E402

UNSET = object()   # token=None means ANON; see test_portal_errors_live


def rpc(name, body, *, token=None):
    return call(f"rpc/{name}", token=token, method="POST", body=body)


def delete(table, query):
    sb._request("DELETE", table, query=query,
                headers={"Prefer": "return=minimal"}, require_secret=True)


@unittest.skipUnless(live_support.available(), "Supabase credentials not configured")
class DerbyReferralsTest(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        cls.identities = live_support.Identities(prefix="MW_DERBYTEST").setup()
        cls.tokens = cls.identities.tokens
        cls.campaign = f"test-{uuid.uuid4().hex[:8]}"
        now = datetime.now(timezone.utc)
        sb.upsert("referral_campaigns", [{
            "campaign_id": cls.campaign, "title": "Live test",
            "opens_at": (now - timedelta(hours=1)).isoformat(),
            "closes_at": (now + timedelta(hours=1)).isoformat(),
        }], on_conflict="campaign_id")
        # Distinct drama-range numbers for this run.
        cls.pool = iter(random.sample(range(1000), 1000))
        cls.phones = []

    @classmethod
    def tearDownClass(cls):
        # A bare "+" in a query string is a space, so "phone=eq.+44…" matched
        # nothing and the first run of this file left ten rows on the live
        # list. Encoded, and every row checked gone afterwards.
        for phone in cls.phones:
            # Referred rows first: referred_by references ref_code.
            delete("update_signups", f"phone=eq.{urllib.parse.quote(phone, safe='')}"
                                     "&referred_by=not.is.null")
        for phone in cls.phones:
            delete("update_signups", f"phone=eq.{urllib.parse.quote(phone, safe='')}")
        left = sb._request("GET", "update_signups",
                           query="select=id&phone=like.%2B447700900*",
                           require_secret=True)
        delete("referral_draws", f"campaign_id=eq.{cls.campaign}")
        delete("referral_campaigns", f"campaign_id=eq.{cls.campaign}")
        cls.identities.teardown()
        assert not left, f"live test rows left on the list: {left}"

    def phone(self):
        n = next(self.pool)
        normalised = f"+447700900{n:03d}"
        self.phones.append(normalised)
        return f"+44 7700 900{n:03d}"

    def sign(self, phone, ref="", follows=("facebook",), name="Live Test",
             pred=(None, None), token=None):
        status, body = rpc("submit_derby_signup", {
            "p_name": name, "p_phone": phone, "p_email": "", "p_ref": ref,
            "p_pred_home": pred[0], "p_pred_away": pred[1],
            "p_follows": list(follows), "p_campaign": self.campaign,
            "p_website": ""}, token=token)
        self.assertEqual(status, 200, body)
        return body

    def count(self, code):
        status, body = rpc("referral_count",
                           {"p_campaign": self.campaign, "p_code": code})
        self.assertEqual(status, 200, body)
        return body

    # ── the page's promises ──────────────────────────────────────────────────

    def test_a_signup_gets_a_code_and_keeps_it(self):
        phone = self.phone()
        first = self.sign(phone)
        self.assertTrue(first["ok"])
        self.assertEqual(first["status"], "signed_up")
        self.assertRegex(first["code"], r"^[ABCDEFGHJKMNPQRSTUVWXYZ2-9]{6}$")
        again = self.sign(phone.replace(" ", ""), follows=("whatsapp",))
        self.assertEqual(again["status"], "already")
        self.assertEqual(again["code"], first["code"])
        self.assertNotIn("id", again)

    def test_a_friend_counts_once(self):
        me = self.sign(self.phone())["code"]
        friend = self.phone()
        out = self.sign(friend, ref=me.lower(), pred=(2, 1))
        self.assertEqual(out["status"], "signed_up")
        self.assertEqual(self.count(me), {"counted": 1, "used": 1})
        # The same friend again, through the same link: still one.
        self.sign(friend, ref=me)
        self.assertEqual(self.count(me), {"counted": 1, "used": 1})

    def test_someone_already_on_the_list_cannot_be_referred(self):
        me = self.sign(self.phone())["code"]
        already = self.sign(self.phone())["code"]
        self.assertNotEqual(me, already)
        # `already` is on the list; signing up again through me changes nothing.
        out = self.sign(self.phones[-1], ref=me)
        self.assertEqual(out["status"], "already")
        self.assertEqual(self.count(me), {"counted": 0, "used": 0})

    def test_an_unknown_code_still_signs_them_up(self):
        out = self.sign(self.phone(), ref="ZZZZZZ")
        self.assertTrue(out["ok"])
        self.assertEqual(out["status"], "signed_up")

    def test_the_follow_and_the_phone_are_required(self):
        out = self.sign(self.phone(), follows=())
        self.assertFalse(out["ok"])
        self.assertIn("Follow EveryLeague", out["message"])
        out = self.sign("", follows=("facebook",))
        self.assertFalse(out["ok"])
        self.assertIn("phone number", out["message"])

    def test_a_code_gives_a_first_name_and_nothing_else(self):
        code = self.sign(self.phone(), name="chikondi  live banda")["code"]
        for asked in (code, code.lower()):
            status, body = rpc("referral_inviter", {"p_code": asked})
            self.assertEqual((status, body), (200, "Chikondi"))
        status, body = rpc("referral_inviter", {"p_code": "ZZZZZZ"})
        self.assertEqual((status, body), (200, ""))

    def test_the_quiz_signup_box_is_unchanged(self):
        phone = self.phone()
        status, body = rpc("submit_update_signup", {
            "p_name": "Live Test", "p_phone": phone, "p_email": "",
            "p_website": ""})
        self.assertEqual(status, 200, body)
        self.assertEqual(body, {"ok": True, "status": "signed_up"})
        status, body = rpc("submit_update_signup", {
            "p_name": "Live Test", "p_phone": phone, "p_email": "",
            "p_website": ""})
        self.assertEqual(body["status"], "already")
        self.assertNotIn("id", body)

    # ── who can see what ─────────────────────────────────────────────────────

    def test_anon_cannot_read_the_tables(self):
        for table in ("update_signups", "referral_campaigns", "referral_draws"):
            with self.subTest(table):
                status, body = call(f"{table}?select=*&limit=1", token=None)
                self.assertIn(status, (401, 403), body)

    def test_the_admin_functions_refuse_anon_and_reporters(self):
        calls = [
            ("admin_referral_campaigns", {}),
            ("admin_referrers", {"p_campaign": self.campaign}),
            ("admin_referral_referees", {"p_campaign": self.campaign, "p_code": "ZZZZZZ"}),
            ("admin_referral_draws", {"p_campaign": self.campaign}),
            ("admin_draw_referral_tickets", {"p_campaign": self.campaign, "p_count": 1}),
        ]
        for name, body in calls:
            with self.subTest(name, who="anon"):
                status, _ = rpc(name, body, token=None)
                self.assertIn(status, (401, 403, 404))
            with self.subTest(name, who="reporter"):
                status, _ = rpc(name, body, token=self.tokens["a"])
                self.assertEqual(status, 403)

    def test_the_admin_sees_the_race(self):
        me = self.sign(self.phone(), name="Live Referrer")["code"]
        self.sign(self.phone(), ref=me)
        self.sign(self.phone(), ref=me)
        status, rows = rpc("admin_referrers", {"p_campaign": self.campaign},
                           token=self.tokens["admin"])
        self.assertEqual(status, 200, rows)
        mine = [r for r in rows if r["code"] == me]
        self.assertEqual(len(mine), 1)
        self.assertEqual(mine[0]["counted"], 2)
        status, people = rpc("admin_referral_referees",
                             {"p_campaign": self.campaign, "p_code": me},
                             token=self.tokens["admin"])
        self.assertEqual(status, 200, people)
        self.assertEqual(len(people), 2)
        self.assertTrue(all(p["counts"] for p in people))

    def test_the_draw_waits_for_the_close(self):
        status, body = rpc("admin_draw_referral_tickets",
                           {"p_campaign": self.campaign, "p_count": 1},
                           token=self.tokens["admin"])
        self.assertNotEqual(status, 200)
        self.assertIn("still open", str(body))


if __name__ == "__main__":
    unittest.main()
