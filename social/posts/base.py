"""The post-type contract, and the two records that flow through the pipeline.

A post type answers two questions and nothing else:

    is_available(ctx) -> (bool, reason)   can this be built from today's data?
    build(ctx)        -> [Draft]          what should be drawn and written?

It never renders, never writes files and never decides where a link points to
beyond naming a path. That keeps `--dry-run` honest (captions without touching
Chromium) and keeps every post type readable end to end.

Deviation from the original design note: `build` returns a *list* of drafts,
not one. A Saturday's results are one board per competition, so a single post
type legitimately produces several boards — each needing its own captions and
its own filename.
"""

import datetime
from dataclasses import dataclass, field

from .. import captions


@dataclass(frozen=True)
class Draft:
    """One board, described but not yet drawn."""
    key: str                 # filename stem, e.g. "results-sl" — must be unique
    post_type: str
    template: str            # template filename, or "" for a caption-only post
    context: "dict"          # template variables
    payload: "captions.Payload"
    alt_text: str
    warnings: "tuple[str, ...]" = ()
    # Which matches this board is built from, so a validation error can block
    # exactly the boards it affects rather than the whole run.
    match_ids: "tuple[str, ...]" = ()


@dataclass
class Post:
    """One finished board: what the post pack and the manifest are built from."""
    post_type: str
    date: datetime.date
    key: str
    captions: "dict[str, str]"     # platform -> final caption text
    image_path: "str | None"
    link: "str | None"             # already UTM-tagged
    alt_text: str
    warnings: "list[str]" = field(default_factory=list)


class PostType:
    """Base class. Subclasses set `name` and implement both methods."""

    name = ""

    def is_available(self, ctx) -> "tuple[bool, str]":
        """(True, "") when there is enough data, else (False, why not).

        Incomplete data is a skipped post, never an estimated one.
        """
        raise NotImplementedError

    def build(self, ctx) -> "list[Draft]":
        raise NotImplementedError


# ── Registry ─────────────────────────────────────────────────────────────────

_REGISTRY: "dict[str, PostType]" = {}


def register(post_type: PostType) -> PostType:
    _REGISTRY[post_type.name] = post_type
    return post_type


def registry() -> "dict[str, PostType]":
    # Imported here so a post type module can import this one at load time.
    from . import fixtures, flex, results, roundup, scorers, table  # noqa: F401
    return dict(_REGISTRY)


def get(name: str) -> PostType:
    reg = registry()
    if name not in reg:
        raise KeyError(
            f"unknown post type {name!r}; known: {', '.join(sorted(reg))}")
    return reg[name]


# ── Shared helpers ───────────────────────────────────────────────────────────

def density_for(count: int) -> str:
    """One match is a hero board, a handful is a list. Shared so every
    template that shows matches breaks at the same counts."""
    if count <= 1:
        return "hero"
    if count <= 3:
        return "roomy"
    return "tight"


# Above this many rows a board stops being readable at thumbnail size, so the
# post type splits into several boards rather than shrinking the type.
MAX_ROWS = 6


def paginate(items: "list", per_board: int = MAX_ROWS) -> "list[list]":
    """Split into boards of at most `per_board`, balanced so the last board
    is never a lonely single row."""
    if len(items) <= per_board:
        return [items]
    boards = -(-len(items) // per_board)          # ceil
    size = -(-len(items) // boards)               # even out across boards
    return [items[i:i + size] for i in range(0, len(items), size)]
