"""Tests for the site search index (src/search.py).

The stakes here are 404s: a search result that points at a page the build never
wrote is worse than no search at all. So most of these assert that the index
agrees with what the other builders actually emit, and the rest pin the
normalisation rules that static/search.js has to match.
"""

import json
import os
import re
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src import adapt, dataset, hubs, render, search  # noqa: E402

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CANONICAL = os.path.join(ROOT, "data", "canonical")


def _load_canonical():
    """The committed snapshot, parsed. Skips the suite if it is not there."""
    return dataset.read_snapshot(CANONICAL)


class NormaliseTest(unittest.TestCase):
    """Pins the rules static/search.js implements in JS.

    These two are a matched pair — change one and you must change the other.
    """

    def norm(self, s):
        """The Python mirror of search.js norm()."""
        import unicodedata
        s = unicodedata.normalize("NFD", str(s).lower())
        s = "".join(c for c in s if not unicodedata.combining(c))
        s = re.sub(r"['’`]", "", s)
        return re.sub(r"[^a-z0-9]+", " ", s).strip()

    def test_apostrophes_are_stripped_not_split(self):
        # Malawian club names carry apostrophes mid-word; "ngwangwazi" is what
        # somebody actually types into a phone.
        self.assertEqual(self.norm("Ngw'Angw'Azi Wimbe FC"), "ngwangwazi wimbe fc")
        self.assertEqual(self.norm("Balang'Ombre Bombers"), "balangombre bombers")
        self.assertEqual(self.norm("M'mbelwa Warriors"), "mmbelwa warriors")

    def test_punctuation_and_accents_fold(self):
        self.assertEqual(self.norm("Blue Eagles  -  Reserves"), "blue eagles reserves")
        self.assertEqual(self.norm("Café FC"), "cafe fc")


class IndexVersionTest(unittest.TestCase):
    def test_stable_for_the_same_data(self):
        a = search.index_version({"clubs": "x"}, {"nt_teams": "y"})
        b = search.index_version({"clubs": "x"}, {"nt_teams": "y"})
        self.assertEqual(a, b)
        self.assertEqual(len(a), 8)

    def test_changes_when_data_changes(self):
        a = search.index_version({"clubs": "x"})
        b = search.index_version({"clubs": "x2"})
        self.assertNotEqual(a, b)


class TemplateTokenTest(unittest.TestCase):
    def test_base_template_has_the_search_token(self):
        # If the token is dropped from the template the widget silently
        # vanishes from every page; if a renderer forgets to substitute it,
        # "{{SEARCH}}" ships as visible text.
        with open(os.path.join(ROOT, "templates", "base.html"), encoding="utf-8") as fh:
            self.assertIn("{{SEARCH}}", fh.read())

    def test_widget_resolves_urls_against_the_page_prefix(self):
        render.SEARCH_INDEX_VERSION = "abc12345"
        render.SEARCH_JS_VERSION = "def67890"
        try:
            deep = render.search_widget("../../")
            self.assertIn('data-search-index="../../search-index.json?v=abc12345"', deep)
            self.assertIn('src="../../search.js?v=def67890"', deep)
            self.assertIn('action="../../search/"', deep)

            root = render.search_widget("")
            self.assertIn('data-search-index="search-index.json?v=abc12345"', root)
            self.assertIn('action="search/"', root)
        finally:
            render.SEARCH_INDEX_VERSION = ""
            render.SEARCH_JS_VERSION = ""

    def test_ships_the_short_placeholder_and_upgrades_from_it(self):
        # The HTML must carry the short label: it is what a no-JS visitor sees
        # and what renders before search.js runs, and it is the only one that
        # fits a 320px screen without being cut mid-word.
        html = render.search_widget("")
        self.assertIn(f'placeholder="{render.SEARCH_PLACEHOLDER_SHORT}"', html)
        self.assertIn(f'data-ss-placeholder="{render.SEARCH_PLACEHOLDER}"', html)
        # The full description is always available to screen readers.
        self.assertIn(f'aria-label="{render.SEARCH_PLACEHOLDER}"', html)
        self.assertLess(len(render.SEARCH_PLACEHOLDER_SHORT),
                        len(render.SEARCH_PLACEHOLDER))

    def test_hero_variant_is_marked(self):
        self.assertIn("site-search-hero", render.search_widget("", variant="hero"))
        self.assertNotIn("site-search-hero", render.search_widget(""))


class IndexContentTest(unittest.TestCase):
    """Built from the committed snapshot, so it tests the real data shapes."""

    @classmethod
    def setUpClass(cls):
        texts = _load_canonical()
        if texts is None:
            raise unittest.SkipTest("data/canonical/ snapshot not present")
        cls.ds = dataset.parse_all(texts)
        cls.leagues = [
            adapt.league_data(cls.ds, cs.competition_id, cs.season_id)
            for cs in adapt.current_competition_seasons(cls.ds)
        ]
        cls.rows = search.build_index(cls.ds, cls.leagues, hidden={"MW_U16"})
        cls.by_type = {}
        for r in cls.rows:
            cls.by_type.setdefault(r[0], []).append(r)

    def test_every_url_is_unique(self):
        urls = [r[2] for r in self.rows]
        self.assertEqual(len(urls), len(set(urls)))

    def test_player_records_match_the_pages_hubs_writes(self):
        # The single most important invariant: hubs.build_player_pages and
        # search.py must agree on exactly which players get a page. Both now
        # ask hubs.player_page_ids, so this asserts they both still ask it.
        expected = hubs.player_page_ids(self.ds)
        indexed = {r[2][len("players/"):-len(".html")]
                   for r in self.by_type[search.T_PLAYER]}
        self.assertEqual(indexed, expected)

    def test_an_appearance_alone_earns_a_page(self):
        """Before team sheets a page meant a goal, so a player with thirty
        appearances and none had no page and no way to be found."""
        credits, own = hubs.player_goal_credits(self.ds)
        scorers = {p for p in set(credits) | set(own) if p in self.ds.players}
        played = {pid for pid, career in hubs.player_careers(self.ds).items()
                  if career.appearances}
        self.assertTrue(played <= hubs.player_page_ids(self.ds))
        # And a scorer still earns one whether or not anyone entered a sheet.
        self.assertTrue(scorers <= hubs.player_page_ids(self.ds))

    def test_unknown_player_is_never_indexed(self):
        self.assertNotIn(
            f"players/{dataset.UNKNOWN_PLAYER_ID}.html",
            {r[2] for r in self.rows},
        )

    def test_club_records_cover_clubs_with_a_hub(self):
        # hubs.build_club_hubs writes a hub for every club with a team in a
        # built competition — the same set build.py derives for club_hub_ids.
        expected = {tv.club_id for lg in self.leagues
                    for tv in lg.teams.values() if tv.club_id}
        indexed = {r[2][len("clubs/"):-len(".html")]
                   for r in self.by_type[search.T_CLUB]}
        self.assertEqual(indexed, expected)

    def test_squads_named_after_their_club_are_deduped(self):
        club_names = {c.name.casefold() for c in self.ds.clubs.values()}
        for row in self.by_type.get(search.T_TEAM, []):
            self.assertNotIn(row[1].casefold(), club_names,
                             f"{row[1]!r} duplicates its club's hub row")

    def test_squads_that_differ_from_their_club_survive(self):
        # e.g. "Wanderers Reserve" under club "Mighty Wanderers". If this ever
        # hits zero the dedup rule has become too aggressive.
        self.assertGreater(len(self.by_type.get(search.T_TEAM, [])), 0)

    def test_no_cup_squad_urls(self):
        # Cups return before writing per-competition club pages
        # (render.build_site), so a cup squad URL would 404.
        cup_slugs = {lg.slug for lg in self.leagues if lg.kind == "cup"}
        for row in self.by_type.get(search.T_TEAM, []):
            self.assertNotIn(row[2].split("/")[0], cup_slugs)

    def test_hidden_competitions_are_excluded(self):
        for row in self.by_type[search.T_COMP]:
            self.assertNotEqual(row[2], "u16/")
        for row in self.by_type.get(search.T_TEAM, []):
            self.assertNotEqual(row[2].split("/")[0], "u16")

    def test_meta_is_plain_text_not_html(self):
        # search.js renders every field through textContent, so an entity here
        # would show up literally on screen.
        for row in self.rows:
            self.assertNotIn("&middot;", row[3])
            self.assertNotIn("&amp;", row[3])

    def test_competition_outranks_any_club_or_player(self):
        top_comp = max(r[4] for r in self.by_type[search.T_COMP])
        top_club = max(r[4] for r in self.by_type[search.T_CLUB])
        top_player = max(r[4] for r in self.by_type[search.T_PLAYER])
        self.assertGreater(top_comp, top_club)
        self.assertGreater(top_club, top_player)

    def test_higher_tier_clubs_weigh_more(self):
        weights = {r[1]: r[4] for r in self.by_type[search.T_CLUB]}
        # Nyasa Big Bullets play in the tier-1 Super League; any club whose
        # best competition is tier 3 must rank below them.
        self.assertIn("Nyasa Big Bullets", weights)
        self.assertGreaterEqual(weights["Nyasa Big Bullets"],
                                max(weights.values()))

    def test_club_caption_names_its_senior_competition(self):
        # competitions.tier is scoped to an age group: Katswiri U19 League is
        # tier 1 just as the Super League is. Picking the club's "main"
        # competition on tier alone captioned every Super League club with its
        # U19 side — senior football has to win first.
        meta = {r[1]: r[3] for r in self.by_type[search.T_CLUB]}
        self.assertIn("Super League of Malawi", meta["Nyasa Big Bullets"])
        self.assertIn("Super League of Malawi", meta["Mighty Wanderers"])

    def test_youth_only_clubs_do_not_earn_a_top_flight_bonus(self):
        rows = {r[1]: r[4] for r in self.by_type[search.T_CLUB]}
        senior_top = rows["Nyasa Big Bullets"]
        for row in self.by_type[search.T_CLUB]:
            if "U19" in row[3] or "U16" in row[3]:
                self.assertLess(row[4], senior_top, row[1])

    def test_youth_competition_does_not_tie_the_top_flight(self):
        w = {r[1]: r[4] for r in self.by_type[search.T_COMP]}
        sl = next(v for k, v in w.items() if "Premiership" in k or "Super League" in k)
        ku19 = next((v for k, v in w.items() if "U19" in k), None)
        if ku19 is not None:
            self.assertLess(ku19, sl)

    def test_index_stays_small_enough_to_ship(self):
        payload = json.dumps({"v": 1, "types": list(search.TYPES), "docs": self.rows},
                             ensure_ascii=False, separators=(",", ":"))
        # A tripwire: adding all 1,316 matches would blow straight through this.
        self.assertLess(len(payload.encode("utf-8")), 80_000)


if __name__ == "__main__":
    unittest.main()
