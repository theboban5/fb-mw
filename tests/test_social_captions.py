"""Caption tests: golden files for the shapes that matter, plus the rules.

Regenerate the golden files after an intentional wording change with:

    UPDATE_GOLDEN=1 python -m unittest discover -s tests -k social

Read the diff before committing it — these files are the house voice.
"""

import os
import sys
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))   # the repo root, for `social`
sys.path.insert(0, HERE)                    # this directory, for the fixture

from social import captions, config          # noqa: E402
from social.posts import base                # noqa: E402
import social_fixture_data as fixture_data   # noqa: E402

GOLDEN = os.path.join(HERE, "social_golden")


def golden(name: str, actual: str) -> str:
    """Compare against tests/social/golden/<name>.txt, or write it."""
    path = os.path.join(GOLDEN, f"{name}.txt")
    if os.environ.get("UPDATE_GOLDEN"):
        os.makedirs(GOLDEN, exist_ok=True)
        with open(path, "w", encoding="utf-8") as fh:
            fh.write(actual)
        return actual
    with open(path, encoding="utf-8") as fh:
        return fh.read()


class GoldenCaptionTest(unittest.TestCase):
    """Every platform, on data covering the awkward cases."""

    maxDiff = None

    def _captions(self, post_type, **options):
        ctx = fixture_data.ctx(**options)
        available, why = base.get(post_type).is_available(ctx)
        self.assertTrue(available, f"{post_type} unavailable: {why}")
        drafts = base.get(post_type).build(ctx)
        texts, _warnings = captions.render(drafts[0].payload)
        return texts

    def test_results_captions(self):
        texts = self._captions("results", days="1")
        for platform, text in texts.items():
            self.assertEqual(golden(f"results.{platform}", text), text,
                             f"{platform} caption changed")

    def test_fixtures_captions(self):
        texts = self._captions("fixtures", date="2026-08-20", days="7")
        for platform, text in texts.items():
            self.assertEqual(golden(f"fixtures.{platform}", text), text,
                             f"{platform} caption changed")

    def test_scorers_captions(self):
        texts = self._captions("scorers", competition=fixture_data.COMPETITION)
        for platform, text in texts.items():
            self.assertEqual(golden(f"scorers.{platform}", text), text,
                             f"{platform} caption changed")


class ResultShapeTest(unittest.TestCase):
    """The specific cases, asserted directly rather than only via goldens."""

    def setUp(self):
        self.ctx = fixture_data.ctx()

    def test_goalless_draw_lists_no_scorers(self):
        m = self.ctx.match("T_M1")
        self.assertEqual(m.scoreline, "0-0")
        self.assertFalse(m.has_any_scorer)
        self.assertTrue(m.scorers_complete)

    def test_two_scorers_for_one_team_with_a_brace(self):
        m = self.ctx.match("T_M2")
        self.assertEqual([s.label for s in m.home.scorers],
                         ["Ndhlovu 12', 58'", "Banda 77' (P)"])
        self.assertEqual([s.label for s in m.away.scorers], ["Mpinganjira 90'"])
        self.assertTrue(m.scorers_complete)

    def test_own_goal_is_marked_and_credited_to_the_beneficiary(self):
        m = self.ctx.match("T_M3")
        self.assertEqual([s.label for s in m.home.scorers],
                         ["Mkandawire 34' (OG)"])
        self.assertEqual(m.away.scorers, ())

    def test_own_goal_never_enters_the_scorer_ranking(self):
        names = [r.name for r in
                 self.ctx.top_scorers(fixture_data.COMPETITION, top_n=8)]
        self.assertNotIn("Lucky Mkandawire", names)

    def test_unnamed_scorer_is_declared_not_absorbed(self):
        m = self.ctx.match("T_M4")
        self.assertEqual([s.label for s in m.home.scorers], ["Duwe 22'"])
        self.assertEqual(m.home.unrecorded, 1)
        self.assertFalse(m.scorers_complete)

    def test_partial_scorers_are_disclosed_in_the_caption(self):
        drafts = base.get("results").build(fixture_data.ctx(days="1"))
        text = captions.render(drafts[0].payload)[0]["whatsapp"]
        self.assertIn("+1 not recorded", text)

    def test_long_team_name_survives_intact(self):
        m = self.ctx.match("T_M3")
        self.assertEqual(m.home.team.name, fixture_data.LONG_NAME)
        text = captions.render(
            base.get("results").build(fixture_data.ctx(days="1"))[0].payload
        )[0]["facebook"]
        self.assertIn(fixture_data.LONG_NAME, text)


class ScorerTieTest(unittest.TestCase):

    def test_joint_positions_are_marked_and_ties_kept_whole(self):
        ctx = fixture_data.ctx()
        # Ndhlovu has 2; three others have 1. A cut at 2 lands inside that
        # group of three, and must widen to hold all of them rather than name
        # one of three players level on the same tally.
        rows = ctx.top_scorers(fixture_data.COMPETITION, top_n=2)
        self.assertEqual(len(rows), 4)
        self.assertEqual(rows[0].goals, 2)
        self.assertFalse(rows[0].joint)
        tied = [r for r in rows if r.goals == 1]
        self.assertGreater(len(tied), 1)
        self.assertTrue(all(r.joint for r in tied))
        self.assertEqual({r.position for r in tied}, {2})


class XLimitTest(unittest.TestCase):
    """280 including the URL, counted at 23 characters as t.co does."""

    def _x_captions(self):
        out = []
        for post_type, options in (("results", {"days": "1"}),
                                   ("fixtures", {"date": "2026-08-20",
                                                 "days": "7"}),
                                   ("scorers", {}),
                                   ("table", {})):
            ctx = fixture_data.ctx(competition=fixture_data.COMPETITION,
                                   **options)
            available, _why = base.get(post_type).is_available(ctx)
            if not available:
                continue
            for draft in base.get(post_type).build(ctx):
                texts, _warns = captions.render(draft.payload)
                out.append((draft.key, texts["x"], draft.payload))
        return out

    def test_every_x_caption_fits(self):
        for key, text, payload in self._x_captions():
            url = captions.link_for("x", payload)
            weighted = captions.x_length(text, url)
            self.assertLessEqual(
                weighted, config.X_LIMIT,
                f"{key}: X caption is {weighted} weighted characters")

    def test_the_link_is_never_dropped(self):
        for key, text, payload in self._x_captions():
            self.assertIn(captions.link_for("x", payload), text,
                          f"{key}: X caption lost its link")

    def test_url_counts_as_23(self):
        url = captions.config.tagged_link("x", "test", "/matches/")
        text = f"body {url}"
        self.assertGreater(len(url), config.X_URL_WEIGHT)
        self.assertEqual(captions.x_length(text, url),
                         len("body ") + config.X_URL_WEIGHT)


class VoiceTest(unittest.TestCase):

    def test_exclamation_point_is_refused(self):
        payload = captions.Payload(headline="Alpha FC 2-0 Bravo United!")
        with self.assertRaises(captions.VoiceError):
            captions.render(payload)

    def test_emoji_outside_the_allowlist_is_refused(self):
        payload = captions.Payload(headline="Alpha FC 2-0 Bravo United",
                                   emoji="\U0001f525")
        with self.assertRaises(captions.VoiceError):
            captions.render(payload)

    def test_instagram_gets_no_link_and_a_bio_pointer(self):
        ctx = fixture_data.ctx(days="1")
        draft = base.get("results").build(ctx)[0]
        texts, _warns = captions.render(draft.payload)
        self.assertNotIn("http", texts["instagram"])
        self.assertIn(config.INSTAGRAM_BIO_POINTER, texts["instagram"])

    def test_every_other_platform_ends_with_the_link(self):
        ctx = fixture_data.ctx(days="1")
        draft = base.get("results").build(ctx)[0]
        texts, _warns = captions.render(draft.payload)
        for platform in ("whatsapp", "facebook", "x"):
            self.assertTrue(
                texts[platform].rstrip().endswith(
                    captions.link_for(platform, draft.payload)),
                f"{platform} caption does not end with its link")

    def test_no_hashtags_flag(self):
        ctx = fixture_data.ctx(days="1")
        draft = base.get("results").build(ctx)[0]
        texts, _warns = captions.render(draft.payload, no_hashtags=True)
        for platform, text in texts.items():
            self.assertNotIn("#", text, f"{platform} kept a hashtag")

    def test_fixture_captions_carry_absolute_times_only(self):
        ctx = fixture_data.ctx(date="2026-08-20", days="7")
        draft = base.get("fixtures").build(ctx)[0]
        texts, _warns = captions.render(draft.payload)
        for platform, text in texts.items():
            lowered = text.lower()
            for banned in ("today", "tomorrow", "tonight", "this weekend"):
                self.assertNotIn(banned, lowered,
                                 f"{platform} used a relative time")
            self.assertIn(config.TZ_LABEL, text)


if __name__ == "__main__":
    unittest.main()
