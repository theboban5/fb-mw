"""Bring your friends to the Derby (0072): the page and the seed they share.

What is being protected:

  * **The page and the database agree about the window.** The page says when
    referrals count; referral_rows decides it from 0072's seed. If they
    drift, a fan reads "closes Saturday 12:00" while the database has already
    stopped counting, and the prize goes to the wrong person.
  * **The page always says something true** before, during and after the
    race, and the form is there in all three: signing up is worth something
    whether or not a race is on.
  * **Unlisted means unlisted.** It carries noindex, and no other page links
    to it, until it is promoted on purpose.
  * **No person is in the HTML.** The page is static and public; a code or a
    phone number in it would be in every copy.
  * **The rules say what the admin screen does**: the minimum for the draw,
    the number of tickets, the verification call.
"""

import os
import re
import sys
import unittest
from datetime import datetime, timedelta

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from src import derby, flags, quiz  # noqa: E402

MIGRATION = os.path.join(ROOT, "supabase", "migrations", "0072_derby_referrals.sql")
STATIC = os.path.join(ROOT, "static")


def _page(now, **kw):
    return derby.render_page(css_ver="x", fl=flags.Flags(STATIC),
                             updated="today", now=now, **kw)


def _shown_phase(html):
    return re.findall(r'<p class="qz-when" data-dy-phase="(\w+)">', html)


class SeedAgreesWithPage(unittest.TestCase):

    def setUp(self):
        with open(MIGRATION, encoding="utf-8") as fh:
            self.sql = fh.read()

    def test_the_campaign_is_seeded_with_the_same_window(self):
        seed = self.sql.split("insert into public.referral_campaigns", 1)[1]
        seed = seed.split(";", 1)[0]
        cid, opens, closes = re.search(
            r"\('([\w-]+)',\s*'[^']*',\s*'([^']+)',\s*'([^']+)'\)", seed).groups()
        self.assertEqual(cid, derby.CAMPAIGN_ID)
        self.assertEqual(datetime.fromisoformat(opens + ":00"), derby.OPENS)
        self.assertEqual(datetime.fromisoformat(closes + ":00"), derby.CLOSES)

    def test_the_signup_defaults_to_this_campaign(self):
        self.assertIn(f"p_campaign  text    default '{derby.CAMPAIGN_ID}'",
                      self.sql)

    def test_the_draw_minimum_is_the_one_the_rules_state(self):
        self.assertIn(f"p_min      integer  default {derby.MIN_REFERRALS}",
                      self.sql)

    def test_the_follow_values_are_the_ones_the_database_accepts(self):
        allowed = re.search(r"follows <@ array\[([^\]]*)\]", self.sql).group(1)
        self.assertEqual(sorted(re.findall(r"'(\w+)'", allowed)),
                         sorted(v for v, _label, _url in derby.FOLLOWS))


class Timing(unittest.TestCase):

    def test_the_race_closes_before_the_derby_with_a_day_to_hand_over(self):
        self.assertLess(derby.OPENS, derby.CLOSES)
        self.assertLessEqual(derby.CLOSES, derby.KICKOFF - timedelta(hours=24))

    def test_it_opens_after_know_your_flames_closed(self):
        self.assertGreaterEqual(derby.OPENS, quiz.QUIZZES[-1].closes)


class ThePage(unittest.TestCase):

    BEFORE = datetime(2026, 11, 18, 12, 0, tzinfo=derby.CAT)
    DURING = datetime(2026, 11, 20, 12, 0, tzinfo=derby.CAT)
    AFTER = datetime(2026, 11, 21, 12, 0, tzinfo=derby.CAT)  # the close itself

    def test_each_phase_shows_its_own_line_and_only_that(self):
        for now, phase in ((self.BEFORE, "before"), (self.DURING, "open"),
                           (self.AFTER, "closed")):
            with self.subTest(phase):
                html = _page(now)
                self.assertEqual(_shown_phase(html), [phase])
                # Every line is in the page, for derby.js to switch to.
                self.assertEqual(html.count("data-dy-phase="), 3)

    def test_the_form_is_there_in_every_phase(self):
        for now in (self.BEFORE, self.DURING, self.AFTER):
            html = _page(now)
            self.assertIn('<form class="qz-form dy-form"', html)
            self.assertNotIn('<form class="qz-form dy-form" hidden', html)

    def test_every_follow_has_a_link_and_a_tick(self):
        html = _page(self.DURING)
        for value, _label, url in derby.FOLLOWS:
            with self.subTest(value):
                self.assertIn(f'data-dy-follow="{value}"', html)
                self.assertIn(f'name="follow_{value}" value="{value}"', html)
                self.assertIn(url, html)

    def test_unlisted_while_testing(self):
        html = _page(self.DURING)
        self.assertTrue(derby.NOINDEX)
        self.assertIn('<meta name="robots" content="noindex">', html)

    def test_the_link_panel_ships_hidden(self):
        html = _page(self.DURING)
        self.assertIn('<section class="qz-done dy-done" hidden>', html)
        self.assertIn('<p class="dy-invited" hidden>', html)

    def test_endpoints_come_from_the_rpc_base(self):
        html = _page(self.DURING, rpc_base="https://x.supabase.co/rest/v1/rpc",
                     key="sb_publishable_abc")
        self.assertIn('"signup": "https://x.supabase.co/rest/v1/rpc/submit_derby_signup"', html)
        self.assertIn('"count": "https://x.supabase.co/rest/v1/rpc/referral_count"', html)
        self.assertIn(f'"campaign": "{derby.CAMPAIGN_ID}"', html)

    def test_no_endpoint_is_an_empty_one_not_a_broken_one(self):
        html = _page(self.DURING)
        self.assertIn('"signup": ""', html)
        self.assertIn('"count": ""', html)

    def test_no_code_or_phone_number_in_the_html(self):
        html = _page(self.DURING)
        self.assertNotRegex(html, r"\?r=[A-Z2-9]{6}")
        self.assertNotRegex(html, r"\+265\d{9}")

    def test_the_rules_state_the_prizes_and_the_check(self):
        html = _page(self.DURING)
        rules = html.split('id="rules"', 1)[1]
        self.assertIn(derby.CASH, rules)
        self.assertIn(f"{derby.TICKETS} VIP tickets", rules)
        self.assertIn(f"at least {derby.MIN_REFERRALS} referrals", rules)
        self.assertIn("phone some of the people each winner", rules)
        self.assertIn(quiz._when(derby.CLOSES), rules)


class NothingLinksHereYet(unittest.TestCase):
    """Promotion is a decision, not a side effect: until it is made, the
    banner and the homepage do not point at /derby/."""

    def test_the_quiz_banner_does_not_point_here(self):
        for day in (19, 20, 21):
            now = datetime(2026, 11, day, 9, 0, tzinfo=derby.CAT)
            self.assertNotIn("derby/", quiz.banner("", now))

    def test_no_source_file_links_to_the_page(self):
        src = os.path.join(ROOT, "src")
        for name in os.listdir(src):
            if not name.endswith(".py") or name == "derby.py":
                continue
            with open(os.path.join(src, name), encoding="utf-8") as fh:
                self.assertNotIn('derby/"', fh.read(), name)


if __name__ == "__main__":
    unittest.main()
