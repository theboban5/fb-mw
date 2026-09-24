"""Tests for the link-preview cards (src/og_card.py) and the <head> that names them.

What goes wrong here is invisible until someone shares a link: a card for a
match with no page, a card that keeps its URL after the score changes (so a
chat client shows Friday's "v 15:00" on Sunday), or a page naming a card that
was never written. These are also the first assertions in the repo about a
page's og: tags at all.
"""

from dataclasses import replace
import os
import re
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src import match_page, og_card, render  # noqa: E402
from tests.test_match_page import (STATIC, TEMPLATES, _leagues,  # noqa: E402
                                   _Snapshot)

try:
    from PIL import Image
except ImportError:  # the build treats Pillow as optional; so do the tests
    Image = None


def _og_image(html):
    return re.search(r'<meta property="og:image" content="([^"]*)">', html).group(1)


class FactsTest(_Snapshot):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.m = next(cls.ds.matches[i] for i in sorted(cls.page_ids)
                     if cls.ds.matches[i].counts_for_table)

    def test_result_fixture_and_off(self):
        f = og_card.facts(self.ds, self.m)
        self.assertEqual(f["kind"], "result")
        self.assertEqual(f["middle"], f"{self.m.home_goals}–{self.m.away_goals}")
        self.assertEqual(f["status"], "Full time")

        fixture = replace(self.m, status="scheduled", home_goals=None,
                          away_goals=None, kickoff="15:00:00")
        f = og_card.facts(self.ds, fixture)
        self.assertEqual((f["kind"], f["middle"]), ("fixture", "15:00"))
        self.assertEqual(og_card.facts(self.ds, replace(fixture, kickoff=""))
                         ["middle"], "vs")

        off = og_card.facts(self.ds, replace(fixture, status="postponed"))
        self.assertEqual((off["kind"], off["status"]), ("off", "Postponed"))
        self.assertIn("postponed", og_card.alt_text(off))

    def test_pens_and_extra_time(self):
        f = og_card.facts(self.ds, replace(self.m, home_pens=4, away_pens=3,
                                           extra_time=True))
        self.assertEqual(f["status"], "4–3 on pens · After extra time")

    def test_filename_moves_with_the_score_and_only_then(self):
        f = og_card.facts(self.ds, self.m)
        a = og_card.filename(self.m.match_id, f, None, None)
        self.assertEqual(a, og_card.filename(self.m.match_id,
                                             og_card.facts(self.ds, self.m),
                                             None, None))
        changed = og_card.facts(self.ds, replace(self.m, home_goals=9))
        self.assertNotEqual(a, og_card.filename(self.m.match_id, changed,
                                                None, None))
        self.assertTrue(a.startswith(self.m.match_id + "-"))


@unittest.skipIf(Image is None, "Pillow not installed")
class DrawTest(_Snapshot):
    def _check(self, f, crest=None):
        img = og_card.draw(f, crest, crest)
        self.assertEqual(img.size, (1200, 630))
        with tempfile.NamedTemporaryFile(suffix=".png") as fh:
            img.quantize(256, method=Image.Quantize.MEDIANCUT).save(
                fh.name, "PNG", optimize=True)
            # WhatsApp drops a preview image much over 300 kB; stay far under.
            self.assertLess(os.path.getsize(fh.name), 100_000)

    def test_every_kind_draws_with_and_without_crests(self):
        crest = os.path.join(STATIC, "logos", "clubs",
                             sorted(os.listdir(os.path.join(
                                 STATIC, "logos", "clubs")))[0])
        base = {"home": "Mighty Mukuru Wanderers Reserves FC United",
                "away": "Bullets", "competition": "Super League",
                "round": "Matchday 21", "date": "Sat 27 Sep"}
        for extra in ({"kind": "result", "middle": "10–10", "status": "Full time"},
                      {"kind": "fixture", "middle": "15:00", "status": ""},
                      {"kind": "fixture", "middle": "vs", "status": ""},
                      {"kind": "off", "middle": "vs", "status": "Postponed"}):
            self._check({**base, **extra})
            self._check({**base, **extra}, crest)


@unittest.skipIf(Image is None, "Pillow not installed")
class BuiltHeadTest(_Snapshot):
    """The cards build.py would write, and the <head> of the pages that name them."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.tmp = tempfile.TemporaryDirectory()
        # A stale card from an earlier build, as the CI cache would restore.
        cls.stale = os.path.join(cls.tmp.name, og_card.SLUG, "MW_OLD-00000000.png")
        os.makedirs(os.path.dirname(cls.stale))
        open(cls.stale, "wb").close()
        # A sample, not all ~1,000: drawing every card is ~35 s, and the suite
        # is meant to run in a couple. The pages are still all built, so the
        # ones outside the sample are the pages-without-a-card case.
        cls.sample = set(sorted(cls.page_ids)[:24])
        cls.cards = og_card.build_cards(cls.tmp.name, STATIC, cls.ds, cls.sample)
        leagues, tables = _leagues(cls.ds)
        match_page.build_pages(cls.tmp.name, TEMPLATES, STATIC, cls.ds, leagues,
                               tables, "1 January 2026, 00:00 CAT",
                               page_ids=cls.page_ids, cards=cls.cards)

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def _head(self, mid):
        path = os.path.join(self.tmp.name, match_page.SLUG, f"{mid}.html")
        with open(path, encoding="utf-8") as fh:
            return fh.read()

    def test_every_page_gets_a_card_and_names_it(self):
        self.assertEqual(set(self.cards), self.sample)
        prefix = f"{render.SITE_URL}/{og_card.SLUG}/"
        for mid, (url, alt) in self.cards.items():
            self.assertTrue(url.startswith(prefix))
            self.assertTrue(os.path.exists(os.path.join(
                self.tmp.name, og_card.SLUG, url[len(prefix):])))
            html = self._head(mid)
            self.assertEqual(_og_image(html), url)
            self.assertIn(f'<meta name="twitter:image" content="{url}">', html)
            self.assertTrue(alt)

    def test_a_page_with_no_card_keeps_the_site_card(self):
        mid = next(i for i in sorted(self.page_ids) if i not in self.cards)
        self.assertEqual(_og_image(self._head(mid)), render.OG_IMAGE)

    def test_stale_cards_are_pruned_and_current_ones_reused(self):
        self.assertFalse(os.path.exists(self.stale))
        out = os.path.join(self.tmp.name, og_card.SLUG)
        before = {f: os.path.getmtime(os.path.join(out, f)) for f in os.listdir(out)}
        again = og_card.build_cards(self.tmp.name, STATIC, self.ds, self.sample)
        self.assertEqual(again, self.cards)
        self.assertEqual(before, {f: os.path.getmtime(os.path.join(out, f))
                                  for f in os.listdir(out)})

    def test_cards_stay_out_of_the_match_directory(self):
        # test_match_page pins /match/ to exactly one .html per page.
        self.assertTrue(all(f.endswith(".html") for f in os.listdir(
            os.path.join(self.tmp.name, match_page.SLUG))))


if __name__ == "__main__":
    unittest.main()
