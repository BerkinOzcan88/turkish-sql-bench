"""The core claim behind the case_i category: SQLite's LOWER()/UPPER() are ASCII-only.

    python -m unittest discover tests
"""
import sqlite3
import unittest


class SQLiteTurkishCaseTest(unittest.TestCase):
    def setUp(self):
        self.con = sqlite3.connect(":memory:")

    def scalar(self, sql, *args):
        return self.con.execute(sql, args).fetchone()[0]

    def test_lower_igdir_is_not_turkish_lowercase(self):
        result = self.scalar("SELECT LOWER('IĞDIR')")
        self.assertNotEqual(result, "ığdır")
        self.assertEqual(result, "iĞdir")  # only ASCII I/D/R are folded

    def test_lower_does_not_match_proper_case_spelling(self):
        self.assertNotEqual(self.scalar("SELECT LOWER('IĞDIR')"), self.scalar("SELECT LOWER('Iğdır')"))

    def test_upper_istanbul_keeps_dotted_capital(self):
        # 'İ' is non-ASCII and left alone, so this one happens to round-trip
        self.assertEqual(self.scalar("SELECT UPPER('İstanbul')"), "İSTANBUL")
        self.assertNotEqual(self.scalar("SELECT LOWER('İSTANBUL')"), "istanbul")

    def test_like_folds_ascii_only(self):
        self.assertEqual(self.scalar("SELECT 'Ankara' LIKE 'ankara'"), 1)
        self.assertEqual(self.scalar("SELECT 'AĞRI' LIKE 'ağrı'"), 0)


if __name__ == "__main__":
    unittest.main()
