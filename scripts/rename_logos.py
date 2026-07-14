#!/usr/bin/env python3
"""One-off: rename logo files from old sheet codes to new-schema IDs.

Club crests:  static/logos/clubs/{legacy_code}.png -> {club_id}.png
              (mapping derived from the teams tab: legacy_code -> club_id;
              never guessed from filenames)
League logos: static/logos/leagues/{slug}.png -> logos/competitions/{competition_id}.png

Where two squads of one club both have a logo file (e.g. SL_BE and CRFA_BER
are both club MW_BLUE), the squad_level-1 team's file becomes the club crest
and the other file is reported and left in place for manual review.

Dry-run by default; pass --apply to actually rename (uses `git mv` so the
rename lands as its own reviewable commit).

Usage:
    python scripts/rename_logos.py [--apply]
"""

import os
import subprocess
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from src import adapt, dataset  # noqa: E402

CLUBS_DIR = os.path.join(ROOT, "static", "logos", "clubs")
LEAGUES_DIR = os.path.join(ROOT, "static", "logos", "leagues")
COMPETITIONS_DIR = os.path.join(ROOT, "static", "logos", "competitions")


def _git_mv(src, dst, apply):
    rel_src = os.path.relpath(src, ROOT)
    rel_dst = os.path.relpath(dst, ROOT)
    print(f"  {rel_src} -> {rel_dst}")
    if apply:
        subprocess.run(["git", "mv", rel_src, rel_dst], cwd=ROOT, check=True)


def main(argv):
    apply = "--apply" in argv
    if not apply:
        print("DRY RUN — pass --apply to rename\n")
    ds = dataset.load()

    # legacy_code -> team, so each existing file maps to exactly one club.
    by_legacy = {t.legacy_code: t for t in ds.teams.values() if t.legacy_code}

    print("Club crests:")
    renamed_clubs = set()
    leftovers = []
    # squad_level 1 first, so the first team's crest wins for its club.
    for team in sorted(by_legacy.values(), key=lambda t: t.squad_level):
        for ext in (".svg", ".png"):
            src = os.path.join(CLUBS_DIR, team.legacy_code + ext)
            if not os.path.exists(src):
                continue
            dst = os.path.join(CLUBS_DIR, team.club_id + ext)
            if team.club_id in renamed_clubs or os.path.exists(dst):
                leftovers.append((src, team))
                continue
            _git_mv(src, dst, apply)
            renamed_clubs.add(team.club_id)

    if leftovers:
        print("\nLeft in place (club crest already exists; review manually):")
        for src, team in leftovers:
            print(f"  {os.path.relpath(src, ROOT)} "
                  f"({team.team_id} -> club {team.club_id})")

    unmatched = [
        f for f in sorted(os.listdir(CLUBS_DIR))
        if f.rsplit(".", 1)[0] not in by_legacy
        and not f.startswith(("MW_",))
        and f != ".DS_Store"
    ] if os.path.isdir(CLUBS_DIR) else []
    if unmatched:
        print("\nNo teams-tab legacy_code for (left in place):")
        for f in unmatched:
            print(f"  static/logos/clubs/{f}")

    print("\nLeague logos:")
    os.makedirs(COMPETITIONS_DIR, exist_ok=True)
    slug_of = {adapt.competition_slug(c.competition_id, c.country): c.competition_id
               for c in ds.competitions.values()}
    if os.path.isdir(LEAGUES_DIR):
        for f in sorted(os.listdir(LEAGUES_DIR)):
            stem, dot, ext = f.rpartition(".")
            comp_id = slug_of.get(stem)
            if comp_id is None:
                print(f"  (no competition for static/logos/leagues/{f}; left in place)")
                continue
            _git_mv(os.path.join(LEAGUES_DIR, f),
                    os.path.join(COMPETITIONS_DIR, f"{comp_id}.{ext}"), apply)

    if not apply:
        print("\nDRY RUN — nothing was renamed")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
