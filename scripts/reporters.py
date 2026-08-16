#!/usr/bin/env python3
"""Reporter administration. Server-side only — never runs in a browser.

Deliberately a CLI and not a portal: reporters are created a handful of times a
season, and an admin web surface would be a large attack surface guarding a
rare operation. Everything here uses SUPABASE_SECRET_KEY, which bypasses Row
Level Security, so it must only ever run on a trusted machine.

    python3 scripts/reporters.py create  --name "James Banda" \
                                         --email james@example.com \
                                         --competition MW_NRFA [--competition ...]
    python3 scripts/reporters.py assign  --reporter MW_REP_001 --competition MW_SRFA
    python3 scripts/reporters.py unassign --reporter MW_REP_001 --competition MW_SRFA
    python3 scripts/reporters.py deactivate --reporter MW_REP_001
    python3 scripts/reporters.py activate   --reporter MW_REP_001
    python3 scripts/reporters.py list
    python3 scripts/reporters.py password --reporter MW_REP_001

`create` makes the auth user with the email already confirmed, so no SMTP is
needed: hand the reporter the printed password and they can sign in at
/report/login immediately. The password is shown ONCE, at creation. The secret
key itself is never printed or logged.
"""

import argparse
import os
import secrets
import string
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from src import supabase_client as sb  # noqa: E402

REPORTER_PREFIX = "MW_REP_"

# Unambiguous alphabet: no O/0, l/1/I. These get read aloud over WhatsApp and
# typed on a phone keyboard, where a mistyped password is a support call.
ALPHABET = "".join(c for c in string.ascii_letters + string.digits
                   if c not in "O0lI1")


def generate_password(length=14):
    return "".join(secrets.choice(ALPHABET) for _ in range(length))


def _auth_admin(method, path, body=None):
    """Call the GoTrue admin API. Requires the secret key."""
    import json
    import urllib.error
    import urllib.request

    api_key = sb.key(require_secret=True)
    req = urllib.request.Request(
        f"{sb.url()}/auth/v1/{path}",
        data=json.dumps(body).encode("utf-8") if body is not None else None,
        method=method)
    req.add_header("apikey", api_key)
    req.add_header("Authorization", f"Bearer {api_key}")
    req.add_header("Content-Type", "application/json")
    try:
        with urllib.request.urlopen(req, timeout=sb.TIMEOUT) as resp:
            raw = resp.read().decode("utf-8")
            return json.loads(raw) if raw.strip() else None
    except urllib.error.HTTPError as err:
        detail = err.read().decode("utf-8", "replace").strip()
        raise sb.SupabaseError(f"auth {path} -> HTTP {err.code}: {detail}") from None


def next_reporter_id():
    """MW_REP_001, MW_REP_002, ... — the same shape as every other id here."""
    rows = sb.select("reporters", columns="reporter_id", require_secret=True)
    used = []
    for row in rows:
        rid = row["reporter_id"]
        if rid.startswith(REPORTER_PREFIX) and rid[len(REPORTER_PREFIX):].isdigit():
            used.append(int(rid[len(REPORTER_PREFIX):]))
    return f"{REPORTER_PREFIX}{(max(used) + 1) if used else 1:03d}"


def _find_reporter(reporter_id):
    rows = sb.select("reporters", params={"reporter_id": f"eq.{reporter_id}"},
                     require_secret=True)
    if not rows:
        raise SystemExit(f"no reporter {reporter_id!r} (try: list)")
    return rows[0]


def _check_competitions(ids):
    known = {r["competition_id"] for r in
             sb.select("competitions", columns="competition_id")}
    unknown = [c for c in ids if c not in known]
    if unknown:
        raise SystemExit(
            f"unknown competition(s): {', '.join(unknown)}\n"
            f"known: {', '.join(sorted(known))}")


def cmd_create(args):
    competitions = list(dict.fromkeys(args.competition))
    if competitions:
        _check_competitions(competitions)
    password = args.password or generate_password()

    # The auth user first: if this fails, no orphan reporter row is left behind.
    user = _auth_admin("POST", "admin/users", {
        "email": args.email,
        "password": password,
        # No SMTP configured, and a reporter in the field cannot be asked to
        # complete an email round trip before their first match report.
        "email_confirm": True,
    })
    reporter_id = args.reporter_id or next_reporter_id()
    try:
        sb.upsert("reporters", [{
            "reporter_id": reporter_id,
            "name": args.name,
            "email": args.email,
            "affiliation": args.affiliation,
            "region": args.region,
            "public_byline": args.byline or args.name,
            "active": True,
            "role": "admin" if args.admin else "reporter",
            "auth_user_id": user["id"],
            "ord": 0,
        }], on_conflict="reporter_id")
    except sb.SupabaseError:
        # Roll the login back so a retry is not blocked by a half-made account.
        _auth_admin("DELETE", f"admin/users/{user['id']}")
        raise

    for competition_id in competitions:
        sb.upsert("reporter_assignments",
                  [{"reporter_id": reporter_id,
                    "competition_id": competition_id,
                    "season_id": args.season}],
                  on_conflict="reporter_id,competition_id,season_id")

    print(f"created {reporter_id}  {args.name} <{args.email}>"
          + ("  [ADMIN]" if args.admin else ""))
    if competitions:
        print(f"  assigned: {', '.join(competitions)}"
              + (f" (season {args.season})" if args.season else " (all seasons)"))
    else:
        print("  assigned: nothing yet — use `assign` before they can report")
    print(f"\n  temporary password: {password}")
    print("  Shown once. Send it over a private channel; they can change it "
          "in /report.")
    return 0


def cmd_assign(args):
    _find_reporter(args.reporter)
    _check_competitions([args.competition])
    sb.upsert("reporter_assignments",
              [{"reporter_id": args.reporter,
                "competition_id": args.competition,
                "season_id": args.season}],
              on_conflict="reporter_id,competition_id,season_id")
    scope = f"season {args.season}" if args.season else "all seasons"
    print(f"{args.reporter} may now report {args.competition} ({scope})")
    return 0


def _check_nt_team(team_code):
    rows = sb.select("nt_teams", columns="team_code",
                     params={"team_code": f"eq.{team_code}"}, require_secret=True)
    if not rows:
        known = [t["team_code"] for t in
                 sb.select("nt_teams", columns="team_code", require_secret=True)]
        raise sb.SupabaseError(
            f"no national team {team_code!r}. Known: {', '.join(sorted(known))}")


def cmd_nt_assign(args):
    """Grant one national team.

    Separate from `assign` because a national team is not a competition: it
    has no season, and the tables it unlocks (nt_matches, nt_goals,
    nt_lineups, nt_squads) are a different schema from the club side. An
    administrator needs none of this — can_edit_nt() already grants them
    everything, exactly as can_report_match() does.
    """
    _find_reporter(args.reporter)
    _check_nt_team(args.team)
    sb.upsert("nt_assignments",
              [{"reporter_id": args.reporter, "team_code": args.team}],
              on_conflict="reporter_id,team_code")
    print(f"{args.reporter} may now report {args.team}")
    return 0


def cmd_nt_unassign(args):
    _find_reporter(args.reporter)
    sb._request("DELETE", "nt_assignments",
                query=f"reporter_id=eq.{args.reporter}&team_code=eq.{args.team}",
                headers={"Prefer": "return=minimal"}, require_secret=True)
    print(f"{args.reporter} may no longer report {args.team}")
    return 0


def cmd_unassign(args):
    _find_reporter(args.reporter)
    query = (f"reporter_id=eq.{args.reporter}"
             f"&competition_id=eq.{args.competition}")
    query += f"&season_id=eq.{args.season}" if args.season else "&season_id=is.null"
    sb._request("DELETE", "reporter_assignments", query=query,
                headers={"Prefer": "return=minimal"}, require_secret=True)
    print(f"{args.reporter} can no longer report {args.competition}")
    return 0


def _set_active(reporter_id, active):
    _find_reporter(reporter_id)
    sb._request("PATCH", "reporters", query=f"reporter_id=eq.{reporter_id}",
                body={"active": active}, headers={"Prefer": "return=minimal"},
                require_secret=True)
    # Assignments are deliberately left in place: deactivating is reversible and
    # is not the same as forgetting what someone covered. `active` alone gates
    # every authorization check (see can_report_match).
    print(f"{reporter_id} is now {'active' if active else 'INACTIVE'}")
    return 0


def cmd_deactivate(args):
    return _set_active(args.reporter, False)


def cmd_activate(args):
    return _set_active(args.reporter, True)


def cmd_password(args):
    reporter = _find_reporter(args.reporter)
    if not reporter.get("auth_user_id"):
        raise SystemExit(f"{args.reporter} has no login to reset")
    password = args.password or generate_password()
    _auth_admin("PUT", f"admin/users/{reporter['auth_user_id']}",
                {"password": password})
    print(f"{args.reporter} password reset\n\n  new password: {password}")
    return 0


def cmd_list(args):
    reporters = sb.select("reporters", order="reporter_id.asc",
                          require_secret=True)
    if not reporters:
        print("no reporters yet — create one with `create`")
        return 0
    assignments = sb.select("reporter_assignments",
                            order="reporter_id.asc", require_secret=True)
    by_reporter = {}
    for a in assignments:
        label = a["competition_id"]
        if a.get("season_id"):
            label += f"@{a['season_id']}"
        by_reporter.setdefault(a["reporter_id"], []).append(label)
    nt_by_reporter = {}
    for a in sb.select("nt_assignments", order="reporter_id.asc",
                       require_secret=True):
        nt_by_reporter.setdefault(a["reporter_id"], []).append(a["team_code"])

    for r in reporters:
        flags = []
        if r.get("role") == "admin":
            flags.append("ADMIN")
        if not r.get("active"):
            flags.append("INACTIVE")
        if not r.get("auth_user_id"):
            flags.append("NO LOGIN")
        suffix = f"  [{', '.join(flags)}]" if flags else ""
        print(f"{r['reporter_id']}  {r['name']} <{r.get('email', '')}>{suffix}")
        covers = by_reporter.get(r["reporter_id"], [])
        print(f"    {', '.join(covers) if covers else '(no competitions)'}")
        # Only worth a line when there is one: an admin reports every national
        # team without an assignment, so a blank here is not a gap.
        nt = nt_by_reporter.get(r["reporter_id"], [])
        if nt:
            print(f"    national teams: {', '.join(sorted(nt))}")
    return 0


def main(argv=None):
    parser = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser("create", help="create a reporter and their login")
    p.add_argument("--name", required=True)
    p.add_argument("--email", required=True)
    p.add_argument("--password", help="omit to generate a strong one")
    p.add_argument("--competition", action="append", default=[],
                   help="repeatable")
    p.add_argument("--season", help="restrict to one season (default: all)")
    p.add_argument("--affiliation", default="")
    p.add_argument("--region", default="")
    p.add_argument("--byline", default="", help="defaults to --name")
    p.add_argument("--admin", action="store_true",
                   help="may report every competition")
    p.add_argument("--reporter-id", help="override the generated MW_REP_NNN")
    p.set_defaults(func=cmd_create)

    p = sub.add_parser("assign", help="grant a competition")
    p.add_argument("--reporter", required=True)
    p.add_argument("--competition", required=True)
    p.add_argument("--season")
    p.set_defaults(func=cmd_assign)

    p = sub.add_parser("unassign", help="revoke a competition")
    p.add_argument("--reporter", required=True)
    p.add_argument("--competition", required=True)
    p.add_argument("--season")
    p.set_defaults(func=cmd_unassign)

    p = sub.add_parser("nt-assign", help="grant a national team")
    p.add_argument("--reporter", required=True)
    p.add_argument("--team", required=True, help="an nt_teams code, e.g. MW_W")
    p.set_defaults(func=cmd_nt_assign)

    p = sub.add_parser("nt-unassign", help="revoke a national team")
    p.add_argument("--reporter", required=True)
    p.add_argument("--team", required=True)
    p.set_defaults(func=cmd_nt_unassign)

    p = sub.add_parser("deactivate", help="revoke all reporting rights")
    p.add_argument("--reporter", required=True)
    p.set_defaults(func=cmd_deactivate)

    p = sub.add_parser("activate", help="restore reporting rights")
    p.add_argument("--reporter", required=True)
    p.set_defaults(func=cmd_activate)

    p = sub.add_parser("password", help="reset a reporter's password")
    p.add_argument("--reporter", required=True)
    p.add_argument("--password", help="omit to generate a strong one")
    p.set_defaults(func=cmd_password)

    p = sub.add_parser("list", help="reporters and their competitions")
    p.set_defaults(func=cmd_list)

    args = parser.parse_args(argv)
    sb.load_dotenv(os.path.join(ROOT, ".env"))
    try:
        sb.key(require_secret=True)
        return args.func(args)
    except sb.SupabaseError as err:
        print(f"ERROR: {err}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
