"""static/report/graphics.js's TOKENS must match social/config.py's exactly.

The reporter portal's canvas-drawn cards (#/graphics) and the social/ CLI's
Jinja2/Playwright cards are two different renderers for the same design
system, kept in sync by hand rather than by a shared build step — this is
the mechanical check that catches a colour changed in one file and not the
other. See CLAUDE.md's "Recent work" entry for the in-portal graphics
generator for why that duplication exists at all.
"""

import os
import re
import sys
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)

from social import config  # noqa: E402

GRAPHICS_JS = os.path.join(ROOT, "static", "report", "graphics.js")


def _extract_js_tokens(text: str) -> dict:
    """Pull the `export const TOKENS = {...};` object literal out of the JS
    source and parse it as the flat string:string map it is. Deliberately not
    a real JS parser — the literal is small and quoted, and a regex that
    fails loudly on a shape change is safer than a dependency to run one."""
    match = re.search(r"export const TOKENS = \{(.*?)\};", text, re.DOTALL)
    if not match:
        raise AssertionError("could not find `export const TOKENS = {...};` "
                              "in static/report/graphics.js")
    body = match.group(1)
    tokens = {}
    for line in body.splitlines():
        line = line.split("//", 1)[0].strip().rstrip(",")
        if not line:
            continue
        key, _, value = line.partition(":")
        tokens[key.strip()] = value.strip().strip('"\'')
    return tokens


class GraphicsTokensMatchSocialConfig(unittest.TestCase):
    def test_tokens_match_key_for_key(self):
        with open(GRAPHICS_JS, encoding="utf-8") as fh:
            js_tokens = _extract_js_tokens(fh.read())
        self.assertEqual(
            js_tokens, config.TOKENS,
            "static/report/graphics.js TOKENS has drifted from "
            "social/config.py TOKENS — update graphics.js to match "
            "(or config.py, if the palette changed on purpose).")


if __name__ == "__main__":
    unittest.main()
