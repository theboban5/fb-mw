#!/usr/bin/env python3
"""Measure how well the importer actually reads Malawian results graphics.

    python3 scripts/import_eval.py                 # run every case
    python3 scripts/import_eval.py --case wp-md4   # run one
    python3 scripts/import_eval.py --model claude-opus-5
    python3 scripts/import_eval.py --json          # machine-readable

WHY THIS IS NOT IN THE TEST SUITE. Every run costs money and needs a key, and
the answer is a score rather than a pass or a fail — "the model read 47 of 51
scorelines correctly" is not a thing a build should turn red over. The unit
tests in tests/js/ cover what we DO with an extraction, which is where the
bugs that matter live; this covers whether the reading is any good, which is a
judgement about a model and changes when the model changes.

Run it when: changing EVERYLEAGUE_IMPORT_MODEL, changing SYSTEM_PROMPT, or
after a run of imports that reporters had to correct heavily.

WHAT IT COMPARES. Only the facts a wrong reading would put on the site: the two
team names as printed, the two scores, and the status. Not `evidence`, not
`notes`, not `fields_not_present` — those are aids to a person, and holding
them to a golden string would make the suite fail every time the model phrased
something differently, which is the fastest way to get an eval set ignored.

Team names are compared with the SAME normalization the database matcher uses
(case, accents and punctuation removed), because "Blue Eagles FC" and "Blue
Eagles fc." are the same read as far as anything downstream is concerned.


ADDING A CASE
-------------
Two files in tests/import_eval/cases/, sharing a name:

    my-case.jpg     the screenshot, exactly as a reporter would send it
    my-case.json    what is actually in it

The JSON:

    {
      "note": "Women's Premiership matchday 4, posted by the league",
      "document_kind": "results",
      "results": [
        {"home": "Blue Eagles", "away": "Silver Strikers",
         "home_score": 2, "away_score": 1, "status": "played"},
        {"home": "DD Sunshine", "away": "Ascent Academy",
         "home_score": null, "away_score": null, "status": "postponed"}
      ]
    }

Write the names EXACTLY AS PRINTED on the image, not as they appear in the
database — the extraction contract is that names come back as printed, and an
expectation written from the database would be marking the model down for
obeying it.

A case with `"results": []` and `"document_kind": "unreadable"` is a valid and
useful case: knowing which pictures are hopeless is worth as much as knowing
which are easy. Real examples that are hard — a photo of a screen at an angle,
a hand-written sheet, a graphic where the scores sit between the badges rather
than between the names — are worth more than clean ones, which already work.

Do not commit a screenshot of anything private. These are public league posts.
"""

import argparse
import base64
import json
import os
import pathlib
import re
import subprocess
import sys
import time
import urllib.error
import urllib.request

NODE = os.environ.get("NODE", "node")

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from src import supabase_client as sb  # noqa: E402

CASES = ROOT / "tests" / "import_eval" / "cases"
API = "https://api.anthropic.com/v1/messages"
API_VERSION = "2023-06-01"

MEDIA = {".jpg": "image/jpeg", ".jpeg": "image/jpeg",
         ".png": "image/png", ".webp": "image/webp"}


def normalize(text):
    """The matcher's rule, in Python: import_normalize() from 0043.

    Kept deliberately identical. An eval that scored a name as wrong when the
    database would have matched it is measuring the wrong thing.
    """
    folded = (text or "")
    for accented, plain in zip("áàâäãéèêëíìîïóòôöõúùûüñçÁÀÂÄÃÉÈÊËÍÌÎÏÓÒÔÖÕÚÙÛÜÑÇ",
                              "aaaaaeeeeiiiiooooouuuuncAAAAAEEEEIIIIOOOOOUUUUNC"):
        folded = folded.replace(accented, plain)
    return re.sub(r"[^A-Za-z0-9]", "", folded).upper()


def load_contract():
    """The schema and prompt, read out of the Edge Function itself.

    NOT A COPY, and not parsed either. A prompt tuned in one place and
    evaluated from another is an eval that stops measuring the thing it is
    named after, silently, the first time somebody edits the real one — so it
    has to come from extract.js.

    Node evaluates it rather than Python parsing it. The first attempt picked
    the schema apart with regexes and died on `enum: DOCUMENT_KINDS`, which is
    an identifier and not a literal: the file is a JavaScript module, so the
    only thing that reliably reads it is a JavaScript engine. Node is already
    needed for `npm test`.
    """
    module = (ROOT / "supabase" / "functions" / "import-extract"
              / "extract.js").as_uri()
    script = (
        f'import("{module}").then((m) => '
        'console.log(JSON.stringify('
        '{schema: m.EXTRACTION_SCHEMA, prompt: m.SYSTEM_PROMPT})))'
    )
    try:
        out = subprocess.run([NODE, "--input-type=module", "-e", script],
                             capture_output=True, text=True, timeout=30,
                             check=True)
    except FileNotFoundError:
        sys.exit(f"{NODE} is not installed. The evaluation reads the schema and "
                 "prompt out of extract.js by evaluating it, so that what is "
                 "measured is exactly what the Edge Function sends.")
    except subprocess.CalledProcessError as err:
        sys.exit(f"could not read extract.js:\n{err.stderr.strip()[:500]}")
    payload = json.loads(out.stdout)
    return payload["schema"], payload["prompt"]


def call_model(image_bytes, media_type, schema, prompt, model, api_key):
    body = {
        "model": model,
        "max_tokens": 8000,
        "system": [{"type": "text", "text": prompt,
                    "cache_control": {"type": "ephemeral"}}],
        "thinking": {"type": "adaptive"},
        "output_config": {"effort": "low",
                          "format": {"type": "json_schema", "schema": schema}},
        "messages": [{"role": "user", "content": [
            {"type": "image", "source": {
                "type": "base64", "media_type": media_type,
                "data": base64.standard_b64encode(image_bytes).decode()}},
            {"type": "text", "text": "Read the football results in this image."},
        ]}],
    }
    request = urllib.request.Request(API, data=json.dumps(body).encode(),
                                     method="POST")
    request.add_header("content-type", "application/json")
    request.add_header("x-api-key", api_key)
    request.add_header("anthropic-version", API_VERSION)
    try:
        with urllib.request.urlopen(request, timeout=180) as response:
            return json.load(response), None
    except urllib.error.HTTPError as err:
        return None, f"HTTP {err.code}: {err.read().decode('utf-8', 'replace')[:300]}"
    except Exception as err:                                  # noqa: BLE001
        return None, str(err)


def extracted_results(message):
    for block in message.get("content", []):
        if block.get("type") == "text":
            try:
                return json.loads(block["text"])
            except ValueError:
                return None
    return None


def name_match(want, got):
    """Would the database have matched these two names to one team?

    Exact after normalization, or either containing the other with something
    substantial left — which is import_team_candidates' tier 1 and tier 5 from
    0043. Not a coincidence: the question this eval is actually asking is "does
    the reading reach the right fixture", and the matcher is what decides that.
    """
    a, b = normalize(want), normalize(got)
    if not a or not b:
        return False
    if a == b:
        return True
    return len(a) >= 6 and len(b) >= 6 and (a in b or b in a)


def score_case(expected, got):
    """Match on the pairing, then check the score and status on it.

    PAIRED, NOT POSITIONAL. The order rows come back in is not something the
    extraction promises, so one missed row would otherwise mark every row after
    it wrong.

    Exact-normalized pairing alone was too strict, and wrongly so: a row read
    as "Blue Eagles FC" against a golden "Blue Eagles" counted as BOTH a missed
    row and an invented one — inflating the two numbers this report exists to
    make trustworthy, over a difference the database would have resolved. So a
    pairing falls back to the matcher's own containment rule, and the drift is
    reported instead of being scored as two failures.
    """
    wants = list(expected.get("results", []))
    haves = list((got or {}).get("results", []))

    right, wrong, drift, missed = 0, [], [], []
    used = set()

    for w in wants:
        hit = None
        # Exact first, so a near-name never steals a row an exact one wanted.
        for tier in (0, 1):
            for i, h in enumerate(haves):
                if i in used:
                    continue
                if tier == 0:
                    ok = (normalize(w["home"]) == normalize(h.get("home_team_raw"))
                          and normalize(w["away"]) == normalize(h.get("away_team_raw")))
                else:
                    ok = (name_match(w["home"], h.get("home_team_raw"))
                          and name_match(w["away"], h.get("away_team_raw")))
                if ok:
                    hit, used = (i, h), used | {i}
                    break
            if hit:
                break

        if not hit:
            missed.append(f"{w['home']} v {w['away']}")
            continue
        i, h = hit
        if (normalize(w["home"]) != normalize(h.get("home_team_raw"))
                or normalize(w["away"]) != normalize(h.get("away_team_raw"))):
            drift.append({"expected": f"{w['home']} v {w['away']}",
                          "got": f"{h.get('home_team_raw')} v "
                                 f"{h.get('away_team_raw')}"})
        if (h.get("home_score") == w.get("home_score")
                and h.get("away_score") == w.get("away_score")
                and h.get("status") == w.get("status")):
            right += 1
        else:
            wrong.append({
                "pairing": f"{w['home']} v {w['away']}",
                "expected": f"{w.get('home_score')}-{w.get('away_score')} "
                            f"{w.get('status')}",
                "got": f"{h.get('home_score')}-{h.get('away_score')} "
                       f"{h.get('status')}",
            })

    return {
        "expected": len(wants), "read": len(haves),
        "correct": right, "wrong": wrong, "drift": drift,
        "missed": missed,
        # THE ONE THAT MATTERS MOST. A row that is not in the picture is an
        # invention, and inventions are the failure this whole design is
        # arranged against — worse than a missed row, which is visible to the
        # reporter as a gap.
        "invented": [f"{h.get('home_team_raw')} v {h.get('away_team_raw')}"
                     for i, h in enumerate(haves) if i not in used],
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--case", help="run one case by name")
    parser.add_argument("--model", default=os.environ.get(
        "EVERYLEAGUE_IMPORT_MODEL", "claude-sonnet-5"))
    parser.add_argument("--json", action="store_true", help="machine-readable")
    args = parser.parse_args()

    sb.load_dotenv(str(ROOT / ".env"))
    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if not api_key:
        sys.exit("ANTHROPIC_API_KEY is not set.\n"
                 "This script calls the real API and costs real money — it is "
                 "the one thing in the repo that does.\n"
                 "Put the key in .env (it is gitignored) or export it.")

    if not CASES.is_dir():
        sys.exit(f"No cases in {CASES.relative_to(ROOT)} — see the docstring "
                 "for how to add one.")

    schema, prompt = load_contract()
    names = sorted({p.stem for p in CASES.glob("*.json")})
    if args.case:
        names = [n for n in names if n == args.case] or sys.exit(
            f"no case called {args.case}")

    totals = {"expected": 0, "read": 0, "correct": 0,
              "missed": 0, "invented": 0, "wrong": 0}
    report = []

    for name in names:
        expected = json.loads((CASES / f"{name}.json").read_text())
        image = next((CASES / f"{name}{ext}" for ext in MEDIA
                      if (CASES / f"{name}{ext}").exists()), None)
        if image is None:
            report.append({"case": name, "error": "no image beside the json"})
            continue

        started = time.time()
        message, error = call_model(image.read_bytes(), MEDIA[image.suffix],
                                    schema, prompt, args.model, api_key)
        if error:
            report.append({"case": name, "error": error})
            continue

        got = extracted_results(message)
        result = score_case(expected, got)
        result["case"] = name
        result["seconds"] = round(time.time() - started, 1)
        usage = message.get("usage", {})
        result["input_tokens"] = usage.get("input_tokens", 0)
        result["output_tokens"] = usage.get("output_tokens", 0)
        report.append(result)

        totals["expected"] += result["expected"]
        totals["read"] += result["read"]
        totals["correct"] += result["correct"]
        totals["missed"] += len(result["missed"])
        totals["invented"] += len(result["invented"])
        totals["wrong"] += len(result["wrong"])

    if args.json:
        print(json.dumps({"model": args.model, "totals": totals,
                          "cases": report}, indent=2))
        return

    print(f"\nmodel: {args.model}\n")
    for row in report:
        if "error" in row:
            print(f"  {row['case']:<24} ERROR  {row['error']}")
            continue
        print(f"  {row['case']:<24} {row['correct']}/{row['expected']} correct"
              f"  ({row['seconds']}s, {row['input_tokens']}+"
              f"{row['output_tokens']} tok)")
        for w in row["wrong"]:
            print(f"      wrong    {w['pairing']}: "
                  f"expected {w['expected']}, got {w['got']}")
        for d in row["drift"]:
            # Not a failure — the matcher's containment tier would still reach
            # the right fixture — but worth seeing, because a model drifting
            # away from "as printed" is how it starts inventing.
            print(f"      drift    {d['expected']}  ->  {d['got']}")
        for m in row["missed"]:
            print(f"      missed   {m}")
        for i in row["invented"]:
            print(f"      INVENTED {i}")

    print(f"\n  {totals['correct']}/{totals['expected']} scorelines correct"
          f"   {totals['missed']} missed   {totals['wrong']} misread"
          f"   {totals['invented']} invented")
    if totals["invented"]:
        # Held out as its own line because it is a different KIND of failure: a
        # missed row is visible to the reporter, an invented one arrives
        # looking exactly like a real one.
        print("\n  An invented row is the serious one — it reaches the review "
              "screen looking like every other row.")
    print()


if __name__ == "__main__":
    main()
