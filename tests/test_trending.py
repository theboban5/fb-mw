"""The homepage carousel (0030) — the `trending` tab and what it renders.

Four things are being protected, and they are the four ways this feature could
quietly go wrong:

  * **An empty tab renders NOTHING and the landing page falls back.** Every
    build before 0030, every offline build and every site that has not
    published a card yet must look exactly as it did — a carousel that renders
    an empty box would be worse than the hand-written card it replaced.
  * **Only `live` reaches the page.** A draft is written and not published;
    the archive is taken down and kept. Both are parsed, both are in the
    snapshot, neither renders.
  * **A missing part renders nothing, not a placeholder.** No photo, no
    eyebrow, no link, no words — each drops out on its own and the card is
    still a card. The house rule, applied to editorial copy.
  * **The link is the one field that can be dangerous**, because it goes
    straight into an href on the most-visited page. And check 11 must refuse a
    bad one on a LIVE card only: erroring on a draft would stop every deploy
    for everyone over a card nobody can see, which is the exact outage the
    validator exists to prevent.
"""

import os
import re
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

import validate  # noqa: E402
from src import dataset, trending  # noqa: E402

HEADER = ("card_id,status,eyebrow,headline,body,link_url,link_label,"
          "image_path,image_alt,image_credit,sort_order,published_at\n")

# The tab as it stood before 0031 added image_credit. Every snapshot in
# data/canonical/ taken before that migration looks like this, and must still
# parse — an absent column reads as every cell blank.
HEADER_0030 = ("card_id,status,eyebrow,headline,body,link_url,link_label,"
               "image_path,image_alt,sort_order,published_at\n")


def parse(*rows, header=HEADER):
    return dataset.parse_trending(header + "".join(r + "\n" for r in rows))


def row(header=HEADER, **cells):
    """One CSV line, every unnamed column blank.

    Written from the header rather than as a literal string of commas: the
    literals silently stopped lining up with the columns the moment 0031 added
    one, and a row that is short by a field still parses — it just tests a
    different column than the test says it does."""
    columns = header.strip().split(",")
    unknown = set(cells) - set(columns)
    assert not unknown, f"no such column(s): {sorted(unknown)}"
    return ",".join(str(cells.get(c, "")) for c in columns)


def card(card_id="MW_TRD_000001", status="live", eyebrow="", headline="A win",
         body="", link_url="", link_label="", image_path="", image_alt="",
         image_credit="", sort_order=1, published_at=""):
    return dataset.TrendingCard(card_id, status, eyebrow, headline, body,
                                link_url, link_label, image_path, image_alt,
                                image_credit, sort_order, published_at)


class ParseTest(unittest.TestCase):
    def test_an_absent_tab_is_an_empty_tab(self):
        """The sheets fallback and a snapshot taken before 0030 both give a
        header-only CSV. That is no cards, not a broken build."""
        empty = dataset.empty_csv("trending")
        self.assertEqual(dataset.parse_trending(empty), {})

    def test_the_supabase_only_header_matches_the_emitter(self):
        from src import source_supabase
        self.assertEqual(tuple(dataset.SUPABASE_ONLY_TABS["trending"]),
                         source_supabase.COLUMNS["trending"])

    def test_everything_but_the_headline_is_optional(self):
        c = parse(row(card_id="MW_TRD_000001", status="draft",
                      headline="Just a headline"))["MW_TRD_000001"]
        self.assertEqual(c.headline, "Just a headline")
        self.assertEqual(
            (c.eyebrow, c.body, c.link_url, c.image_path, c.image_credit),
            ("",) * 5)

    def test_a_blank_sort_order_reads_as_zero(self):
        c = parse(row(card_id="MW_TRD_000001", status="live",
                      headline="A win", sort_order=""))["MW_TRD_000001"]
        self.assertEqual(c.sort_order, 0)

    def test_an_unknown_status_is_a_data_error(self):
        with self.assertRaises(dataset.DataError):
            parse(row(card_id="MW_TRD_000001", status="published",
                      headline="A win"))

    def test_a_blank_headline_is_a_data_error(self):
        with self.assertRaises(dataset.DataError):
            parse(row(card_id="MW_TRD_000001", status="live"))

    def test_a_snapshot_from_before_the_credit_still_parses(self):
        """An absent column reads as every cell blank — the same rule the
        officials and man-of-the-match columns live by, and what lets an older
        data/canonical/ keep building."""
        c = parse(row(HEADER_0030, card_id="MW_TRD_000001", status="live",
                      headline="A win", sort_order=2),
                  header=HEADER_0030)["MW_TRD_000001"]
        self.assertEqual(c.image_credit, "")
        self.assertEqual(c.sort_order, 2)


class OrderTest(unittest.TestCase):
    def test_only_live_cards_render(self):
        ds = dataset.Dataset(trending={c.card_id: c for c in (
            card("MW_TRD_000001", "live"),
            card("MW_TRD_000002", "draft"),
            card("MW_TRD_000003", "archived"),
        )})
        self.assertEqual([c.card_id for c in ds.live_trending()],
                         ["MW_TRD_000001"])

    def test_sort_order_wins_and_card_id_breaks_the_tie(self):
        """Two cards sharing a sort_order must not swap between builds — the
        homepage would reorder itself for no reason a reader could see."""
        ds = dataset.Dataset(trending={c.card_id: c for c in (
            card("MW_TRD_000009", sort_order=2),
            card("MW_TRD_000002", sort_order=1),
            card("MW_TRD_000005", sort_order=1),
        )})
        self.assertEqual([c.card_id for c in ds.live_trending()],
                         ["MW_TRD_000002", "MW_TRD_000005", "MW_TRD_000009"])


class RenderTest(unittest.TestCase):
    def test_no_live_cards_renders_nothing(self):
        """The whole fallback story: "" is what makes the landing page keep
        its hand-written feature card."""
        self.assertEqual(trending.carousel([]), "")

    def test_a_linked_card_is_an_anchor_and_the_whole_card_is_the_target(self):
        html = trending.carousel([card(link_url="/scorchers/")])
        self.assertIn('<a class="el-trend-card', html)
        self.assertIn('href="/scorchers/"', html)

    def test_an_unlinked_card_is_not_a_dead_link(self):
        html = trending.carousel([card(link_url="")])
        self.assertNotIn("<a ", html)
        self.assertIn("<div class=", html)
        self.assertNotIn("el-trend-cta", html)

    def test_the_button_falls_back_to_a_house_default(self):
        html = trending.carousel([card(link_url="/matches/", link_label="")])
        self.assertIn(trending.DEFAULT_CTA, html)
        html = trending.carousel([card(link_url="/matches/",
                                       link_label="See the fixtures")])
        self.assertIn("See the fixtures", html)
        self.assertNotIn(trending.DEFAULT_CTA, html)

    def test_a_photo_renders_only_when_the_build_resolved_it(self):
        """image_path names an object in a bucket; the URL is resolved by
        build.py. An unresolved one renders a text-only card rather than a
        broken image."""
        one = card(image_path="2026-08/a.jpg", image_alt="Bullets fans")
        self.assertNotIn("<img", trending.carousel([one]))
        html = trending.carousel([one], {"2026-08/a.jpg": "trending/abc.jpg"})
        self.assertIn('src="trending/abc.jpg"', html)
        self.assertIn('alt="Bullets fans"', html)
        self.assertIn('loading="lazy"', html)

    def test_the_photo_credit_renders_small_and_last(self):
        html = trending.carousel(
            [card(image_path="p.jpg", image_credit="FAM Media",
                  link_url="/matches/", link_label="See them")],
            {"p.jpg": "trending/p.jpg"})
        self.assertIn('<span class="el-trend-credit">Photo: FAM Media</span>',
                      html)
        # After the call to action, not before the headline: it is an
        # obligation to the photographer, not part of the story.
        self.assertGreater(html.index("el-trend-credit"),
                           html.index("el-trend-cta"))

    def test_a_credit_with_no_photo_renders_nothing(self):
        """Dropped by the renderer, not asked of the writer: a photo can be
        cleared off a card months after its credit was typed, and the site must
        not then thank somebody for a picture nobody can see."""
        # No image_path at all…
        self.assertNotIn("el-trend-credit",
                         trending.carousel([card(image_credit="FAM Media")]))
        # …and a path the build could not resolve to a URL.
        self.assertNotIn("el-trend-credit", trending.carousel(
            [card(image_path="p.jpg", image_credit="FAM Media")]))

    def test_the_credit_is_escaped(self):
        html = trending.carousel(
            [card(image_path="p.jpg", image_credit='Mary & "Co" <b>')],
            {"p.jpg": "trending/p.jpg"})
        self.assertNotIn("<b>", html)
        self.assertIn("Mary &amp; &quot;Co&quot;", html)

    def test_a_card_with_no_photo_is_marked_so_its_words_can_be_centred(self):
        self.assertIn("is-plain", trending.carousel([card()]))
        self.assertNotIn("is-plain", trending.carousel(
            [card(image_path="p.jpg")], {"p.jpg": "trending/p.jpg"}))

    def test_every_field_is_escaped(self):
        html = trending.carousel([card(
            headline='Bullets & "Wanderers" <b>draw</b>',
            eyebrow="A & B", body="1 < 2", image_alt='He said "no"',
            image_path="p.jpg", link_url="/a?b=1&c=2", link_label="Go & see")],
            {"p.jpg": "trending/p.jpg"})
        self.assertNotIn("<b>draw</b>", html)
        self.assertIn("&amp;", html)
        self.assertIn("&lt; 2", html)
        self.assertIn("/a?b=1&amp;c=2", html)

    def test_the_dots_ship_hidden_for_the_script_to_reveal(self):
        """Without JavaScript they would be buttons that do nothing, and the
        track swipes either way."""
        html = trending.carousel([card("MW_TRD_000001"), card("MW_TRD_000002")])
        self.assertIn("<div class=\"el-trend-dots\" data-trend-dots hidden>", html)
        self.assertEqual(html.count("data-trend-dot="), 2)

    def test_one_card_gets_no_dots(self):
        self.assertNotIn("el-trend-dots", trending.carousel([card()]))

    def test_a_screen_reader_is_told_which_slide_it_is_on(self):
        html = trending.carousel([card("MW_TRD_000001"), card("MW_TRD_000002")])
        self.assertIn('aria-label="1 of 2"', html)
        self.assertIn('aria-label="2 of 2"', html)

    def test_the_slide_role_never_lands_on_the_link_itself(self):
        """role="group" on the <a> would override its link role, so a card
        that IS a link would stop being announced as one. The labelled group
        is the wrapper; the card inside it keeps its own semantics."""
        html = trending.carousel([card(link_url="/scorchers/")])
        anchor = html[html.index("<a "):html.index(">", html.index("<a "))]
        self.assertNotIn("role=", anchor)
        self.assertNotIn("aria-roledescription", anchor)
        self.assertIn('<div class="el-trend-slide" role="group"'
                      ' aria-roledescription="slide"', html)


class CheckTrendingTest(unittest.TestCase):
    def _errors(self, *cards):
        ds = dataset.Dataset(trending={c.card_id: c for c in cards})
        return validate.check_trending(ds)

    def test_a_path_and_an_https_url_are_both_fine(self):
        self.assertEqual(self._errors(
            card("MW_TRD_000001", link_url="/scorchers/"),
            card("MW_TRD_000002", link_url="/players/CAF_MW_000123.html"),
            card("MW_TRD_000003", link_url="https://example.org/story"),
            card("MW_TRD_000004", link_url=""),
        ), [])

    def test_a_script_link_on_a_live_card_stops_the_build(self):
        errors = self._errors(card(link_url="javascript:alert(1)"))
        self.assertEqual(len(errors), 1)
        self.assertIn("MW_TRD_000001", errors[0])

    def test_a_protocol_relative_link_leaves_the_site_and_is_refused(self):
        """`//host` and `/\\host` are followed OFF this site by a browser, and
        both sail through a naive "starts with a slash" test."""
        self.assertEqual(len(self._errors(card(link_url="//evil.example"))), 1)
        self.assertEqual(len(self._errors(card(link_url="/\\evil.example"))), 1)

    def test_plain_http_is_refused(self):
        self.assertEqual(len(self._errors(card(link_url="http://example.org"))), 1)

    def test_a_bad_link_on_a_card_nobody_can_see_does_not_stop_the_build(self):
        """THE RULE THIS CHECK EXISTS TO OBEY. An ERROR here fails the deploy
        for the whole site; a draft is not on the site, so refusing to build
        over one would be the validator causing the outage it prevents."""
        self.assertEqual(self._errors(
            card("MW_TRD_000001", "draft", link_url="javascript:alert(1)"),
            card("MW_TRD_000002", "archived", link_url="//evil.example"),
        ), [])


class SearchVersionTest(unittest.TestCase):
    """Rewording a homepage card must not re-download the search index for
    every reader — the tab holds no searchable entity, so it is not hashed
    into the index's cache-busting version."""

    def test_a_card_edit_does_not_move_the_index_version(self):
        from src import search
        base = {tab: dataset.empty_csv(tab) if tab in dataset.SUPABASE_ONLY_TABS
                else "" for tab in dataset.TABS}
        before = search.index_version(base)
        after = search.index_version({
            **base,
            "trending": HEADER + "MW_TRD_000001,live,,A new headline,,,,,,1,\n",
        })
        self.assertEqual(before, after)

    def test_a_real_data_change_still_moves_it(self):
        from src import search
        self.assertNotEqual(search.index_version({"matches": "a"}),
                            search.index_version({"matches": "b"}))


class MigrationParityTest(unittest.TestCase):
    """The link rule is written twice — in Postgres, where a browser cannot go
    round it, and here, where the build re-checks it. Two copies of a rule
    drift; this is the cheapest thing that notices."""

    def test_the_sql_and_python_link_patterns_agree(self):
        path = os.path.join(ROOT, "supabase", "migrations", "0030_trending.sql")
        with open(path, encoding="utf-8") as fh:
            sql = fh.read()
        # Both halves appear in the migration — in the CHECK constraint and
        # again in save_trending_card, which restates them to give the portal
        # a sentence a person can act on.
        self.assertEqual(sql.count(r"'^/([^/\\\s][^\s]*)?$'"), 2)
        self.assertEqual(sql.count(r"'^https://[^\s]+$'"), 2)
        # And the same two, alternated, is what the build re-checks.
        self.assertEqual(validate._SAFE_LINK.pattern,
                         r"^(/([^/\\\s][^\s]*)?|https://[^\s]+)$")


if __name__ == "__main__":
    unittest.main()
