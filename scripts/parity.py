#!/usr/bin/env python3
"""Prove the Supabase-backed build renders the same site as the CSV one.

This is the gate on the data-source migration. Counting rows only shows that
nothing was lost in transit; what actually matters is that every page a
visitor can open is byte-identical, because the public site is the product.
So both sources are built in full and the two trees are compared file by file.

Two differences are expected and are normalized away, each for a stated
reason. Everything else is a regression and is reported.

  * The "last updated" footer stamp is wall-clock — two builds a second apart
    disagree — so it is masked.
  * The search-index cache-buster hashes the source CSV text, and that text is
    legitimately different: Sheets published 865 blank trailing rows and left
    source_type blank where the parser reads 'unknown'. The *parsed* data is
    identical, which is why every other byte must match. The versioned URL is
    masked; the index's own contents are compared like any other file.

Usage:
    python3 scripts/parity.py [--keep]

Requires SUPABASE_URL and a key in the environment (or .env) for the Supabase
half. The CSV half reads data/canonical/ and needs no network.
"""

import argparse
import os
import re
import subprocess
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from src import supabase_client as sb  # noqa: E402

# "15 August 2026, 18:24 CAT" — build.py's `updated` stamp.
UPDATED = re.compile(
    rb"\d{1,2} (?:January|February|March|April|May|June|July|August|September"
    rb"|October|November|December) \d{4}, \d{2}:\d{2} CAT")
# The same stamp's machine-readable half, <time datetime="...">. It carries
# seconds, so two builds a second apart differ here even when the displayed
# minute agrees. Only the full form is masked — a match date renders as a bare
# datetime="YYYY-MM-DD" and must still be compared.
UPDATED_ISO = re.compile(
    rb'datetime="\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}[+-]\d{2}:\d{2}"')
# ?v=<hash> on search-index.json only; the CSS and search.js versions hash
# files on disk and must stay identical, so they are deliberately not masked.
INDEX_VERSION = re.compile(rb"(search-index\.json\?v=)[0-9a-f]+")


def normalize(blob: bytes) -> bytes:
    blob = UPDATED.sub(b"<UPDATED>", blob)
    blob = UPDATED_ISO.sub(b'datetime="<UPDATED>"', blob)
    return INDEX_VERSION.sub(rb"\1<VERSION>", blob)


def build(dist, env_extra):
    env = {**os.environ, **env_extra}
    proc = subprocess.run(
        [sys.executable, os.path.join(ROOT, "build.py"),
         "--dist", dist, "--no-snapshot"],
        cwd=ROOT, env=env, capture_output=True, text=True)
    if proc.returncode != 0:
        sys.stderr.write(proc.stdout + proc.stderr)
        raise SystemExit(f"build failed for {env_extra}")
    return proc.stdout.strip()


def tree(root):
    out = {}
    for dirpath, _dirnames, filenames in os.walk(root):
        for name in filenames:
            full = os.path.join(dirpath, name)
            out[os.path.relpath(full, root)] = full
    return out


def compare(a_root, b_root):
    a, b = tree(a_root), tree(b_root)
    only_a = sorted(set(a) - set(b))
    only_b = sorted(set(b) - set(a))
    differing = []
    for rel in sorted(set(a) & set(b)):
        with open(a[rel], "rb") as fh:
            left = normalize(fh.read())
        with open(b[rel], "rb") as fh:
            right = normalize(fh.read())
        if left != right:
            differing.append(rel)
    return only_a, only_b, differing, len(set(a) & set(b))


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--keep", action="store_true",
                        help="leave both build trees on disk for inspection")
    args = parser.parse_args(argv)

    sb.load_dotenv(os.path.join(ROOT, ".env"))
    work = tempfile.mkdtemp(prefix="parity-")
    csv_dist = os.path.join(work, "from-csv")
    db_dist = os.path.join(work, "from-supabase")

    print("building from data/canonical/ ...")
    print("  " + build(csv_dist, {
        "DATASET_LOCAL_DIR": os.path.join(ROOT, "data", "canonical"),
        "DATASET_SOURCE": "sheets"}))
    print("building from Supabase ...")
    # DATASET_LOCAL_DIR outranks DATASET_SOURCE, so it must be cleared.
    print("  " + build(db_dist, {"DATASET_SOURCE": "supabase",
                                 "DATASET_LOCAL_DIR": ""}))

    only_csv, only_db, differing, shared = compare(csv_dist, db_dist)
    print(f"\ncompared {shared} files present in both trees")

    for label, items in (("only in the CSV build", only_csv),
                         ("only in the Supabase build", only_db),
                         ("differing content", differing)):
        if items:
            print(f"\n{len(items)} {label}:")
            for rel in items[:40]:
                print(f"  {rel}")
            if len(items) > 40:
                print(f"  ... and {len(items) - 40} more")

    ok = not (only_csv or only_db or differing)
    if ok:
        print("\nPARITY OK — the Supabase build is byte-identical.")
    else:
        print("\nPARITY FAILED")
    if args.keep or not ok:
        print(f"\ntrees kept at {work}")
        print(f"  diff -r {csv_dist} {db_dist}")
    else:
        subprocess.run(["rm", "-rf", work], check=False)
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
