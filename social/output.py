"""Turn drafts into a folder: PNGs, caption files, manifest, and the post pack.

Layout, one folder per date:

    out/social/YYYY-MM-DD/
      <key>.png
      <key>.captions.json     every platform, plus alt text and the link
      <key>.whatsapp.txt      plain files, for copy-paste on a desktop
      <key>.facebook.txt
      <key>.x.txt
      <key>.instagram.txt
      index.html              the post pack — the thing you actually use
      manifest.json           what was generated, what was skipped and why
"""

import datetime
import json
import os

from . import captions as captions_mod
from . import config, render
from .posts import base


def folder_for(date: datetime.date, root: str = "") -> str:
    return os.path.join(root or config.OUT, date.isoformat())


def write_post(draft: "base.Draft", date: datetime.date, folder: str,
               no_hashtags: bool = False, dry_run: bool = False) -> "base.Post":
    """Render one draft and write everything belonging to it."""
    texts, caption_warnings = captions_mod.render(draft.payload, no_hashtags)
    warnings = list(draft.warnings) + caption_warnings

    image_path = None
    if not dry_run and draft.template:
        image_path = os.path.join(folder, f"{draft.key}.png")
        render.render_png(draft.template, draft.context, image_path)

    post = base.Post(
        post_type=draft.post_type,
        date=date,
        key=draft.key,
        captions=texts,
        image_path=image_path,
        link=captions_mod.link_for("whatsapp", draft.payload),
        alt_text=draft.alt_text,
        warnings=warnings,
    )
    if not dry_run:
        _write_captions(post, draft, folder)
    return post


def _write_captions(post: "base.Post", draft: "base.Draft", folder: str) -> None:
    os.makedirs(folder, exist_ok=True)
    bundle = {
        "post_type": post.post_type,
        "key": post.key,
        "date": post.date.isoformat(),
        "alt_text": post.alt_text,
        "captions": post.captions,
        # Per platform, because each carries its own utm_source.
        "links": {p: captions_mod.link_for(p, draft.payload)
                  for p in config.PLATFORMS},
        "warnings": post.warnings,
    }
    with open(os.path.join(folder, f"{post.key}.captions.json"), "w",
              encoding="utf-8") as fh:
        json.dump(bundle, fh, indent=2, ensure_ascii=False)
        fh.write("\n")
    for platform, text in post.captions.items():
        with open(os.path.join(folder, f"{post.key}.{platform}.txt"), "w",
                  encoding="utf-8") as fh:
            fh.write(text + "\n")


def write_manifest(folder: str, date: datetime.date, posts, skipped,
                   warnings) -> str:
    """What ran, what did not, and why — the run's audit trail."""
    path = os.path.join(folder, "manifest.json")
    payload = {
        "date": date.isoformat(),
        "generated_at": datetime.datetime.now(
            datetime.timezone(datetime.timedelta(hours=config.TZ_OFFSET_HOURS))
        ).isoformat(),
        "posts": [
            {
                "key": p.key,
                "post_type": p.post_type,
                "image": os.path.basename(p.image_path) if p.image_path else None,
                "warnings": p.warnings,
            }
            for p in posts
        ],
        "skipped": [{"post_type": t, "reason": r} for t, r in skipped],
        "data_warnings": warnings,
    }
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(payload, fh, indent=2, ensure_ascii=False)
        fh.write("\n")
    return path


def write_post_pack(folder: str, date: datetime.date, posts, skipped) -> str:
    """The mobile page. Self-contained, no build step, no network."""
    env = render.environment()
    cards = []
    for p in posts:
        cards.append({
            "key": p.key,
            "post_type": p.post_type,
            "image": os.path.basename(p.image_path) if p.image_path else "",
            "alt_text": p.alt_text,
            "warnings": p.warnings,
            "captions": p.captions,
            # X counts a link as 23 characters, so the counter on the page has
            # to weight it the same way or it will cry wolf on every post.
            "x_length": captions_mod.x_length(
                p.captions.get("x", ""), _url_in(p.captions.get("x", ""))),
        })
    html = env.get_template("postpack.html").render(
        date=date,
        date_label=date.strftime("%A %d %B %Y"),
        cards=cards,
        skipped=skipped,
        platforms=config.PLATFORMS,
        platform_labels=config.PLATFORM_LABELS,
        x_limit=config.X_LIMIT,
        tokens=config.TOKENS,
    )
    path = os.path.join(folder, "index.html")
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(html)
    return path


def _url_in(text: str) -> str:
    for word in text.split():
        if word.startswith("http"):
            return word
    return ""
