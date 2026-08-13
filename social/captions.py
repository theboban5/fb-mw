"""One structured payload in, four platform captions out.

Post types never write platform-specific text. They describe what happened —
a headline, some body lines, which competitions are involved — and this module
renders that for each platform's real constraints. That way a change to how X
counts URLs, or to the hashtag policy, happens in one file.

The house voice rules (config.VOICE_RULES) are enforced here where they can be
enforced mechanically; the rest are a matter of what post types put in `lines`.
"""

from dataclasses import dataclass, field

from . import config


class VoiceError(Exception):
    """Generated copy broke a house rule. Always a bug, never a data problem."""


@dataclass
class Payload:
    """What happened, in a form every platform can be rendered from."""
    headline: str
    lines: "list[str]" = field(default_factory=list)
    # A qualifier that must survive truncation on every platform that keeps
    # the lines it qualifies — e.g. "Scorers where recorded."
    note: str = ""
    campaign: str = "post"
    path: str = "/"
    competition_ids: "tuple[str, ...]" = ()
    # One emoji from the allowlist, used positionally at the head of the
    # headline. Never sprinkled through the body.
    emoji: str = ""


def render(payload: Payload, no_hashtags: bool = False) -> "tuple[dict, list[str]]":
    """Return ({platform: caption}, warnings)."""
    _check_voice(payload)
    warnings: "list[str]" = []
    out = {}
    for platform in config.PLATFORMS:
        text, warns = _render_one(platform, payload, no_hashtags)
        out[platform] = text
        warnings.extend(warns)
    return out, warnings


def _render_one(platform: str, p: Payload, no_hashtags: bool):
    tags = () if no_hashtags else config.hashtags(platform, p.competition_ids)
    if platform == "whatsapp":
        return _whatsapp(p), []
    if platform == "facebook":
        return _facebook(p, tags), []
    if platform == "x":
        return _x(p, tags)
    if platform == "instagram":
        return _instagram(p, tags), []
    raise ValueError(f"unknown platform {platform!r}")


def headline_text(p: Payload) -> str:
    return f"{p.emoji} {p.headline}".strip() if p.emoji else p.headline


def link_for(platform: str, p: Payload) -> str:
    return config.tagged_link(platform, p.campaign, p.path)


# ── Platforms ────────────────────────────────────────────────────────────────

def _whatsapp(p: Payload) -> str:
    """Plain text with WhatsApp markup. Line breaks are free here — use them.

    The URL goes on its own line with nothing after it, which is what makes
    WhatsApp render it as a tappable link rather than running it into the
    surrounding words.
    """
    parts = [f"*{headline_text(p)}*", ""]
    parts += p.lines
    if p.note:
        parts += ["", f"_{p.note}_"]
    parts += ["", link_for("whatsapp", p)]
    return "\n".join(parts)


def _facebook(p: Payload, tags) -> str:
    """Longer form is fine; the link goes last, on its own line."""
    parts = [headline_text(p), ""]
    parts += p.lines
    if p.note:
        parts += ["", p.note]
    if tags:
        parts += ["", " ".join(tags)]
    parts += ["", link_for("facebook", p)]
    return "\n".join(parts)


def _x(p: Payload, tags):
    """Hard 280 including the URL, which always counts as 23 characters.

    When it does not fit, whole body lines are dropped from the end and
    replaced with a count of what was left out. The link and the headline are
    never dropped — a truncated post that still links to the full results is
    useful; one that silently loses the link is not.
    """
    warnings: "list[str]" = []
    url = link_for("x", p)
    head = headline_text(p)
    tag_line = " ".join(tags)

    def assemble(lines, dropped):
        parts = [head]
        if lines:
            parts += [""] + list(lines)
        if dropped:
            parts += [f"+{dropped} more"]
        if tag_line:
            parts += ["", tag_line]
        parts += ["", url]
        return "\n".join(parts)

    def weighted(text):
        # Every link counts as 23 characters on X regardless of real length.
        return len(text) - len(url) + config.X_URL_WEIGHT

    lines = list(p.lines)
    dropped = 0
    text = assemble(lines, dropped)
    while weighted(text) > config.X_LIMIT and lines:
        lines.pop()
        dropped += 1
        text = assemble(lines, dropped)

    if weighted(text) > config.X_LIMIT and tag_line:
        tag_line = ""
        text = assemble(lines, dropped)

    if weighted(text) > config.X_LIMIT:
        # Nothing left to drop but the headline itself. Trim it rather than
        # emit an over-length caption the user would have to fix by hand.
        room = config.X_LIMIT - (weighted(text) - len(head))
        head = head[:max(0, room - 1)].rstrip() + "…"
        text = assemble(lines, dropped)
        warnings.append("X caption: headline truncated to fit 280 characters")

    if dropped:
        warnings.append(
            f"X caption: {dropped} line(s) dropped to fit 280 characters")
    return text, warnings


def _instagram(p: Payload, tags) -> str:
    """No clickable links, and only the first ~125 characters show unexpanded.

    So the headline has to carry the post on its own, and the caption ends
    with the bio pointer instead of a URL.
    """
    parts = [headline_text(p), ""]
    parts += p.lines
    if p.note:
        parts += ["", p.note]
    parts += ["", config.INSTAGRAM_BIO_POINTER]
    if tags:
        parts += ["", " ".join(tags)]
    text = "\n".join(parts)
    return text[:config.INSTAGRAM_LIMIT]


# ── Voice ────────────────────────────────────────────────────────────────────

def _check_voice(p: Payload) -> None:
    """Mechanically checkable house rules, applied to generated copy.

    Hand-written flex content is checked too — a typo'd exclamation mark in a
    scoreline is exactly the kind of thing that slips through at 7am.
    """
    for text in [p.headline] + list(p.lines):
        if "!" in text:
            raise VoiceError(
                f"exclamation point in {text!r} — house rule: no exclamation "
                f"points in scorelines")
    if p.emoji and p.emoji not in config.EMOJI.values():
        raise VoiceError(
            f"emoji {p.emoji!r} is not in the allowlist "
            f"({', '.join(config.EMOJI.values())})")


def x_length(text: str, url: str = "") -> int:
    """The length X will actually count, with any URL weighted at 23."""
    if url and url in text:
        return len(text) - len(url) + config.X_URL_WEIGHT
    return len(text)
