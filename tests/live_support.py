"""Shared plumbing for the live Supabase tests.

These tests are the only ones that touch a real project, so they are opt-in
(RLS_LIVE=1) and they clean up everything they make. Two rules keep them safe
to run against the production database:

  * every fixture is namespaced (MW_RLSTEST_*, MW_RPCTEST_*) and deleted in
    teardown;
  * NOTHING mutates a real match. A test that needs to publish a result
    creates its own fixture row first. Rewriting a genuine scoreline to prove
    a policy works would be a strange way to protect the data.
"""

import json
import os
import sys
import urllib.error
import urllib.request
import uuid

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from src import supabase_client as sb  # noqa: E402

PASSWORD = "rls-test-" + uuid.uuid4().hex[:12]


def available():
    """Live tests run only when asked, and only when configured."""
    if not os.environ.get("RLS_LIVE"):
        return False
    sb.load_dotenv(os.path.join(ROOT, ".env"))
    return bool(os.environ.get("SUPABASE_URL")
                and os.environ.get("SUPABASE_SECRET_KEY")
                and os.environ.get("SUPABASE_PUBLISHABLE_KEY"))


def call(path, *, token, method="GET", body=None, prefer=None):
    """A PostgREST call made AS a specific identity — or as anon when token is
    None, which is exactly what a visitor to everyleague.co sends."""
    anon = os.environ["SUPABASE_PUBLISHABLE_KEY"]
    req = urllib.request.Request(
        f"{sb.url()}/rest/v1/{path}",
        data=json.dumps(body).encode() if body is not None else None,
        method=method)
    req.add_header("apikey", anon)
    req.add_header("Authorization", f"Bearer {token or anon}")
    req.add_header("Accept", "application/json")
    if body is not None:
        req.add_header("Content-Type", "application/json")
    if prefer:
        req.add_header("Prefer", prefer)
    try:
        with urllib.request.urlopen(req, timeout=sb.TIMEOUT) as resp:
            raw = resp.read().decode()
            return resp.status, (json.loads(raw) if raw.strip() else None)
    except urllib.error.HTTPError as err:
        raw = err.read().decode("utf-8", "replace")
        try:
            return err.code, json.loads(raw)
        except ValueError:
            return err.code, raw


def sign_in(email):
    anon = os.environ["SUPABASE_PUBLISHABLE_KEY"]
    req = urllib.request.Request(
        f"{sb.url()}/auth/v1/token?grant_type=password",
        data=json.dumps({"email": email, "password": PASSWORD}).encode(),
        method="POST")
    req.add_header("apikey", anon)
    req.add_header("Content-Type", "application/json")
    with urllib.request.urlopen(req, timeout=sb.TIMEOUT) as resp:
        return json.load(resp)["access_token"]


def admin_auth(method, path, body=None):
    key = sb.key(require_secret=True)
    req = urllib.request.Request(
        f"{sb.url()}/auth/v1/{path}",
        data=json.dumps(body).encode() if body is not None else None,
        method=method)
    req.add_header("apikey", key)
    req.add_header("Authorization", f"Bearer {key}")
    req.add_header("Content-Type", "application/json")
    with urllib.request.urlopen(req, timeout=sb.TIMEOUT) as resp:
        raw = resp.read().decode()
        return json.loads(raw) if raw.strip() else None


class Identities:
    """Four real signed-in users: assigned, assigned elsewhere, inactive, admin.

    Created through the same tables the CLI writes, then signed in over the
    network so the tests hold genuine JWTs rather than a simulation of one.
    """

    COMP_A = "MW_NRFA"
    COMP_B = "MW_SRFA"

    def __init__(self, prefix="MW_RLSTEST"):
        self.prefix = prefix
        self.suffix = uuid.uuid4().hex[:8]
        self.ids, self.users, self.tokens = {}, {}, {}

    def setup(self):
        plan = [
            ("a", self.COMP_A, True, "reporter"),
            ("b", self.COMP_B, True, "reporter"),
            ("inactive", self.COMP_A, False, "reporter"),
            ("admin", None, True, "admin"),
        ]
        for label, competition, active, role in plan:
            email = f"{self.prefix.lower()}-{label}-{self.suffix}@everyleague.test"
            user = admin_auth("POST", "admin/users", {
                "email": email, "password": PASSWORD, "email_confirm": True})
            reporter_id = f"{self.prefix}_{label.upper()}_{self.suffix}"
            self.ids[label] = reporter_id
            self.users[label] = user["id"]
            sb.upsert("reporters", [{
                "reporter_id": reporter_id, "name": f"Live {label}",
                "email": email, "active": active, "role": role,
                "auth_user_id": user["id"], "ord": 0,
            }], on_conflict="reporter_id")
            if competition:
                sb.upsert("reporter_assignments", [{
                    "reporter_id": reporter_id,
                    "competition_id": competition, "season_id": None,
                }], on_conflict="reporter_id,competition_id,season_id")
            self.tokens[label] = sign_in(email)
        return self

    def teardown(self):
        for label, reporter_id in self.ids.items():
            sb._request("DELETE", "reporter_assignments",
                        query=f"reporter_id=eq.{reporter_id}",
                        headers={"Prefer": "return=minimal"}, require_secret=True)
            sb._request("DELETE", "reporters",
                        query=f"reporter_id=eq.{reporter_id}",
                        headers={"Prefer": "return=minimal"}, require_secret=True)
            admin_auth("DELETE", f"admin/users/{self.users[label]}")


def make_test_match(competition_id, suffix):
    """A throwaway fixture in a real competition, so publishing is harmless.

    Both teams must already hold an entries row for the competition+season —
    the composite foreign key added in 0001 enforces validate.py check 3 — so
    the sides are taken from existing entries rather than invented.
    """
    entries = sb.select(
        "entries", columns="team_id,season_id,competition_id",
        params={"competition_id": f"eq.{competition_id}"},
        order="ord.asc", require_secret=True)
    if len(entries) < 2:
        raise RuntimeError(f"{competition_id} has too few entries to build a fixture")
    home, away = entries[0], entries[1]
    match_id = f"MW_RPCTEST_{suffix}_{competition_id}"
    sb.upsert("matches", [{
        "match_id": match_id,
        "competition_id": competition_id,
        "season_id": home["season_id"],
        "home_team_id": home["team_id"],
        "away_team_id": away["team_id"],
        "stage": "md_1",
        "status": "scheduled",
        "source_type": "placeholder",   # renders nowhere even if one leaks
        "confidence": "unconfirmed",
        "ord": 0,
    }], on_conflict="match_id")
    return sb.select("matches", params={"match_id": f"eq.{match_id}"},
                     require_secret=True)[0]


def drop_test_match(match_id):
    # goals and match_change_log cascade from matches.
    sb._request("DELETE", "matches", query=f"match_id=eq.{match_id}",
                headers={"Prefer": "return=minimal"}, require_secret=True)
