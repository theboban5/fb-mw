"""Rendering smoke tests.

Deliberately not pixel snapshots — those break on every font tweak and teach
you to regenerate them without looking. What is asserted is what actually goes
wrong in practice: a blank board, wrong dimensions, or a silent font fallback.
"""

import os
import shutil
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))   # the repo root, for `social`
sys.path.insert(0, HERE)                    # this directory, for the fixture

from social import config, render             # noqa: E402
from social.posts import base                 # noqa: E402
import social_fixture_data as fixture_data    # noqa: E402

# A 1080x1080 board of type on a dark ground is ~80KB. Well under this and
# the render is blank or nearly so, which is the failure worth catching.
MIN_BYTES = 20_000


def playwright_available() -> bool:
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        return False
    try:
        with sync_playwright() as p:
            p.chromium.launch().close()
        return True
    except Exception:
        return False


HAVE_PLAYWRIGHT = playwright_available()


@unittest.skipUnless(HAVE_PLAYWRIGHT,
                     "playwright + chromium needed (playwright install chromium)")
class RenderTest(unittest.TestCase):

    def setUp(self):
        self.folder = tempfile.mkdtemp(prefix="social-render-")
        self.addCleanup(shutil.rmtree, self.folder, ignore_errors=True)

    def _render(self, post_type, **options):
        ctx = fixture_data.ctx(**options)
        available, why = base.get(post_type).is_available(ctx)
        self.assertTrue(available, f"{post_type} unavailable: {why}")
        draft = base.get(post_type).build(ctx)[0]
        path = os.path.join(self.folder, f"{draft.key}.png")
        render.render_png(draft.template, draft.context, path)
        return path

    def _assert_board(self, path):
        self.assertTrue(os.path.exists(path))
        size = os.path.getsize(path)
        self.assertGreater(size, MIN_BYTES,
                           f"{os.path.basename(path)} is {size} bytes — "
                           f"probably a blank render")
        try:
            from PIL import Image
        except ImportError:
            self.skipTest("Pillow not installed")
        with Image.open(path) as img:
            self.assertEqual(img.size, (config.IMAGE_SIZE, config.IMAGE_SIZE))

    def test_results_board(self):
        self._assert_board(self._render("results", days="1"))

    def test_fixtures_board(self):
        self._assert_board(
            self._render("fixtures", date="2026-08-20", days="7"))

    def test_scorers_board(self):
        self._assert_board(
            self._render("scorers", competition=fixture_data.COMPETITION))

    def test_table_board(self):
        self._assert_board(
            self._render("table", competition=fixture_data.COMPETITION))

    def test_a_missing_font_fails_loudly(self):
        """The failure this whole assertion exists for: no silent fallback."""
        html = """<!doctype html><html><head><style>
          body { font-family: 'Inter', serif; }
          #board { width: 1080px; height: 1080px; background: #15171a;
                   color: #fff; font-size: 60px; }
        </style></head><body><div id="board">No font-face declared</div></body></html>"""
        with self.assertRaises(render.RenderError):
            render.html_to_png(html, os.path.join(self.folder, "nofont.png"))


if __name__ == "__main__":
    unittest.main()
