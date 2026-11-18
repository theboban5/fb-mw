"""The ideas board (0075) must never become a Dataset tab.

Everything the build fetches is snapshotted into data/canonical/, which is
public, and an idea is a note about the site's plans: a marketing idea, "move
to a private repo". The tables are admin-only in the database; this is the
other half, so that the privacy rule lives in a test rather than in someone
remembering it.
"""

import os
import re
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from src import dataset  # noqa: E402

PRIVATE = ("ideas", "idea_lanes")


class IdeasStayPrivateTest(unittest.TestCase):

    def test_not_a_dataset_tab(self):
        for name in PRIVATE:
            with self.subTest(table=name):
                self.assertNotIn(name, dataset.TABS)
                self.assertNotIn(name, dataset.NT_TABS)

    def test_no_build_module_names_them(self):
        """A table can be read without being a tab; nothing on the build side
        may mention either name as a quoted identifier."""
        paths = [os.path.join(ROOT, "build.py"), os.path.join(ROOT, "validate.py")]
        src = os.path.join(ROOT, "src")
        paths += [os.path.join(src, f) for f in os.listdir(src) if f.endswith(".py")]
        pattern = re.compile(r"""["'](%s)["']""" % "|".join(PRIVATE))
        for path in paths:
            with open(path, encoding="utf-8") as fh:
                hit = pattern.search(fh.read())
            with self.subTest(path=os.path.relpath(path, ROOT)):
                self.assertIsNone(hit, f"{path} names {hit and hit.group(0)}")

    def test_not_in_the_public_snapshot(self):
        canonical = os.path.join(ROOT, "data", "canonical")
        if not os.path.isdir(canonical):
            self.skipTest("no snapshot in this checkout")
        names = {os.path.splitext(f)[0] for f in os.listdir(canonical)}
        for name in PRIVATE:
            self.assertNotIn(name, names)


if __name__ == "__main__":
    unittest.main()
