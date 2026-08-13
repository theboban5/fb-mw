"""Jinja2 -> HTML -> Playwright -> PNG.

Rendering never touches the network: fonts are vendored woff2 loaded over
`file://`, crests are local PNGs. The HTML is written into `social/` (not a
system temp dir) so those relative asset paths resolve, and removed after.

Two things here are load-bearing and easy to lose in a refactor:

  * The design tokens come from `config.TOKENS`. `_base.html` has no colour
    literals — it writes whatever this passes into `:root`.
  * The font is *asserted*, not hoped for. A headless Chromium that silently
    falls back to its default sans produces a plausible-looking graphic in the
    wrong typeface, and nothing downstream would ever catch it.
"""

import os
import tempfile

from jinja2 import Environment, FileSystemLoader, StrictUndefined

from . import config


class RenderError(Exception):
    """Rendering could not produce a trustworthy image."""


def environment() -> Environment:
    env = Environment(
        loader=FileSystemLoader(config.TEMPLATES),
        autoescape=True,
        undefined=StrictUndefined,   # a typo'd variable fails, never blanks
        trim_blocks=True,
        lstrip_blocks=True,
    )
    env.globals.update(
        tokens=config.TOKENS,
        font_stack=config.FONT_STACK,
        font_files=config.FONT_FILES,
        watermark=config.WATERMARK,
        size=config.IMAGE_SIZE,
    )
    env.filters["file_url"] = file_url
    return env


def file_url(path: "str | None") -> str:
    """Absolute filesystem path -> file:// URL usable from the rendered page."""
    if not path:
        return ""
    return "file://" + os.path.abspath(path)


def render_html(template: str, context: dict) -> str:
    return environment().get_template(template).render(**context)


def render_png(template: str, context: dict, out_path: str) -> str:
    """Render one template to a 1080x1080 PNG. Returns the path written."""
    html = render_html(template, context)
    return html_to_png(html, out_path)


def html_to_png(html: str, out_path: str) -> str:
    from playwright.sync_api import sync_playwright

    os.makedirs(os.path.dirname(out_path) or ".", exist_ok=True)
    # Inside social/ so `assets/fonts/...` resolves relative to the page.
    fd, tmp = tempfile.mkstemp(suffix=".html", prefix=".render-", dir=config.SOCIAL)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(html)
        with sync_playwright() as p:
            browser = p.chromium.launch()
            try:
                page = browser.new_page(
                    viewport={"width": config.IMAGE_SIZE, "height": config.IMAGE_SIZE},
                    device_scale_factor=config.DEVICE_SCALE,
                )
                page.goto(file_url(tmp), wait_until="load")
                _assert_fonts(page)
                board = page.locator("#board")
                if board.count() != 1:
                    raise RenderError(
                        "template must contain exactly one #board element "
                        f"(found {board.count()})")
                # Screenshot the element, not the page: a page screenshot can
                # pick up a scrollbar gutter and land at 1065px wide.
                board.screenshot(path=out_path)
            finally:
                browser.close()
    finally:
        if os.path.exists(tmp):
            os.remove(tmp)
    return _downscale(out_path)


def _assert_fonts(page) -> None:
    """Force the webfaces to load, then prove the real face is available.

    `document.fonts.ready` alone is not enough: a face nothing has painted yet
    stays 'unloaded' and the promise resolves anyway. Requesting each weight
    explicitly makes the check deterministic — and makes a missing or corrupt
    woff2 fail loudly here instead of shipping a graphic in Helvetica.
    """
    ok = page.evaluate(
        """async (family) => {
            await Promise.all([
                document.fonts.load(`400 100px ${family}`),
                document.fonts.load(`600 100px ${family}`),
                document.fonts.load(`900 100px ${family}`),
            ]);
            await document.fonts.ready;
            return document.fonts.check(`900 100px ${family}`)
                && document.fonts.check(`400 100px ${family}`);
        }""",
        config.FONT_FAMILY,
    )
    if not ok:
        raise RenderError(
            f"{config.FONT_FAMILY} did not load — the graphic would render in "
            f"a fallback face. Check {config.FONTS} holds the woff2 files "
            f"(see social/README.md).")
    _assert_painted_font(page)


def _assert_painted_font(page) -> None:
    """Assert the glyphs on the board were actually painted in Inter.

    Loading the face and *using* it are different failures. An escaped quote
    in the font stack, a typo'd family name, or a rule overridden downstream
    all leave the face loaded (so the check above passes) while Chromium
    paints the whole board in its default serif. Only the renderer knows
    which fonts really hit the glyphs, so ask it.
    """
    cdp = page.context.new_cdp_session(page)
    try:
        cdp.send("DOM.enable")
        cdp.send("CSS.enable")
        root = cdp.send("DOM.getDocument")["root"]["nodeId"]
        node = cdp.send("DOM.querySelector",
                        {"nodeId": root, "selector": "#board"})["nodeId"]
        fonts = cdp.send("CSS.getPlatformFontsForNode",
                         {"nodeId": node})["fonts"]
    finally:
        cdp.detach()
    if not fonts:
        return   # nothing painted yet is a different problem; layout catches it
    used = {f["familyName"]: f["glyphCount"] for f in fonts}
    wrong = {name: n for name, n in used.items()
             if config.FONT_FAMILY not in name}
    if wrong:
        raise RenderError(
            f"fallback font in use: {wrong} — the board must paint entirely in "
            f"{config.FONT_FAMILY}. Fonts seen: {used}")


def _downscale(path: str) -> str:
    """2160px screenshot -> a crisp 1080px PNG.

    Rendering at device_scale_factor=2 and resampling down is what keeps
    hairlines and small type sharp; screenshotting at 1x gives softer edges.
    """
    if config.DEVICE_SCALE == 1:
        return path
    try:
        from PIL import Image
    except ImportError:
        # Pillow is optional for the site build, so it is optional here too:
        # a 2160px PNG is still correct, just heavier.
        return path
    with Image.open(path) as img:
        if img.width == config.IMAGE_SIZE:
            return path
        img.convert("RGB").resize(
            (config.IMAGE_SIZE, config.IMAGE_SIZE), Image.LANCZOS
        ).save(path, "PNG", optimize=True)
    return path
