"""Flex — hand-written content rendered in-brand.

This is how a poll, a Backroom Staff crosspost or an announcement gets made
without opening Canva. Drop a file at `social/flex/<date>.json` (or .yml if
PyYAML happens to be installed) and it renders with the same eyebrow, type and
watermark as every other board.

    {
      "headline": "Backroom Staff, episode 14",
      "body": "Why the Super League's away form collapsed after April.",
      "stat": "62%",                     optional — the big figure
      "stat_label": "of goals at home",  optional
      "eyebrow": "Backroom Staff",       optional, defaults to EverLeague
      "kind": "Podcast",                 optional
      "path": "/",                       optional, for the link
      "hashtags": true                   optional
    }

Nothing here is validated against the football data, because none of it comes
from the football data. It is the one post type where the words are yours.
"""

import json
import os

from .. import captions, config
from . import base


class Flex(base.PostType):
    name = "flex"

    def _path(self, ctx) -> "str | None":
        explicit = ctx.options.get("flex_file")
        if explicit:
            return explicit if os.path.exists(explicit) else None
        for ext in ("json", "yml", "yaml"):
            path = os.path.join(config.FLEX_DIR, f"{ctx.date.isoformat()}.{ext}")
            if os.path.exists(path):
                return path
        return None

    def is_available(self, ctx):
        path = self._path(ctx)
        if not path:
            return False, (f"no flex file at "
                           f"{os.path.join(config.FLEX_DIR, ctx.date.isoformat())}"
                           f".json")
        try:
            content = _load(path)
        except Exception as err:
            return False, f"{os.path.basename(path)}: {err}"
        if not content.get("headline"):
            return False, f"{os.path.basename(path)}: no headline"
        return True, ""

    def build(self, ctx):
        path = self._path(ctx)
        content = _load(path)
        payload = captions.Payload(
            headline=content["headline"],
            lines=[line for line in str(content.get("body", "")).split("\n") if line],
            note=content.get("note", ""),
            campaign=content.get("campaign", "flex"),
            path=content.get("path", "/"),
        )
        return [base.Draft(
            key="flex",
            post_type=self.name,
            template="flex.html",
            context={
                "eyebrow": content.get("eyebrow", "EverLeague"),
                "kind": content.get("kind", "") or "Notice",
                "standfirst_left": content.get("standfirst", ""),
                "standfirst_right": "",
                "headline": content["headline"],
                "body": content.get("body", ""),
                "stat": content.get("stat", ""),
                "stat_label": content.get("stat_label", ""),
                "season_label": "",
            },
            payload=payload,
            alt_text=content.get(
                "alt_text",
                f"{content['headline']}. {content.get('body', '')}".strip()),
            warnings=(),
        )]


def _load(path: str) -> dict:
    with open(path, encoding="utf-8") as fh:
        text = fh.read()
    if path.endswith((".yml", ".yaml")):
        try:
            import yaml
        except ImportError:
            raise RuntimeError(
                "YAML needs PyYAML installed; rename the file to .json to "
                "avoid the dependency")
        return yaml.safe_load(text) or {}
    return json.loads(text)


base.register(Flex())
