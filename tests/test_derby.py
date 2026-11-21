"""Bring your friends to the Derby (0072): the page and the seed they share.

What is being protected:

  * **The page and the database agree about the window.** The page says when
    referrals count; referral_rows decides it from 0072's seed. If they
    drift, a fan reads "closes Saturday 12:00" while the database has already
    stopped counting, and the prize goes to the wrong person.
  * **The page always says something true** before, during and after the
    race, and the form is there in all three: signing up is worth something
    whether or not a race is on.
  * **Promoted on purpose, from 18 Nov.** The site-wide banner points here
    from derby.ANNOUNCED until the winners have had their days, and the
    page is indexed. (It shipped unlisted, and a test held it so, while it
    was tried.)
  * **No person is in the HTML.** The page is static and public; a code or a
    phone number in it would be in every copy.
  * **The rules say what the admin screen does**: the minimum for the draw,
    the number of tickets, the verification call.
"""

import os
import re
import sys
import unittest
from unittest import mock
from datetime import datetime, timedelta

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from src import derby, events, flags, quiz  # noqa: E402

MIGRATION = os.path.join(ROOT, "supabase", "migrations", "0072_derby_referrals.sql")
OPENING = os.path.join(ROOT, "supabase", "migrations", "0074_derby_opens_now.sql")
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
        with open(OPENING, encoding="utf-8") as fh:
            self.opening = fh.read()

    def test_the_campaign_is_seeded_with_the_same_window(self):
        seed = self.sql.split("insert into public.referral_campaigns", 1)[1]
        seed = seed.split(";", 1)[0]
        cid, opens, closes = re.search(
            r"\('([\w-]+)',\s*'[^']*',\s*'([^']+)',\s*'([^']+)'\)", seed).groups()
        self.assertEqual(cid, derby.CAMPAIGN_ID)
        self.assertEqual(datetime.fromisoformat(closes + ":00"), derby.CLOSES)
        # 0074 moved the opening; the LAST statement setting it is the one
        # running, so the page must agree with that one.
        moved = re.findall(r"set opens_at = '([^']+)'", self.opening)
        self.assertEqual(datetime.fromisoformat((moved or [opens])[-1] + ":00"),
                         derby.OPENS)

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

    def test_every_follow_is_one_button_backed_by_a_hidden_box(self):
        html = _page(self.DURING)
        for value, _label, url in derby.FOLLOWS:
            with self.subTest(value):
                self.assertIn(f'data-dy-follow="{value}"', html)
                self.assertIn(f'name="follow_{value}" value="{value}" hidden', html)
                self.assertIn(url, html)

    def test_the_flow_is_three_steps_with_the_first_lit(self):
        html = _page(self.DURING)
        steps = re.findall(r'<li class="dy-flow-step( is-now)?" data-dy-step="(\d)"', html)
        self.assertEqual([n for _now, n in steps], ["1", "2", "3"])
        self.assertEqual([n for now, n in steps if now], ["1"])

    def test_the_share_step_leads_with_whatsapp(self):
        done = _page(self.DURING).split('class="qz-done dy-done"', 1)[1]
        self.assertLess(done.index("Now share it"), done.index('data-dy-share="whatsapp"'))
        self.assertLess(done.index('data-dy-share="whatsapp"'),
                        done.index('data-dy-share="facebook"'))

    def test_no_prediction_on_the_form(self):
        # Dropped for this race; it may come back as its own competition.
        self.assertNotIn("pred_home", _page(self.DURING))

    def test_indexed_now_it_is_promoted(self):
        self.assertFalse(derby.NOINDEX)
        self.assertNotIn('name="robots"', _page(self.DURING))

    def test_no_winners_block_until_they_are_named(self):
        with mock.patch.object(derby, "WINNER_NAMES", ()):
            html = _page(self.AFTER)
        self.assertNotIn("qz-winners", html)
        self.assertIn("will announce", html)

    def test_named_winners_replace_the_promise_to_announce(self):
        html = _page(self.AFTER)
        self.assertIn("qz-winners", html)
        for name in derby.WINNER_NAMES:
            self.assertIn(name, html)
        self.assertNotIn("will announce", html)

    def test_the_link_panel_ships_hidden(self):
        html = _page(self.DURING)
        self.assertIn('<section class="qz-done dy-done" hidden tabindex="-1">', html)
        self.assertIn('<p class="dy-invited" hidden>', html)

    def test_endpoints_come_from_the_rpc_base(self):
        html = _page(self.DURING, rpc_base="https://x.supabase.co/rest/v1/rpc",
                     key="sb_publishable_abc")
        self.assertIn('"signup": "https://x.supabase.co/rest/v1/rpc/submit_derby_signup"', html)
        self.assertIn('"count": "https://x.supabase.co/rest/v1/rpc/referral_count"', html)
        self.assertIn('"inviter": "https://x.supabase.co/rest/v1/rpc/referral_inviter"', html)
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
        self.assertIn(f"at least {derby.MIN_REFERRALS} points", rules)
        self.assertIn("phone some of the people each winner", rules)
        self.assertIn('<details class="qz-rules" id="rules">', html)
        self.assertIn(quiz._when(derby.CLOSES), rules)


class TheBannerPointsHere(unittest.TestCase):
    """events.banner puts the Derby in front of the quiz's winners from the
    day it is announced; quiz.banner alone still knows nothing of it."""

    def test_from_the_announcement_to_after_the_close(self):
        for when in (derby.ANNOUNCED + timedelta(hours=1),
                     derby.OPENS + timedelta(hours=1),
                     derby.CLOSES + timedelta(hours=1)):
            with self.subTest(when):
                html = events.banner("../", when)
                self.assertIn('href="../derby/"', html)

    def test_it_says_when_points_start_before_they_do(self):
        html = events.banner("", derby.ANNOUNCED + timedelta(hours=1))
        self.assertIn(f"starts {derby.OPENS:%A}", html)
        self.assertIn(derby.CASH, html)

    def test_it_goes_once_the_winners_have_had_their_days(self):
        self.assertEqual(events.banner("", derby.WINNERS_BANNER_UNTIL), "")

    def test_the_segments_run_end_to_end(self):
        segs = derby.banner_segments()
        for a, b in zip(segs, segs[1:]):
            self.assertEqual(a["until"], b["from"])
        self.assertTrue(all(s["href"] == "derby/" for s in segs))

    def test_before_the_announcement_the_quiz_still_has_the_strip(self):
        html = events.banner("", derby.ANNOUNCED - timedelta(hours=1))
        self.assertIn('href="quiz/"', html)


if __name__ == "__main__":
    unittest.main()
