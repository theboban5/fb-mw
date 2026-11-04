"""FAM registration numbers (0065): a number names one player.

The rule that matters most is the attach guard: a number goes onto a player
the sheet was linked to BY NAME only if that player holds no number yet,
because one wrong attachment links that number to the wrong person on every
sheet after it. `test_a_second_number_is_never_attached` is that rule.

    RLS_LIVE=1 python3 -m unittest tests.test_fam_regs_live
"""

import os
import random
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from src import supabase_client as sb  # noqa: E402
from tests import live_support  # noqa: E402
from tests.live_support import call  # noqa: E402


def fake_reg():
    # Well-formed and from a range no real FAM number sits in: the FA's
    # numbers counted up from 000001, and this is 999 thousand-something.
    return f"999{random.randint(0, 999):03d}F99"


@unittest.skipUnless(live_support.available(), "Supabase credentials not configured")
class FamRegsTest(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        cls.identities = live_support.Identities(prefix="MW_FAMTEST").setup()
        cls.tokens = cls.identities.tokens
        cls.players = []
        cls.regs = []

    @classmethod
    def tearDownClass(cls):
        for reg in cls.regs:
            sb._request("DELETE", "player_fam_regs", query=f"fam_reg_no=eq.{reg}",
                        headers={"Prefer": "return=minimal"}, require_secret=True)
        for player_id in cls.players:
            sb._request("DELETE", "aliases",
                        query=f"entity_type=eq.player&entity_id=eq.{player_id}",
                        headers={"Prefer": "return=minimal"}, require_secret=True)
            sb._request("DELETE", "players", query=f"player_id=eq.{player_id}",
                        headers={"Prefer": "return=minimal"}, require_secret=True)
        cls.identities.teardown()

    def reg(self):
        reg = fake_reg()
        while reg in self.regs:
            reg = fake_reg()
        self.regs.append(reg)
        return reg

    def player(self, name):
        status, body = call("rpc/create_player", token=self.tokens["a"], method="POST",
                            body={"p_full_name": name, "p_force": True})
        self.assertEqual(status, 200, body)
        self.players.append(body[0]["player_id"])
        return body[0]["player_id"]

    def register(self, rows, label="a"):
        status, body = call("rpc/register_fam_players", token=self.tokens.get(label),
                            method="POST", body={"p_rows": rows})
        if status == 200:
            self.players.extend(r["player_id"] for r in body
                                if r["outcome"] == "created")
        return status, body

    def holders(self, regs, label="a"):
        return call("rpc/fam_players", token=self.tokens.get(label), method="POST",
                    body={"p_reg_nos": regs})

    def test_anon_and_inactive_are_refused(self):
        for label in (None, "inactive"):
            status, _ = self.holders([self.reg()], label=label)
            self.assertNotEqual(status, 200, label)
            status, _ = self.register([], label=label)
            self.assertNotEqual(status, 200, label)

    def test_the_table_is_not_readable_directly(self):
        status, body = call("player_fam_regs?select=*", token=self.tokens["a"])
        self.assertNotEqual(status, 200, body)

    def test_an_unlinked_number_creates_the_player_once(self):
        reg = self.reg()
        status, body = self.register([{"reg_no": reg, "player_id": "",
                                        "name": "Zzzfam  Newcomer"}])
        self.assertEqual(status, 200, body)
        self.assertEqual(body[0]["outcome"], "created")
        self.assertEqual(body[0]["full_name"], "Zzzfam Newcomer")
        created = body[0]["player_id"]

        # The same sheet saved again, or the next match's: the holder, not a
        # second player.
        status, body = self.register([{"reg_no": reg.lower(), "player_id": "",
                                       "name": "ZZZFAM NEWCOMER"}])
        self.assertEqual(status, 200, body)
        self.assertEqual((body[0]["outcome"], body[0]["player_id"]), ("known", created))
        status, body = self.holders([reg])
        self.assertEqual(status, 200, body)
        self.assertEqual(body[0]["player_id"], created)

    def test_a_created_namesake_is_counted(self):
        self.player("Zzzfam Namesake")
        status, body = self.register([{"reg_no": self.reg(), "player_id": "",
                                       "name": "Zzzfam Namesake"}])
        self.assertEqual(status, 200, body)
        self.assertEqual((body[0]["outcome"], body[0]["namesakes"]), ("created", 1))

    def test_a_second_number_is_never_attached(self):
        linked = self.player("Zzzfam Linked")
        first, second = self.reg(), self.reg()
        status, body = self.register([{"reg_no": first, "player_id": linked,
                                       "name": "Zzzfam Linked"}])
        self.assertEqual((status, body[0]["outcome"]), (200, "attached"), body)
        status, body = self.register([{"reg_no": second, "player_id": linked,
                                       "name": "Zzzfam Linked"}])
        self.assertEqual((status, body[0]["outcome"]), (200, "conflict"), body)
        self.assertIn(first, body[0]["message"])
        status, body = self.holders([second])
        self.assertEqual((status, body), (200, []))

    def test_a_held_number_on_another_link_is_a_conflict(self):
        holder, other = self.player("Zzzfam Holder"), self.player("Zzzfam Other")
        reg = self.reg()
        self.register([{"reg_no": reg, "player_id": holder, "name": "Zzzfam Holder"}])
        status, body = self.register([{"reg_no": reg, "player_id": other,
                                       "name": "Zzzfam Other"}])
        self.assertEqual((status, body[0]["outcome"]), (200, "conflict"), body)
        self.assertEqual(body[0]["player_id"], holder)
        self.assertIn("Zzzfam Holder", body[0]["message"])

    def test_a_bad_number_is_one_line_not_the_batch(self):
        status, body = self.register([
            {"reg_no": "00584M95", "player_id": "", "name": "Zzzfam Typo"},
            {"reg_no": self.reg(), "player_id": "", "name": "Zzzfam Fine"}])
        self.assertEqual(status, 200, body)
        self.assertEqual([r["outcome"] for r in body], ["invalid", "created"])

    def test_a_merge_carries_the_numbers(self):
        loser, winner = self.player("Zzzfam Issac"), self.player("Zzzfam Isaac")
        a, b = self.reg(), self.reg()
        self.register([{"reg_no": a, "player_id": loser, "name": "Zzzfam Issac"},
                       {"reg_no": b, "player_id": winner, "name": "Zzzfam Isaac"}])
        status, body = call("rpc/merge_players", token=self.tokens["admin"],
                            method="POST", body={"p_loser": loser, "p_winner": winner})
        self.assertEqual(status, 200, body)
        status, body = self.holders([a, b])
        self.assertEqual(status, 200, body)
        self.assertEqual({r["fam_reg_no"]: r["player_id"] for r in body},
                         {a: winner, b: winner})


if __name__ == "__main__":
    unittest.main()
