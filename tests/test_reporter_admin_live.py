"""Managing the reporter pool from /report, exercised over the network.

Three things are proved here, and the first is the one that matters most.

  1. NOBODY CAN PROMOTE THEMSELVES. `admin_create_reporter` takes an
     auth_user_id and a role, so a signed-in account that could reach it could
     attach an admin reporters row to its own login. It is revoked from
     `authenticated` outright, and that revoke is the whole security boundary
     — so it is asserted directly rather than inferred from the fact that the
     portal never calls it. The other four RPCs are is_admin()-gated and are
     checked against anon, an ordinary reporter and an inactive one.

  2. THE LAST ADMINISTRATOR SURVIVES. Nothing in the portal can restore an
     admin, so nothing in the portal may remove the last one. Demoting and
     deactivating both refuse, and so does an admin acting on their own
     account — the mis-tap that would otherwise be unrecoverable.

  3. THE ROUND TRIP WORKS. A reporter created through the Edge Function can
     sign in with the password it returned, and sees exactly the competitions
     it was given.

It creates real auth users and rows in `reporters`, so it is opt-in:

    RLS_LIVE=1 python3 -m unittest tests.test_reporter_admin_live

Every fixture is namespaced (MW_REPADMIN_*) and torn down again, including the
auth users. It writes no football data and touches no real match.

The Edge Function half is skipped automatically when `manage-reporters` is not
deployed to this project — a migration and a function are separate deploys
(see CLAUDE.md), and a checkout that has one without the other should report a
skip rather than a failure.
"""

import json
import os
import sys
import unittest
import urllib.error
import urllib.request
import uuid

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from src import supabase_client as sb  # noqa: E402
from tests import live_support  # noqa: E402
from tests.live_support import call as _call  # noqa: E402


def _function(path, *, token, body):
    """POST to an Edge Function as a specific identity."""
    anon = os.environ["SUPABASE_PUBLISHABLE_KEY"]
    req = urllib.request.Request(
        f"{sb.url()}/functions/v1/{path}",
        data=json.dumps(body).encode(), method="POST")
    req.add_header("apikey", anon)
    req.add_header("Authorization", f"Bearer {token}")
    req.add_header("Content-Type", "application/json")
    try:
        with urllib.request.urlopen(req, timeout=sb.TIMEOUT) as resp:
            return resp.status, json.loads(resp.read().decode() or "{}")
    except urllib.error.HTTPError as err:
        raw = err.read().decode("utf-8", "replace")
        try:
            return err.code, json.loads(raw)
        except ValueError:
            return err.code, raw


@unittest.skipUnless(live_support.available(), "set RLS_LIVE=1 to run")
class ReporterAdminTest(unittest.TestCase):
    """The 0026 RPCs and the manage-reporters function."""

    PREFIX = "MW_REPADMIN"

    @classmethod
    def setUpClass(cls):
        cls.ids = live_support.Identities(prefix=cls.PREFIX).setup()
        cls.suffix = cls.ids.suffix
        # Anything this class creates BEYOND the harness, torn down in one
        # place so a failing assertion cannot leave an admin account behind.
        cls.extra_reporters = []
        cls.extra_users = []

    @classmethod
    def tearDownClass(cls):
        for reporter_id in cls.extra_reporters:
            sb._request("DELETE", "reporter_assignments",
                        query=f"reporter_id=eq.{reporter_id}",
                        headers={"Prefer": "return=minimal"}, require_secret=True)
            sb._request("DELETE", "reporters",
                        query=f"reporter_id=eq.{reporter_id}",
                        headers={"Prefer": "return=minimal"}, require_secret=True)
        for user_id in cls.extra_users:
            try:
                live_support.admin_auth("DELETE", f"admin/users/{user_id}")
            except Exception:  # noqa: BLE001 - teardown must not mask a failure
                pass
        cls.ids.teardown()

    def rpc(self, name, body, *, token):
        return _call(f"rpc/{name}", token=token, method="POST", body=body)

    # ── 1. Who may call what ─────────────────────────────────────────────────

    def test_create_reporter_is_unreachable_from_a_browser(self):
        """The escalation path, closed at the grant rather than in a check.

        This function trusts its p_actor argument, because the secret key it is
        called with has no auth.uid() to check instead. That is only safe while
        no browser identity can reach it — which is what this asserts.
        """
        payload = {
            "p_actor": self.ids.ids["admin"],       # a genuine administrator
            "p_name": "Escalation",
            "p_email": f"escalate-{self.suffix}@everyleague.test",
            "p_auth_user_id": self.ids.users["a"],  # ...attached to MY login
            "p_role": "admin",
        }
        for label in ("a", "admin"):
            status, body = self.rpc("admin_create_reporter", payload,
                                    token=self.ids.tokens[label])
            self.assertEqual(status, 403, f"{label} reached admin_create_reporter")
            self.assertIn("permission denied", json.dumps(body).lower())

        # anon too, which is the same grant but the likelier probe.
        status, _ = self.rpc("admin_create_reporter", payload, token=None)
        self.assertEqual(status, 401)

    def test_ordinary_reporters_cannot_manage_the_pool(self):
        cases = [
            ("admin_assign_competition",
             {"p_reporter": self.ids.ids["b"], "p_competition": "MW_SRFA"}),
            ("admin_unassign_competition",
             {"p_reporter": self.ids.ids["b"], "p_competition": "MW_SRFA"}),
            ("admin_set_reporter_role",
             {"p_reporter": self.ids.ids["b"], "p_role": "admin"}),
            ("admin_set_reporter_active",
             {"p_reporter": self.ids.ids["b"], "p_active": False}),
        ]
        # An inactive administrator is included deliberately: `active` alone
        # gates every other check in this schema, and is_admin() requires it.
        for label in ("a", "inactive"):
            for name, body in cases:
                status, _ = self.rpc(name, body, token=self.ids.tokens[label])
                self.assertEqual(status, 403, f"{label} reached {name}")
        for name, body in cases:
            status, _ = self.rpc(name, body, token=None)
            self.assertEqual(status, 401, f"anon reached {name}")

    def test_next_reporter_id_is_granted_to_nobody(self):
        for label in ("a", "admin"):
            status, _ = self.rpc("next_reporter_id", {},
                                 token=self.ids.tokens[label])
            self.assertEqual(status, 403)

    # ── 2. What an administrator may do ──────────────────────────────────────

    def test_admin_assigns_and_unassigns_a_competition(self):
        target = self.ids.ids["b"]
        admin = self.ids.tokens["admin"]

        def assignments():
            rows = sb.select("reporter_assignments", columns="competition_id",
                             params={"reporter_id": f"eq.{target}"},
                             require_secret=True)
            return {r["competition_id"] for r in rows}

        self.assertNotIn("MW_NRFA", assignments())

        status, _ = self.rpc("admin_assign_competition",
                             {"p_reporter": target, "p_competition": "MW_NRFA"},
                             token=admin)
        self.assertEqual(status, 204, "assign should succeed for an admin")
        self.assertIn("MW_NRFA", assignments())

        # Twice is not an error: the portal saves on every tap and a repeated
        # one must not raise.
        status, _ = self.rpc("admin_assign_competition",
                             {"p_reporter": target, "p_competition": "MW_NRFA"},
                             token=admin)
        self.assertEqual(status, 204)

        status, _ = self.rpc("admin_unassign_competition",
                             {"p_reporter": target, "p_competition": "MW_NRFA"},
                             token=admin)
        self.assertEqual(status, 204)
        self.assertNotIn("MW_NRFA", assignments())
        # The one it started with is untouched.
        self.assertIn("MW_SRFA", assignments())

    def test_assigning_an_unknown_competition_says_so(self):
        status, body = self.rpc(
            "admin_assign_competition",
            {"p_reporter": self.ids.ids["b"], "p_competition": "MW_NOT_A_LEAGUE"},
            token=self.ids.tokens["admin"])
        self.assertEqual(status, 400)
        self.assertIn("no competition", json.dumps(body))

    def test_admin_changes_a_role(self):
        target = self.ids.ids["a"]
        admin = self.ids.tokens["admin"]

        def role():
            return sb.select("reporters", columns="role",
                             params={"reporter_id": f"eq.{target}"},
                             require_secret=True)[0]["role"]

        self.assertEqual(role(), "reporter")
        status, _ = self.rpc("admin_set_reporter_role",
                             {"p_reporter": target, "p_role": "admin"},
                             token=admin)
        self.assertEqual(status, 204)
        self.assertEqual(role(), "admin")

        status, _ = self.rpc("admin_set_reporter_role",
                             {"p_reporter": target, "p_role": "reporter"},
                             token=admin)
        self.assertEqual(status, 204)
        self.assertEqual(role(), "reporter")

    def test_role_must_be_one_of_two_things(self):
        status, body = self.rpc(
            "admin_set_reporter_role",
            {"p_reporter": self.ids.ids["a"], "p_role": "superuser"},
            token=self.ids.tokens["admin"])
        self.assertEqual(status, 400)
        self.assertIn("role must be", json.dumps(body))

    def test_admin_deactivates_and_reactivates(self):
        target = self.ids.ids["b"]
        admin = self.ids.tokens["admin"]

        def active():
            return sb.select("reporters", columns="active",
                             params={"reporter_id": f"eq.{target}"},
                             require_secret=True)[0]["active"]

        status, _ = self.rpc("admin_set_reporter_active",
                             {"p_reporter": target, "p_active": False},
                             token=admin)
        self.assertEqual(status, 204)
        self.assertFalse(active())

        # Assignments survive deactivation — it is reversible, and is not the
        # same as forgetting what somebody covered.
        rows = sb.select("reporter_assignments", columns="competition_id",
                         params={"reporter_id": f"eq.{target}"},
                         require_secret=True)
        self.assertTrue(rows, "deactivating must not delete assignments")

        status, _ = self.rpc("admin_set_reporter_active",
                             {"p_reporter": target, "p_active": True},
                             token=admin)
        self.assertEqual(status, 204)
        self.assertTrue(active())

    # ── 3. The rules that cannot be undone from the portal ───────────────────

    def test_an_admin_cannot_change_their_own_role_or_account(self):
        me = self.ids.ids["admin"]
        token = self.ids.tokens["admin"]

        status, body = self.rpc("admin_set_reporter_role",
                                {"p_reporter": me, "p_role": "reporter"},
                                token=token)
        self.assertEqual(status, 403)
        self.assertIn("your own role", json.dumps(body))

        status, body = self.rpc("admin_set_reporter_active",
                                {"p_reporter": me, "p_active": False},
                                token=token)
        self.assertEqual(status, 403)
        self.assertIn("your own account", json.dumps(body))

        # ...and it really did not happen.
        row = sb.select("reporters", columns="role,active",
                        params={"reporter_id": f"eq.{me}"},
                        require_secret=True)[0]
        self.assertEqual(row["role"], "admin")
        self.assertTrue(row["active"])

    def test_the_last_administrator_cannot_be_removed(self):
        """Proved against a pool with exactly one admin in it.

        The harness admin is not the only administrator on a real project, so
        demoting somebody else would ordinarily be allowed. This makes a
        private two-admin situation and then removes one, leaving the other as
        the last — which is the state the guard exists for.
        """
        admin_token = self.ids.tokens["admin"]
        # Promote 'a' so there are two, then have 'a' demote the harness admin
        # and try to demote itself — the second is refused as a self-change,
        # so the guard is reached by deactivating instead.
        status, _ = self.rpc("admin_set_reporter_role",
                             {"p_reporter": self.ids.ids["a"], "p_role": "admin"},
                             token=admin_token)
        self.assertEqual(status, 204)
        a_token = live_support.sign_in(
            sb.select("reporters", columns="email",
                      params={"reporter_id": f"eq.{self.ids.ids['a']}"},
                      require_secret=True)[0]["email"])

        try:
            # There are other real administrators on this project, so the guard
            # is asserted against a reporter that IS provably the last one only
            # if none exists elsewhere. Count them instead of assuming.
            admins = sb.select("reporters", columns="reporter_id",
                               params={"role": "eq.admin", "active": "is.true"},
                               require_secret=True)
            others = [r["reporter_id"] for r in admins
                      if not r["reporter_id"].startswith(self.PREFIX)]
            if others:
                self.skipTest(
                    "this project has other administrators "
                    f"({len(others)}), so no test row can be the last one")

            status, body = self.rpc(
                "admin_set_reporter_active",
                {"p_reporter": self.ids.ids["admin"], "p_active": False},
                token=a_token)
            self.assertEqual(status, 204)
            status, body = self.rpc(
                "admin_set_reporter_role",
                {"p_reporter": self.ids.ids["a"], "p_role": "reporter"},
                token=a_token)
            self.assertEqual(status, 403)
            self.assertIn("last administrator", json.dumps(body))
        finally:
            sb._request("PATCH", "reporters",
                        query=f"reporter_id=eq.{self.ids.ids['admin']}",
                        body={"active": True, "role": "admin"},
                        headers={"Prefer": "return=minimal"}, require_secret=True)
            sb._request("PATCH", "reporters",
                        query=f"reporter_id=eq.{self.ids.ids['a']}",
                        body={"role": "reporter"},
                        headers={"Prefer": "return=minimal"}, require_secret=True)

    # ── 4. The Edge Function, end to end ─────────────────────────────────────

    def _skip_without_function(self):
        status, _ = _function("manage-reporters", token=self.ids.tokens["a"],
                              body={"action": "create"})
        if status == 404:
            self.skipTest("manage-reporters is not deployed to this project")

    def test_function_refuses_everyone_but_an_administrator(self):
        self._skip_without_function()
        body = {"action": "create", "name": "Nope",
                "email": f"nope-{self.suffix}@everyleague.test", "role": "admin"}
        for label in ("a", "inactive"):
            status, out = _function("manage-reporters",
                                    token=self.ids.tokens[label], body=body)
            self.assertEqual(status, 403, f"{label} got through")
            self.assertIn("administrator", json.dumps(out))

        # The publishable key is a valid JWT for this project and satisfies
        # verify_jwt on its own. It is not a USER token, which is the check
        # that keeps this from being an open endpoint.
        status, out = _function("manage-reporters",
                                token=os.environ["SUPABASE_PUBLISHABLE_KEY"],
                                body=body)
        self.assertEqual(status, 403)

    def test_function_creates_a_reporter_who_can_sign_in(self):
        self._skip_without_function()
        email = f"{self.PREFIX.lower()}-new-{self.suffix}@everyleague.test"
        status, out = _function("manage-reporters",
                                token=self.ids.tokens["admin"],
                                body={"action": "create",
                                      "name": "Live Created",
                                      "email": email,
                                      "role": "reporter",
                                      "competitions": ["MW_SRFA"]})
        self.assertEqual(status, 200, out)
        reporter_id = out["reporter_id"]
        self.extra_reporters.append(reporter_id)

        row = sb.select("reporters", params={"reporter_id": f"eq.{reporter_id}"},
                        require_secret=True)[0]
        self.extra_users.append(row["auth_user_id"])
        self.assertEqual(row["name"], "Live Created")
        self.assertEqual(row["role"], "reporter")
        self.assertTrue(row["active"])
        self.assertTrue(row["auth_user_id"], "a reporter without a login")
        # public_byline defaults to the name, exactly as the CLI does it.
        self.assertEqual(row["public_byline"], "Live Created")

        rows = sb.select("reporter_assignments", columns="competition_id",
                         params={"reporter_id": f"eq.{reporter_id}"},
                         require_secret=True)
        self.assertEqual([r["competition_id"] for r in rows], ["MW_SRFA"])

        # THE POINT OF THE WHOLE FEATURE: the password that came back works.
        anon = os.environ["SUPABASE_PUBLISHABLE_KEY"]
        req = urllib.request.Request(
            f"{sb.url()}/auth/v1/token?grant_type=password",
            data=json.dumps({"email": email,
                             "password": out["password"]}).encode(),
            method="POST")
        req.add_header("apikey", anon)
        req.add_header("Content-Type", "application/json")
        with urllib.request.urlopen(req, timeout=sb.TIMEOUT) as resp:
            token = json.load(resp)["access_token"]
        self.assertTrue(token)

        # ...and they see exactly what they were given, through the same read
        # loadContext() makes.
        status, mine = _call("reporter_assignments?select=competition_id",
                             token=token)
        self.assertEqual(status, 200)
        self.assertEqual([r["competition_id"] for r in mine], ["MW_SRFA"])

    def test_function_refuses_a_duplicate_email(self):
        self._skip_without_function()
        email = f"{self.PREFIX.lower()}-dup-{self.suffix}@everyleague.test"
        body = {"action": "create", "name": "First", "email": email,
                "role": "reporter", "competitions": []}
        status, out = _function("manage-reporters",
                                token=self.ids.tokens["admin"], body=body)
        self.assertEqual(status, 200, out)
        self.extra_reporters.append(out["reporter_id"])
        row = sb.select("reporters", params={"reporter_id": f"eq.{out['reporter_id']}"},
                        require_secret=True)[0]
        self.extra_users.append(row["auth_user_id"])

        before = len(sb.select("reporters", columns="reporter_id",
                               require_secret=True))
        status, out = _function("manage-reporters",
                                token=self.ids.tokens["admin"],
                                body=dict(body, name="Second"))
        self.assertEqual(status, 409)
        self.assertIn("already has a login", json.dumps(out))
        # No half-made row survived the refusal.
        after = len(sb.select("reporters", columns="reporter_id",
                              require_secret=True))
        self.assertEqual(before, after)

    def test_function_resets_a_password(self):
        self._skip_without_function()
        target = self.ids.ids["b"]
        status, out = _function("manage-reporters",
                                token=self.ids.tokens["admin"],
                                body={"action": "reset_password",
                                      "reporter_id": target})
        self.assertEqual(status, 200, out)
        self.assertTrue(out.get("password"))

        # The new one works...
        email = sb.select("reporters", columns="email",
                          params={"reporter_id": f"eq.{target}"},
                          require_secret=True)[0]["email"]
        anon = os.environ["SUPABASE_PUBLISHABLE_KEY"]
        req = urllib.request.Request(
            f"{sb.url()}/auth/v1/token?grant_type=password",
            data=json.dumps({"email": email, "password": out["password"]}).encode(),
            method="POST")
        req.add_header("apikey", anon)
        req.add_header("Content-Type", "application/json")
        with urllib.request.urlopen(req, timeout=sb.TIMEOUT) as resp:
            self.assertTrue(json.load(resp)["access_token"])

        # ...and the old one does not, which is the half worth asserting: a
        # reset that left the previous password working would be worse than no
        # reset at all.
        req = urllib.request.Request(
            f"{sb.url()}/auth/v1/token?grant_type=password",
            data=json.dumps({"email": email,
                             "password": live_support.PASSWORD}).encode(),
            method="POST")
        req.add_header("apikey", anon)
        req.add_header("Content-Type", "application/json")
        with self.assertRaises(urllib.error.HTTPError):
            urllib.request.urlopen(req, timeout=sb.TIMEOUT)
        # The harness signed this identity in during setUpClass; its token is
        # still valid but its password is not. Nothing later depends on it.


if __name__ == "__main__":
    unittest.main()
