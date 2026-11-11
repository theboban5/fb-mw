#!/usr/bin/env python3
"""Know Your Flames: count, export and draw the quiz entries (0067).

The everyday way is the portal, #/ops?tab=quiz (0068), which does all of this
for an admin on a phone. This is the same thing from a laptop.

Server-side only, like scripts/reporters.py: it reads quiz_entries with
SUPABASE_SECRET_KEY, which bypasses Row Level Security, so it runs on a
trusted machine and nowhere else. The entries are members of the public's
names and phone numbers. They never go in the repo, which is public.

    python3 scripts/quiz.py status
    python3 scripts/quiz.py export --out ~/Desktop/quiz-entries.csv [--quiz flames-egypt-1]
    python3 scripts/quiz.py draw flames-egypt-1 [--dry-run]

`draw` refuses before the round has closed, marks every entry against
src/quiz.py's answers (and, for the Cairo score, against nt_matches once that
match is played), picks the winners at random from the entries that got every
answer right, prints them, and records the draw in quiz_draws, so "who was in
the pool and what counted as right" has an answer afterwards. Run it again
and it draws again, and records that too; the record is the audit, and a
redraw for an unreachable winner is a second row, not an edit.

`export` writes a CSV for a spreadsheet (Google Sheets: File → Import), and
refuses to write it anywhere inside this repository.
"""

import argparse
import csv
import os
import secrets
import sys
from datetime import datetime, timezone

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from src import quiz, supabase_client as sb  # noqa: E402

COLUMNS = ("id", "quiz_id", "created_at", "name", "phone", "email",
           "opt_in_updates", "answers")


def _entries(quiz_id=None):
    params = {"quiz_id": f"eq.{quiz_id}"} if quiz_id else None
    return sb.select("quiz_entries", columns=",".join(COLUMNS),
                     order="id.asc", params=params, require_secret=True)


def answer_key(q):
    """{question_id: winning value} for round `q`, or SystemExit saying why not."""
    key = {}
    for question in q.questions:
        if question.kind != "score":
            key[question.id] = question.answer
            continue
        rows = sb.select("nt_matches",
                         columns="match_id,status,team_score,opponent_score",
                         params={"match_id": f"eq.{question.match_id}"},
                         require_secret=True)
        if not rows or rows[0]["status"] != "played":
            raise SystemExit(
                f"Cannot mark '{question.id}': nt_matches {question.match_id} "
                "has no result yet. Enter it in /report first.")
        row = rows[0]
        key[question.id] = f"{int(row['team_score'])}-{int(row['opponent_score'])}"
    return key


def is_right(entry, key):
    answers = entry.get("answers") or {}
    return all(answers.get(qid) == value for qid, value in key.items())


def cmd_status(_args):
    rows = _entries()
    for q in quiz.QUIZZES:
        mine = [r for r in rows if r["quiz_id"] == q.id]
        updates = sum(1 for r in mine if r["opt_in_updates"])
        print(f"{q.id}: {len(mine)} entries, {updates} opted in to updates "
              f"(closes {q.closes:%a %d %b %H:%M} CAT)")
    phones = {r["phone"] for r in rows}
    subscribers = {r["phone"] for r in rows if r["opt_in_updates"]}
    print(f"{len(phones)} different phone numbers, {len(subscribers)} subscribers")


def cmd_export(args):
    out = os.path.abspath(os.path.expanduser(args.out))
    if os.path.commonpath([out, ROOT]) == ROOT:
        raise SystemExit(f"Refusing to write entries inside the repository "
                         f"({ROOT}): it is public. Pick a path outside it.")
    rows = _entries(args.quiz)
    with open(out, "w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh)
        qids = [qq.id for q in quiz.QUIZZES for qq in q.questions]
        w.writerow([c for c in COLUMNS if c != "answers"] + qids)
        for r in rows:
            answers = r.get("answers") or {}
            w.writerow([r[c] for c in COLUMNS if c != "answers"]
                       + [answers.get(qid, "") for qid in qids])
    print(f"Wrote {len(rows)} entries to {out}")


def cmd_draw(args):
    q = quiz.by_id(args.quiz)
    if q is None:
        raise SystemExit(f"No quiz {args.quiz!r}; one of "
                         + ", ".join(x.id for x in quiz.QUIZZES))
    if datetime.now(timezone.utc) < q.closes and not args.force:
        raise SystemExit(f"{q.id} is still open until {q.closes:%a %d %b %H:%M} "
                         "CAT. Draw after it closes (or --force for a test).")
    key = answer_key(q)
    rows = _entries(q.id)
    # A redraw leaves out everyone already drawn, as admin_draw_quiz (0068)
    # does: the replacement for an unreachable winner is a new person.
    drawn = {i for d in sb.select("quiz_draws", columns="winners",
                                  params={"quiz_id": f"eq.{q.id}"},
                                  require_secret=True)
             for i in d["winners"]}
    pool = [r for r in rows if is_right(r, key) and r["id"] not in drawn]
    n = min(args.winners or q.winners, len(pool))
    winners = secrets.SystemRandom().sample(pool, n)
    print(f"{q.id}: {len(rows)} entries, {len(pool)} with every answer right.")
    print("Answer key: " + ", ".join(f"{k}={v}" for k, v in key.items()))
    for i, w in enumerate(winners, 1):
        print(f"  {i}. {w['name']}  {w['phone']}  {w['email'] or '-'}  "
              f"(entry {w['id']}, {w['created_at']})")
    if not winners:
        print("  Nobody to draw.")
    if args.dry_run:
        print("Dry run: nothing recorded.")
        return
    sb._request("POST", "quiz_draws", body=[{
        "quiz_id": q.id, "entries": len(rows), "pool": len(pool),
        "winners": [w["id"] for w in winners], "answer_key": key,
    }], headers={"Prefer": "return=minimal"}, require_secret=True)
    print("Recorded in quiz_draws.")


def main(argv=None):
    parser = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="cmd", required=True)
    sub.add_parser("status")
    p = sub.add_parser("export")
    p.add_argument("--out", required=True)
    p.add_argument("--quiz")
    p = sub.add_parser("draw")
    p.add_argument("quiz")
    p.add_argument("--winners", type=int)
    p.add_argument("--dry-run", action="store_true")
    p.add_argument("--force", action="store_true",
                   help="draw before the close (testing only)")
    args = parser.parse_args(argv)
    sb.load_dotenv(os.path.join(ROOT, ".env"))
    {"status": cmd_status, "export": cmd_export, "draw": cmd_draw}[args.cmd](args)


if __name__ == "__main__":
    main()
