"""Command line: generate, preview, doctor.

    python -m social generate                    today's applicable post types
    python -m social generate --date 2026-08-17
    python -m social generate --types roundup,scorers
    python -m social generate --match-id MW_SL_2627_090
    python -m social generate --dry-run          captions to stdout, no rendering
    python -m social preview --type roundup      render one and open the PNG
    python -m social doctor                      environment and data health

Nothing here posts anything, anywhere. It writes files.
"""

import argparse
import datetime
import os
import subprocess
import sys

from . import captions as captions_mod
from . import config, data, output, render, validate
from .posts import base


def main(argv=None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    parser = argparse.ArgumentParser(
        prog="python -m social",
        description="Generate social post packs from the EverLeague data. "
                    "Never publishes; writes files for you to post by hand.")
    sub = parser.add_subparsers(dest="command", required=True)

    gen = sub.add_parser("generate", help="build a post pack")
    _common(gen)
    gen.add_argument("--types", help="comma-separated post types "
                                     "(default: everything available)")
    gen.add_argument("--match-id", help="build a single match as one board")
    gen.add_argument("--days", type=int,
                     help="override a post type's default window in days")
    gen.add_argument("--top-n", type=int, help="scorers: how many to list")
    gen.add_argument("--no-hashtags", action="store_true",
                     help="omit hashtags from every caption")
    gen.add_argument("--dry-run", action="store_true",
                     help="print captions; render nothing, write nothing")
    gen.add_argument("--out", default="", help="output root (default out/social)")

    pre = sub.add_parser("preview", help="render one post type and open it")
    _common(pre)
    pre.add_argument("--type", required=True, help="post type to render")
    pre.add_argument("--match-id")
    pre.add_argument("--days", type=int)
    pre.add_argument("--no-open", action="store_true",
                     help="write the PNG but do not open it")

    doc = sub.add_parser("doctor", help="check environment, assets and data")
    _common(doc)

    args = parser.parse_args(argv)
    if args.command == "generate":
        return cmd_generate(args)
    if args.command == "preview":
        return cmd_preview(args)
    return cmd_doctor(args)


def _common(p) -> None:
    p.add_argument("--date", help="YYYY-MM-DD (default: today in CAT)")
    p.add_argument("--competition",
                   help="competition_id, e.g. MW_SL (scorers, table, fixtures)")


def _load(args) -> "data.Ctx":
    date = (datetime.date.fromisoformat(args.date) if getattr(args, "date", None)
            else data.today())
    ctx = data.load(date)
    ctx.options = {
        k: v for k, v in {
            "match_id": getattr(args, "match_id", None),
            "competition": getattr(args, "competition", None),
            "days": getattr(args, "days", None),
            "top_n": getattr(args, "top_n", None),
        }.items() if v is not None
    }
    return ctx


# ── generate ─────────────────────────────────────────────────────────────────

def cmd_generate(args) -> int:
    ctx = _load(args)
    registry = base.registry()

    if args.types:
        wanted = [t.strip() for t in args.types.split(",") if t.strip()]
        unknown = [t for t in wanted if t not in registry]
        if unknown:
            print(f"unknown post type(s): {', '.join(unknown)}", file=sys.stderr)
            print(f"known: {', '.join(sorted(registry))}", file=sys.stderr)
            return 2
    elif args.match_id:
        # A single match is a results board; nothing else is scoped to one.
        wanted = ["results"]
    else:
        wanted = list(registry)

    report = validate.run(ctx)
    for issue in report.errors:
        print(f"  data error: {issue.message}", file=sys.stderr)

    folder = output.folder_for(ctx.date, args.out)
    if not args.dry_run:
        os.makedirs(folder, exist_ok=True)

    posts, skipped = [], []
    for name in wanted:
        post_type = registry[name]
        available, why = post_type.is_available(ctx)
        if not available:
            skipped.append((name, why))
            continue
        for draft in post_type.build(ctx):
            blocking = report.blocks(
                draft.match_ids, draft.payload.competition_ids)
            if blocking:
                # A validation error stops this board rather than producing a
                # plausible-looking wrong graphic. Credibility is the product.
                skipped.append((draft.key, "; ".join(
                    i.message for i in blocking[:3])))
                continue
            post = output.write_post(draft, ctx.date, folder,
                                     no_hashtags=args.no_hashtags,
                                     dry_run=args.dry_run)
            posts.append(post)
            if args.dry_run:
                _print_post(post)
            else:
                print(f"  {post.key}: {os.path.basename(post.image_path or '')}")

    if args.dry_run:
        _print_skipped(skipped)
        return 0

    warnings = [i.message for i in report.warnings]
    output.write_manifest(folder, ctx.date, posts, skipped, warnings)
    pack = output.write_post_pack(folder, ctx.date, posts, skipped)
    print(f"\n{len(posts)} post(s) in {folder}")
    _print_skipped(skipped)
    print(f"\nOpen the post pack: {pack}")
    return 0


def _print_post(post) -> None:
    print(f"\n{'=' * 60}\n{post.key}  [{post.post_type}]\n{'=' * 60}")
    for w in post.warnings:
        print(f"  ! {w}")
    for platform, text in post.captions.items():
        count = ""
        if platform == "x":
            count = f"  ({captions_mod.x_length(text, output._url_in(text))}/{config.X_LIMIT})"
        print(f"\n--- {platform}{count} ---\n{text}")


def _print_skipped(skipped) -> None:
    if not skipped:
        return
    print("\nnot generated:")
    for name, reason in skipped:
        print(f"  {name}: {reason}")


# ── preview ──────────────────────────────────────────────────────────────────

def cmd_preview(args) -> int:
    ctx = _load(args)
    try:
        post_type = base.get(args.type)
    except KeyError as err:
        print(err, file=sys.stderr)
        return 2

    available, why = post_type.is_available(ctx)
    if not available:
        print(f"{args.type} is not available: {why}", file=sys.stderr)
        return 1

    draft = post_type.build(ctx)[0]
    if not draft.template:
        print(f"{args.type} has no graphic to preview", file=sys.stderr)
        return 1

    path = os.path.join(config.OUT, "preview", f"{draft.key}.png")
    render.render_png(draft.template, draft.context, path)
    print(path)
    if not args.no_open:
        _open(path)
    return 0


def _open(path: str) -> None:
    opener = {"darwin": "open", "win32": "start"}.get(sys.platform, "xdg-open")
    try:
        subprocess.run([opener, path], check=False)
    except FileNotFoundError:
        pass


# ── doctor ───────────────────────────────────────────────────────────────────

def cmd_doctor(args) -> int:
    problems = 0

    print("environment")
    problems += _check("Playwright installed", _playwright_ok(),
                       "pip install playwright")
    problems += _check("Chromium browser", _chromium_ok(),
                       "playwright install chromium")
    problems += _check("Jinja2 installed", _import_ok("jinja2"),
                       "pip install jinja2")
    missing_fonts = [f for f, _r in config.FONT_FILES
                     if not os.path.exists(os.path.join(config.FONTS, f))]
    problems += _check(f"fonts vendored ({len(config.FONT_FILES)} files)",
                       not missing_fonts,
                       f"missing: {', '.join(missing_fonts)} — see social/README.md")

    print("\ndata")
    try:
        ctx = _load(args)
    except Exception as err:
        print(f"  FAIL  loading the dataset: {err}")
        return 1
    print(f"  ok    dataset loaded, active season {ctx.season.label}")

    latest = max((m.date for m in ctx.ds.matches.values()
                  if m.date and m.counts_for_table), default="")
    fresh = bool(latest) and (
        ctx.date - datetime.date.fromisoformat(latest)).days <= 10
    problems += _check(f"data freshness (latest result {latest or 'none'})",
                       fresh, "the sheet may not have this weekend's results yet")

    # Reported, never failed: a missing crest renders a monogram tile, which
    # is a designed fallback rather than a defect. Add a PNG to
    # static/logos/clubs/ named for the club_id to fill one in.
    coverage, missing = _crest_coverage(ctx)
    print(f"  note  crest coverage {coverage:.0f}% "
          f"({len(missing)} team(s) fall back to a monogram)")
    for name in sorted(missing):
        print(f"          {name}")

    report = validate.run(ctx)
    problems += _check(f"validation ({len(report.errors)} error(s))",
                       report.ok,
                       "; ".join(i.message for i in report.errors[:5]))
    if report.warnings:
        print(f"  note  {len(report.warnings)} warning(s); run generate to see "
              f"them per post")

    print("\npost types")
    for name, post_type in sorted(base.registry().items()):
        available, why = post_type.is_available(ctx)
        print(f"  {'ok   ' if available else 'skip '} {name}"
              f"{'' if available else f': {why}'}")

    print("\n" + ("doctor: all checks passed" if not problems
                  else f"doctor: {problems} problem(s)"))
    return 1 if problems else 0


def _check(label: str, ok: bool, hint: str = "") -> int:
    print(f"  {'ok   ' if ok else 'FAIL '} {label}")
    if not ok and hint:
        print(f"        {hint}")
    return 0 if ok else 1


def _import_ok(module: str) -> bool:
    try:
        __import__(module)
        return True
    except ImportError:
        return False


def _playwright_ok() -> bool:
    return _import_ok("playwright")


def _chromium_ok() -> bool:
    if not _playwright_ok():
        return False
    try:
        from playwright.sync_api import sync_playwright
        with sync_playwright() as p:
            browser = p.chromium.launch()
            browser.close()
        return True
    except Exception:
        return False


def _crest_coverage(ctx) -> "tuple[float, set]":
    """Coverage over teams that actually appear in matches, not every team.

    A dormant team with no crest is not a problem worth reporting; one playing
    this weekend is.
    """
    playing = set()
    for m in ctx.ds.matches.values():
        if not m.is_placeholder:
            playing.update((m.home_team_id, m.away_team_id))
    missing = set()
    for team_id in playing:
        team = ctx.ds.teams.get(team_id)
        if team is None:
            continue
        if data.crest_path(team.legacy_code, team.club_id) is None:
            missing.add(team.display_name)
    if not playing:
        return 100.0, missing
    return 100.0 * (len(playing) - len(missing)) / len(playing), missing
