"""Past seasons: which get a page, what the page says, and where it links.

Offline. The committed snapshot has no backfilled season, so the fixture is
the snapshot plus two RSSSF seasons merged in through scripts/season.py's own
planner — the rows a real `apply` would write, not a hand-made imitation.
"""

import csv
import io
import json
import os
import re
import sys
import tempfile
import unittest
import urllib.parse

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "scripts"))

import build  # noqa: E402
import season  # noqa: E402
from src import adapt, dataset, hubs, matches_page, render  # noqa: E402

CANONICAL = os.path.join(ROOT, "data", "canonical")
BACKFILL = os.path.join(ROOT, "data", "backfill", "rsssf")
SEASONS = ("sl_2023.json", "sl_2025.json")


def merged_texts():
    """The snapshot's CSV texts with SEASONS applied, as season.py plans them."""
    texts = dataset.read_snapshot(CANONICAL, dataset.TABS)
    if texts is None:
        return None
    tabs = ("clubs", "teams", "competitions", "seasons", "competition_seasons",
            "entries", "venues", "matches")
    existing = {t: list(csv.DictReader(io.StringIO(texts[t]))) for t in tabs}
    for m in existing["matches"]:
        m["date"] = m["date"] or None
    for name in SEASONS:
        with open(os.path.join(BACKFILL, name), encoding="utf-8") as fh:
            plan, _ = season.build_plan(json.load(fh), existing)
        for attr, _key in season.WRITE_ORDER:
            rows = getattr(plan, attr)
            if not rows:
                continue
            existing[attr] += rows
            header = next(csv.reader(io.StringIO(texts[attr])))
            out = io.StringIO()
            w = csv.writer(out, lineterminator="\n")
            for r in rows:
                w.writerow(["" if r.get(h) is None else r.get(h, "") for h in header])
            texts[attr] = texts[attr].rstrip("\r\n") + "\n" + out.getvalue()
    return texts


class ArchiveTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        texts = merged_texts()
        if texts is None:
            raise unittest.SkipTest("data/canonical/ snapshot not present")
        cls.ds = dataset.parse_all(texts)
        cls.archives = adapt.archive_competition_seasons(cls.ds)

    def test_backfilled_seasons_get_a_page_newest_first(self):
        sl = [cs.season_id for cs in self.archives if cs.competition_id == "MW_SL"]
        self.assertEqual(sl, ["MW_2025", "MW_2023"])

    def test_the_current_season_is_never_an_archive(self):
        current = {(cs.competition_id, cs.season_id)
                   for cs in adapt.current_competition_seasons(self.ds)}
        for cs in self.archives:
            self.assertNotIn((cs.competition_id, cs.season_id), current)

    def test_season_slug(self):
        self.assertEqual(adapt.season_slug("2025"), "2025")
        self.assertEqual(adapt.season_slug("2020/21"), "2020-21")

    def test_day_view_leaves_archives_out(self):
        days = matches_page.collect(self.ds)
        self.assertNotIn("2025-04-05", days)        # SL 2025 matchday 1

    def test_archive_page(self):
        current = next(cs for cs in adapt.current_competition_seasons(self.ds)
                       if cs.competition_id == "MW_SL")
        cs = next(c for c in self.archives if c.season_id == "MW_2023")
        links = [("26/27", "../", False), ("2025", "../2025/", False),
                 ("2023", "../2023/", True)]
        with tempfile.TemporaryDirectory() as dist:
            league, rows, played = build._build_league(
                self.ds, cs, dist, "", season_links=links,
                archive_hub_ids={"MW_MB", "MW_BLUE"})
            self.assertEqual(played, 240)
            # Head to head, not the alphabet, keeps Blue Eagles in 14th.
            order = {r.code: r.position for r in rows}
            codes = {t.team_id: c for c, t in league.teams.items()}
            self.assertEqual(order[codes["MW_MB_M1"]], 13)
            self.assertEqual(order[codes["MW_BLUE_M1"]], 14)

            out = os.path.join(dist, "sl", "2023")
            self.assertFalse(os.path.exists(os.path.join(out, "overview.html")))
            with open(os.path.join(out, "index.html"), encoding="utf-8") as fh:
                page = fh.read()
            self.assertIn('class="v2-seasons"', page)
            self.assertIn('aria-current="page">2023<', page)
            self.assertIn("rsssf.org/tablesm/malaw2023.html", page)
            self.assertNotIn("v2-arrow", page)       # no "since last matchday"
            with open(os.path.join(out, "results.html"), encoding="utf-8") as fh:
                results = fh.read()
            self.assertIn("MAY 2023", results)       # grouped by month
            self.assertNotIn("../../match/", results)  # no match pages

            # Every link inside the season resolves, except the hub links the
            # test put there on purpose (no hubs are built here).
            for path in ("index.html", "results.html"):
                with open(os.path.join(out, path), encoding="utf-8") as fh:
                    html = fh.read()
                for href in re.findall(r'href="([^"#?]+)', html):
                    if href.startswith(("http", "mailto", "//", "../../")):
                        continue
                    if href.startswith("../"):
                        continue                     # sibling seasons, not built here
                    target = os.path.normpath(os.path.join(out, urllib.parse.unquote(href)))
                    self.assertTrue(os.path.exists(target), f"{path}: {href}")
            # A team with no hub links to its own season page instead.
            self.assertNotIn("../../clubs/MW_KU.html", page)
            self.assertIn("../../clubs/MW_MB.html", page)


class SeasonNavTest(unittest.TestCase):
    def test_one_season_is_no_picker(self):
        self.assertEqual(render.season_nav([("26/27", "./", True)]), "")
        self.assertEqual(render.season_nav(None), "")

    def test_links_keep_the_tab(self):
        html = render.season_nav([("26/27", "../", False), ("2025", "../2025/", True)],
                                 "results.html")
        self.assertIn('href="../results.html"', html)
        self.assertIn('aria-current="page">2025<', html)


class PastSeasonsOnHubTest(unittest.TestCase):
    def test_nothing_without_past(self):
        self.assertEqual(hubs._past_seasons(None), [])


if __name__ == "__main__":
    unittest.main()
