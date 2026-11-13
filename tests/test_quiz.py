"""Know Your Flames (0067): the quiz page, its banner, and the seed they share.

What is being protected:

  * **The page and the database agree.** The windows and question ids live in
    src/quiz.py (for the page) and in 0067's seed (for submit_quiz_entry,
    which refuses an entry whose answers do not name exactly the round's
    questions). If they drift, every entry is refused or a round opens on the
    page and not in the database, and nobody finds out until a fan says so.
  * **Every round can be won.** A choice question whose answer is not one of
    its options, or a value longer than the RPC accepts, makes the draw pool
    empty.
  * **The page always says something true**, whichever state the build saw:
    a round open, the gap between rounds, or all over.
  * **The banner goes away on its own** once the last round has closed, and
    never on the quiz page itself, which does not need pointing to.
"""

import json
import os
import re
import sys
import unittest
from datetime import datetime, timedelta

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from src import flags, nt_page, quiz, render  # noqa: E402

MIGRATION = os.path.join(ROOT, "supabase", "migrations", "0067_quiz_entries.sql")
STATIC = os.path.join(ROOT, "static")

R1, R2 = quiz.QUIZZES


def _page(now, **kw):
    return quiz.render_page(css_ver="x", fl=flags.Flags(STATIC),
                            updated="today", now=now, **kw)


def _shown(html, attr):
    """The values of `attr` on sections that are NOT hidden."""
    return re.findall(rf'<section class="qz-\w+" {attr}="([^"]+)">', html)


class SeedAgreesWithPage(unittest.TestCase):

    def setUp(self):
        with open(MIGRATION, encoding="utf-8") as fh:
            sql = fh.read()
        seed = sql.split("insert into public.quizzes", 1)[1].split(";", 1)[0]
        self.rows = re.findall(
            r"\('([\w-]+)',\s*'[^']*',\s*'([^']+)',\s*'([^']+)',\s*"
            r"array\[([^\]]*)\]\)", seed)
        # 0069 moved Round 2's opening; the database holds the seed with that
        # update on top, so that is what the page must agree with.
        with open(os.path.join(os.path.dirname(MIGRATION),
                               "0069_quiz_round2_midnight.sql"),
                  encoding="utf-8") as fh:
            moved = dict((qid, at) for at, qid in re.findall(
                r"set opens_at = '([^']+)'\s*where quiz_id = '([\w-]+)'",
                fh.read()))
        self.rows = [(qid, moved.get(qid, opens), closes, ids)
                     for qid, opens, closes, ids in self.rows]

    def test_every_round_is_seeded_with_the_same_window_and_questions(self):
        self.assertEqual([r[0] for r in self.rows], [q.id for q in quiz.QUIZZES])
        for (qid, opens, closes, ids), q in zip(self.rows, quiz.QUIZZES):
            with self.subTest(qid):
                self.assertEqual(datetime.fromisoformat(opens + ":00"), q.opens)
                self.assertEqual(datetime.fromisoformat(closes + ":00"), q.closes)
                self.assertEqual(re.findall(r"'([\w-]+)'", ids),
                                 [x.id for x in q.questions])


class AnswerKeyAgreesWithPage(unittest.TestCase):
    """0068 put the answer key in the database, so it can mark entries in
    #/ops?tab=quiz. A key that drifts from src/quiz.py marks every right entry
    wrong, and the draw would quietly come out of an empty pool."""

    def setUp(self):
        with open(MIGRATION.replace("0067_quiz_entries", "0068_quiz_admin"),
                  encoding="utf-8") as fh:
            self.sql = fh.read()

    def test_static_answers_match(self):
        keys = dict(re.findall(
            r"set answer_key =\s*'(\{[^']*\})'.*?where quiz_id = '([\w-]+)'",
            self.sql, re.S))
        found = {qid: json.loads(raw) for raw, qid in keys.items()}
        for q in quiz.QUIZZES:
            with self.subTest(q.id):
                self.assertEqual(found[q.id], {x.id: x.answer for x in q.questions
                                               if x.kind != "score"})

    def test_the_score_question_reads_the_same_match(self):
        for q in quiz.QUIZZES:
            for x in q.questions:
                if x.kind == "score":
                    self.assertIn(f"score_question = '{x.id}', "
                                  f"score_match_id = '{x.match_id}'", self.sql)


class EveryRoundCanBeWon(unittest.TestCase):

    def test_rounds_are_in_order_and_do_not_overlap(self):
        for a, b in zip(quiz.QUIZZES, quiz.QUIZZES[1:]):
            self.assertLessEqual(a.closes, b.opens)
        for q in quiz.QUIZZES:
            self.assertLess(q.opens, q.closes)

    def test_each_choice_answer_is_one_of_its_options(self):
        for q in quiz.QUIZZES:
            for question in q.questions:
                with self.subTest(question.id):
                    if question.kind == "score":
                        self.assertTrue(question.match_id)
                        continue
                    values = [v for v, _label in question.options]
                    self.assertIn(question.answer, values)
                    # submit_quiz_entry refuses an answer over 20 characters.
                    self.assertTrue(all(1 <= len(v) <= 20 for v in values))

    def test_round_two_opens_after_cairo_and_closes_a_day_before_lilongwe(self):
        cairo_kickoff = datetime(2026, 11, 13, 21, 0, tzinfo=quiz.CAT)
        lilongwe_kickoff = datetime(2026, 11, 17, 15, 0, tzinfo=quiz.CAT)
        self.assertLess(R1.closes, cairo_kickoff)
        self.assertGreater(R2.opens, cairo_kickoff + timedelta(minutes=105))
        self.assertEqual(R2.closes, lilongwe_kickoff - timedelta(hours=24))

    def test_hints_point_at_the_flames_pages(self):
        slug = nt_page.page_for("MW_M").slug
        for q in quiz.QUIZZES:
            for question in q.questions:
                self.assertTrue(question.hint_href.startswith(f"{slug}/"))


class PageStates(unittest.TestCase):

    def test_round_one_open(self):
        html = _page(R1.opens + timedelta(hours=1))
        self.assertEqual(_shown(html, "data-qz-round"), [R1.id])
        self.assertEqual(_shown(html, "data-qz-state"), [])
        self.assertIn("Win a Malawi Flames jersey", html)

    def test_between_rounds(self):
        html = _page(R1.closes + timedelta(minutes=5))
        self.assertEqual(_shown(html, "data-qz-round"), [])
        self.assertEqual(_shown(html, "data-qz-state"), ["between"])
        self.assertIn("Round 1 has closed", html)

    def test_round_two_open(self):
        html = _page(R2.opens + timedelta(hours=1))
        self.assertEqual(_shown(html, "data-qz-round"), [R2.id])
        self.assertIn('name="flames-egypt-2:cairo:home"', html)

    def test_all_over(self):
        html = _page(R2.closes + timedelta(days=1))
        self.assertEqual(_shown(html, "data-qz-round"), [])
        self.assertEqual(_shown(html, "data-qz-state"), ["over"])

    def test_every_round_is_in_the_page_so_the_reader_clock_can_pick(self):
        html = _page(R1.opens + timedelta(hours=1))
        for q in quiz.QUIZZES:
            self.assertIn(f'data-qz-round="{q.id}"', html)

    def test_config_carries_windows_and_endpoint_never_answers(self):
        html = _page(R1.opens, rpc_url="https://x.supabase.co/rest/v1/rpc/"
                     "submit_quiz_entry", key="sb_publishable_abc")
        raw = re.search(r'id="qz-config">(.*?)</script>', html).group(1)
        cfg = json.loads(raw)
        self.assertEqual([q["id"] for q in cfg["quizzes"]],
                         [q.id for q in quiz.QUIZZES])
        self.assertEqual(cfg["quizzes"][0]["closes"],
                         int(R1.closes.timestamp() * 1000))
        self.assertEqual(cfg["key"], "sb_publishable_abc")
        self.assertNotIn("answer", raw)

    def test_offline_build_still_renders(self):
        cfg = json.loads(re.search(r'id="qz-config">(.*?)</script>',
                                   _page(R1.opens)).group(1))
        self.assertEqual(cfg["rpc"], "")

    def test_the_header_leagues_pill_goes_to_the_homepage(self):
        html = _page(R1.opens)
        self.assertIn('href="../#leagues"', html)
        self.assertNotIn('href="#leagues"', html)

    def test_rules_name_both_windows(self):
        html = _page(R1.opens)
        self.assertIn("Friday 13 November, 18:00 CAT", html)
        self.assertIn("Monday 16 November, 15:00 CAT", html)


class Banner(unittest.TestCase):

    def tearDown(self):
        render.BANNER = None

    def test_open_round_links_to_the_quiz_from_any_depth(self):
        html = quiz.banner("../", R1.opens + timedelta(hours=1))
        self.assertIn('href="../quiz/"', html)
        self.assertIn(R1.banner, html)

    def test_between_rounds_says_round_two_is_coming(self):
        html = quiz.banner("", R1.closes + timedelta(minutes=1))
        self.assertIn("Round 2 opens at midnight tonight", html)

    def test_gone_once_the_last_round_closes(self):
        self.assertEqual(quiz.banner("", R2.closes), "")

    def test_segments_cover_the_whole_campaign_without_gaps(self):
        segs = quiz._banner_segments()
        for a, b in zip(segs, segs[1:]):
            self.assertEqual(a["until"], b["from"])
        self.assertEqual(segs[-1]["until"], int(R2.closes.timestamp() * 1000))

    def test_search_bar_carries_it_and_the_hero_search_does_not(self):
        render.BANNER = lambda p: quiz.banner(p, R1.opens)
        self.assertIn("qz-banner", render.search_widget("../"))
        self.assertNotIn("qz-banner", render.search_widget("../", variant="hero"))
        render.BANNER = None
        self.assertNotIn("qz-banner", render.search_widget("../"))

    def test_the_quiz_page_has_no_banner(self):
        render.BANNER = lambda p: quiz.banner(p, R1.opens)
        self.assertNotIn('class="qz-banner"', _page(R1.opens))



class DrawMarking(unittest.TestCase):
    """scripts/quiz.py: an entry is in the pool only with EVERY answer right."""

    def setUp(self):
        sys.path.insert(0, os.path.join(ROOT, "scripts"))
        import quiz as script  # scripts/quiz.py, not src.quiz
        self.script = script

    def test_every_answer_must_match(self):
        key = {"a": "2008", "b": "x"}
        self.assertTrue(self.script.is_right({"answers": {"a": "2008", "b": "x"}}, key))
        self.assertFalse(self.script.is_right({"answers": {"a": "2008", "b": "y"}}, key))
        self.assertFalse(self.script.is_right({"answers": {"a": "2008"}}, key))
        self.assertFalse(self.script.is_right({"answers": None}, key))

    def test_round_one_key_needs_no_database(self):
        self.assertEqual(self.script.answer_key(R1),
                         {"last-win": "2008", "ss-scorers": "adepoju-njaliwa",
                          "japan": "jaziya"})

    def test_export_refuses_the_repository(self):
        args = type("A", (), {"out": os.path.join(ROOT, "entries.csv"),
                              "quiz": None})
        with self.assertRaises(SystemExit):
            self.script.cmd_export(args)
        self.assertFalse(os.path.exists(os.path.join(ROOT, "entries.csv")))


class Winners(unittest.TestCase):

    def test_named_winners_show_whichever_round_is_open(self):
        for now in (R1.closes + timedelta(minutes=1), R2.opens + timedelta(hours=1)):
            html = _page(now)
            self.assertIn('class="qz-winners"', html)
            for name in R1.winner_names:
                self.assertIn(f"<li>{name}</li>", html)

    def test_the_rules_say_names_may_be_published_here(self):
        # The page prints winners' full names; the rules must say it may.
        self.assertIn("names on EveryLeague", quiz._rules())

    def test_a_round_without_winners_renders_no_card(self):
        self.assertEqual(_page(R2.opens).count('class="qz-winners"'),
                         sum(1 for q in quiz.QUIZZES if q.winner_names))


if __name__ == "__main__":
    unittest.main()
