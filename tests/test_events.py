"""Events and winners: /events/ (src/events.py).

What is being protected:

  * **A winner's name is typed once.** The page reads quiz.QUIZZES and the
    derby module, so it can never disagree with the campaign's own page.
  * **Every card says something true on any day**: Coming up, On now or
    Finished for the build's clock, all three in the page for the script.
  * **A photo that is named exists, and is small.** static/ is copied
    verbatim, so a 4 MB phone photo would ship as one; a typo would ship as
    a broken image.
  * **It is reachable**: the footer on every page, and the Derby page.
"""

import os
import re
import sys
import unittest
from datetime import datetime

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from src import derby, events, flags, quiz, render  # noqa: E402

STATIC = os.path.join(ROOT, "static")
CAT = events.CAT


def _page(now):
    return events.render_page(css_ver="x", fl=flags.Flags(STATIC),
                              updated="today", now=now)


def _shown(card):
    return re.findall(r'class="ev-chip is-(\w+)" data-ev-when="\w+">', card)


class TheList(unittest.TestCase):

    def test_newest_first_and_every_campaign_once(self):
        evs = events.events()
        self.assertEqual([e.starts for e in evs],
                         sorted((e.starts for e in evs), reverse=True))
        self.assertEqual(evs[0].id, derby.CAMPAIGN_ID)
        self.assertEqual({e.id for e in evs},
                         {derby.CAMPAIGN_ID} | {q.id for q in quiz.QUIZZES})

    def test_winners_come_from_the_campaigns(self):
        by_id = {e.id: e for e in events.events()}
        for q in quiz.QUIZZES:
            self.assertEqual(by_id[q.id].winners, q.winner_names)
        self.assertEqual(by_id[derby.CAMPAIGN_ID].winners, derby.WINNER_NAMES)

    def test_a_prize_reads_as_a_thing(self):
        self.assertEqual(events._prize("Win a Malawi Flames jersey"),
                         "A Malawi Flames jersey")


class ThePage(unittest.TestCase):

    def test_each_card_shows_one_state_for_the_build_clock(self):
        html = _page(datetime(2026, 11, 20, 12, 0, tzinfo=CAT))
        cards = html.split('<li class="ev-card"')[1:]
        self.assertEqual(len(cards), len(events.events()))
        self.assertEqual(_shown(cards[0]), ["on"])
        self.assertTrue(all(_shown(c) == ["over"] for c in cards[1:]))
        for c in cards:
            self.assertEqual(c.count("data-ev-when="), 6)   # 3 chips, 3 links

    def test_the_named_winners_are_on_it(self):
        html = _page(datetime(2026, 11, 20, 12, 0, tzinfo=CAT))
        for q in quiz.QUIZZES:
            for name in q.winner_names:
                self.assertIn(name, html)

    def test_links_go_to_each_campaign(self):
        html = _page(datetime(2026, 11, 20, 12, 0, tzinfo=CAT))
        self.assertIn('href="../derby/"', html)
        self.assertIn('href="../quiz/"', html)

    def test_no_photos_render_nothing(self):
        html = _page(datetime(2026, 11, 20, 12, 0, tzinfo=CAT))
        self.assertEqual("ev-photos" in html, bool(events.PHOTOS))


class Photos(unittest.TestCase):

    def test_every_named_photo_exists_and_is_small(self):
        for event_id, photos in events.PHOTOS.items():
            self.assertIn(event_id, {e.id for e in events.events()})
            for ph in photos:
                with self.subTest(ph.src):
                    path = os.path.join(STATIC, ph.src)
                    self.assertTrue(os.path.isfile(path), path)
                    self.assertLessEqual(os.path.getsize(path),
                                         events.PHOTO_MAX_BYTES)
                    self.assertTrue(ph.alt)


class Reachable(unittest.TestCase):

    def test_the_footer_links_here(self):
        self.assertIn('href="../events/"', render.footer("today", "../"))

    def test_the_derby_page_links_here(self):
        html = derby.render_page(css_ver="x", fl=flags.Flags(STATIC),
                                 updated="today",
                                 now=datetime(2026, 11, 20, tzinfo=CAT))
        self.assertIn('href="../events/"', html)


if __name__ == "__main__":
    unittest.main()
