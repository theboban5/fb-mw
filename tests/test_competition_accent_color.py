"""Migration 0040 — competitions.accent_color.

The hex-colour rule is written twice — once in the CHECK constraint, once in
set_competition_accent_color's own validation, so the RPC can refuse with a
sentence a person can act on before ever reaching the constraint. Two copies
of a rule drift; this is the cheapest thing that notices.
"""

import os
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)

MIGRATION = os.path.join(ROOT, "supabase", "migrations",
                          "0040_competition_accent_color.sql")


class MigrationParityTest(unittest.TestCase):
    def test_the_hex_pattern_appears_in_the_check_and_the_rpc(self):
        with open(MIGRATION, encoding="utf-8") as fh:
            sql = fh.read()
        self.assertEqual(sql.count(r"'^#[0-9a-fA-F]{6}$'"), 2)


if __name__ == "__main__":
    unittest.main()
