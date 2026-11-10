"""Properties of the built SQLite databases that the questions rely on.

    python -m unittest discover tests
"""
import re
import sqlite3
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))
import evaluate as ev  # noqa: E402


def connect(db_id):
    return sqlite3.connect(f"file:{(ROOT / 'databases' / db_id / f'{db_id}.sqlite').as_posix()}?mode=ro", uri=True)


def money_columns(db_id):
    """(table, column) for every NUMERIC(p,s) column in schema.sql."""
    ddl = (ROOT / "databases" / db_id / "schema.sql").read_text(encoding="utf-8")
    out = []
    for table, body in re.findall(r"CREATE TABLE (\w+)\s*\((.*?)\n\);", ddl, flags=re.S):
        out += [(table, col) for col in re.findall(r"^\s*(\w+)\s+NUMERIC\(", body, flags=re.M)]
    return out


class MoneyStorageTest(unittest.TestCase):
    def test_money_is_stored_as_real_in_sqlite(self):
        # with NUMERIC affinity SQLite stores 45100.00 as the integer 45100, so `x * rate / 100`
        # does integer division on SQLite only: an engine artefact, not a trap
        for db_id in ("belediye_su", "ik_bordro"):
            con = connect(db_id)
            cols = money_columns(db_id)
            self.assertTrue(cols, db_id)
            for table, col in cols:
                n = con.execute(f"SELECT COUNT(*) FROM {table} WHERE typeof({col}) <> 'real'").fetchone()[0]
                self.assertEqual(n, 0, f"{db_id}.{table}.{col}")

    def test_whole_amount_divides_like_postgres(self):
        con = connect("ik_bordro")
        (v,) = con.execute("SELECT brut_ucret * 2 / 7 FROM bordro WHERE brut_ucret = 45100").fetchone()
        self.assertAlmostEqual(v, 12885.71, places=2)  # integer division would give 12885


class CopyCheckTest(unittest.TestCase):
    def test_sqlite_and_postgres_forms_of_a_row_hash_alike(self):
        import datetime as dt
        from decimal import Decimal
        import load_postgres as lp
        sqlite_rows = [(1, 45100.0, 12.5, "2024-01-01", None, "Iğdır")]
        pg_rows = [(1, Decimal("45100.00"), Decimal("12.50"), dt.date(2024, 1, 1), None, "Iğdır")]
        self.assertEqual(lp.rows_digest(sqlite_rows), lp.rows_digest(pg_rows))
        self.assertNotEqual(lp.rows_digest(sqlite_rows), lp.rows_digest([(1, 45100.0, 12.51, "2024-01-01", None, "Iğdır")]))
        self.assertNotEqual(lp.rows_digest(sqlite_rows), lp.rows_digest([(1, 45100.0, 12.5, "2024-01-01", None, "IĞDIR")]))


class ExampleRowsTest(unittest.TestCase):
    def test_whole_amounts_print_without_decimal_point(self):
        # keeps the prompt text (and so the cached answers) identical to the NUMERIC-affinity build
        self.assertEqual(ev.cell_text(45100.0), "45100")
        self.assertEqual(ev.cell_text(12.5), "12.5")
        self.assertEqual(ev.cell_text(None), "NULL")
        self.assertEqual(ev.cell_text("Iğdır"), "Iğdır")


if __name__ == "__main__":
    unittest.main()
