"""How model answers are parsed and scored (scripts/evaluate.py, scripts/validate.py).

    python -m unittest discover tests
"""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
import evaluate as ev  # noqa: E402
import leaderboard as lb  # noqa: E402
from validate import QueryError, cells_equal  # noqa: E402


def fence(tag, body):
    return f"```{tag}\n{body}\n```"


class EngineSQLTest(unittest.TestCase):
    # date arithmetic has no portable form, so a question may carry SQL_postgres / trap_sql_postgres
    q = {"SQL": "SELECT julianday('2024-01-15') - julianday('2024-01-01')",
         "SQL_postgres": "SELECT DATE '2024-01-15' - DATE '2024-01-01'", "trap_sql": "SELECT 13"}

    def test_engine_override_is_used_on_its_engine(self):
        self.assertEqual(ev.engine_sql(self.q, "SQL", "postgres"), self.q["SQL_postgres"])

    def test_other_engines_use_the_shared_sql(self):
        self.assertEqual(ev.engine_sql(self.q, "SQL", "sqlite"), self.q["SQL"])
        self.assertEqual(ev.engine_sql(self.q, "trap_sql", "postgres"), "SELECT 13")


class ExtractSQLTest(unittest.TestCase):
    def test_known_tags(self):
        for tag in ("sql", "SQL", "sqlite", "postgresql", ""):
            self.assertEqual(ev.extract_sql(fence(tag, "SELECT 1;")), "SELECT 1", tag)

    def test_unknown_tag_is_not_part_of_the_query(self):
        for tag in ("mysql", "tsql", "postgres16", "sql92"):
            self.assertEqual(ev.extract_sql(fence(tag, "SELECT 1")), "SELECT 1", tag)

    def test_inline_mention_of_a_fence_is_not_a_block(self):
        text = "The task says to answer in a single ```sql code block.\n\n" + fence("sql", "SELECT COUNT(*) FROM abone;")
        self.assertEqual(ev.extract_sql(text), "SELECT COUNT(*) FROM abone")

    def test_trailing_non_sql_block_is_ignored(self):
        text = fence("sql", "SELECT 4") + "\nRun it like:\n" + fence("bash", "sqlite3 db.sqlite")
        self.assertEqual(ev.extract_sql(text), "SELECT 4")

    def test_last_sql_block_wins(self):
        self.assertEqual(ev.extract_sql(fence("sql", "SELECT 1") + "\nor better\n" + fence("sql", "SELECT 2")),
                         "SELECT 2")

    def test_reasoning_is_dropped(self):
        self.assertEqual(ev.extract_sql("<think>maybe ```sql\nSELECT 9\n```</think>\n" + fence("sql", "SELECT 3")),
                         "SELECT 3")

    def test_unclosed_reasoning_is_no_answer(self):
        self.assertIsNone(ev.extract_sql("<think>\nlet me see, SELECT"))

    def test_bare_query_without_fence(self):
        self.assertEqual(ev.extract_sql("SELECT COUNT(*)\nFROM abone;"), "SELECT COUNT(*)\nFROM abone")

    def test_prose_word_with_is_not_a_query(self):
        self.assertIsNone(ev.extract_sql("Here is the query with the join you need."))


class SQLiteRunnerTest(unittest.TestCase):
    def test_multiple_statements_are_refused(self):
        runner = ev.SQLiteRunner("belediye_su")
        with self.assertRaisesRegex(QueryError, "multiple statements"):
            runner.run("SELECT 1; SELECT 2")


class ScoreItemTest(unittest.TestCase):
    Q = {"question_id": "x_001", "trap": "control", "pair_id": "x_p01", "question": "?", "SQL": "SELECT 1"}

    def score(self, content, finish_reason="stop"):
        runner = ev.SQLiteRunner("belediye_su")
        return ev.score_item(self.Q, "sqlite", runner, {"content": content, "finish_reason": finish_reason},
                             [(1.0,)], None)

    def test_complete_answer_is_scored(self):
        item = self.score(fence("sql", "SELECT 1"))
        self.assertEqual(item["status"], "correct")
        self.assertTrue(item["relaxed"])

    def test_truncated_answer_is_not_scored(self):
        # a reply cut off by max_tokens may hold only a draft; never credit it
        item = self.score(fence("sql", "SELECT 1") + "\nWait, I should check", finish_reason="length")
        self.assertEqual(item["status"], "truncated")
        self.assertFalse(item["correct"])
        self.assertFalse(item["relaxed"])


class SummarizeTest(unittest.TestCase):
    def item(self, trap, pair, relaxed, status):
        return {"engine": "sqlite", "trap": trap, "pair_id": pair, "relaxed": relaxed, "correct": relaxed,
                "status": status, "trap_hit": None}

    def test_pair_with_a_missing_answer_is_not_counted(self):
        items = [self.item("domain", "p1", False, "missing"), self.item("control", "p1", True, "correct"),
                 self.item("case_i", "p2", False, "wrong"), self.item("control", "p2", True, "correct")]
        self.assertEqual(ev.summarize(items, ["sqlite"])["sqlite"]["pairs"], {"fell_for_trap": 1})


class RelaxedEqualTest(unittest.TestCase):
    GOLD = [("Ali", "Kaya")]

    def test_exact(self):
        self.assertTrue(ev.relaxed_equal([("Ali", "Kaya")], self.GOLD, False))

    def test_extra_column(self):
        self.assertTrue(ev.relaxed_equal([("Ali", "Kaya", "20190012")], self.GOLD, False))
        self.assertTrue(ev.relaxed_equal([(5.0, 99.0)], [(5.0,)], False))

    def test_columns_in_another_order(self):
        self.assertTrue(ev.relaxed_equal([("Kaya", "Ali")], self.GOLD, False))

    def test_name_and_surname_in_one_column(self):
        self.assertTrue(ev.relaxed_equal([("Ali Kaya",)], self.GOLD, False))
        self.assertTrue(ev.relaxed_equal([("Ali Kaya", "Muhasebe")], self.GOLD, False))

    def test_wrong_name_still_wrong(self):
        self.assertFalse(ev.relaxed_equal([("Ali Kaya",)], [("Ali", "Kara")], False))
        self.assertFalse(ev.relaxed_equal([("AliKaya",)], self.GOLD, False))

    def test_rows_must_stay_aligned(self):
        gold = [("A", 1.0), ("B", 2.0)]
        self.assertTrue(ev.relaxed_equal([("B", 2.0, "y"), ("A", 1.0, "x")], gold, False))
        self.assertFalse(ev.relaxed_equal([("A", 2.0, "x"), ("B", 1.0, "y")], gold, False))

    def test_row_count_must_match(self):
        self.assertFalse(ev.relaxed_equal([("Ali", "Kaya"), ("Ali", "Kaya")], self.GOLD, False))
        self.assertFalse(ev.relaxed_equal([], self.GOLD, False))

    def test_order_matters_when_gold_is_ordered(self):
        gold = [("A", 1.0), ("B", 2.0)]
        self.assertTrue(ev.relaxed_equal([(1.0, "A"), (2.0, "B")], gold, True))
        self.assertFalse(ev.relaxed_equal([(2.0, "B"), (1.0, "A")], gold, True))


class CellsEqualTest(unittest.TestCase):
    def test_cent_tolerance(self):
        self.assertTrue(cells_equal(10.0, 10.01))
        self.assertFalse(cells_equal(10.0, 10.02))

    def test_rounding_per_invoice_matches_rounding_the_sum(self):
        # belediye_su_041 / 043: SUM(ROUND(x, 2)) vs ROUND(SUM(x), 2) over thousands of invoices
        self.assertTrue(cells_equal(3637608.94, 3637611.16))
        self.assertTrue(cells_equal(248870.59, 248866.44))

    def test_counts_stay_exact(self):
        self.assertFalse(cells_equal(57166.0, 57167.0))
        self.assertFalse(cells_equal(704.0, 705.0))

    def test_real_differences_still_fail(self):
        self.assertFalse(cells_equal(3637608.94, 3637708.94))  # 100 TL off on 3.6 M


class RunBookkeepingTest(unittest.TestCase):
    META = {"temperature": 0.0, "max_tokens": 2048, "sample_rows": 3, "extra_body": {},
            "no_think_prefill": False, "n_questions": 100, "engines": ["sqlite", "postgres"]}

    def test_same_settings_do_not_conflict(self):
        self.assertEqual(ev.settings_conflict(self.META, dict(self.META)), [])

    def test_changed_settings_conflict(self):
        new = dict(self.META, no_think_prefill=True, max_tokens=8192)
        self.assertEqual(sorted(ev.settings_conflict(self.META, new)), ["max_tokens", "no_think_prefill"])

    def test_subset_of_a_full_run_is_partial(self):
        self.assertTrue(ev.is_partial(self.META, n_items=8, subset_flags=False))
        self.assertFalse(ev.is_partial(self.META, n_items=200, subset_flags=False))
        self.assertFalse(ev.is_partial(None, n_items=8, subset_flags=False))
        self.assertTrue(ev.is_partial(None, n_items=8, subset_flags=True))

    def test_prompt_dump_shows_every_message(self):
        text = ev.format_messages([{"role": "user", "content": "Q?"},
                                   {"role": "assistant", "content": ev.NO_THINK_PREFILL}])
        self.assertIn("Q?", text)
        self.assertIn("</think>", text)
        self.assertEqual(ev.format_messages([{"role": "user", "content": "Q?"}]), "Q?")


class LeaderboardTest(unittest.TestCase):
    def scores(self, **meta):
        eng = {"n": 50, "relaxed": 30, "control": [20, 25], "trap": [10, 25],
               "pairs": {"both_correct": 8, "fell_for_trap": 12, "control_only_wrong": 2, "both_wrong": 3},
               "trap_sql_reproduced": [3, 25], "status": {"correct": 30, "wrong": 18, "truncated": 2}}
        base = {"run": "lmstudio__m", "model": "m", "engines": ["sqlite"], "max_tokens": 2048,
                "no_think_prefill": False}
        return {"meta": {**base, **meta}, "summary": {"sqlite": eng}, "missing": 0}

    def test_fell_for_trap_is_a_share_of_pairs_with_the_control_right(self):
        row = lb.row_for(self.scores())
        self.assertEqual((row["fell"], row["control_right"]), (12, 20))
        self.assertIn("12/20 (60%)", lb.render([row]))

    def test_notes_show_what_makes_runs_not_comparable(self):
        note = lb.row_for(self.scores(no_think_prefill=True, max_tokens=8192))["note"]
        self.assertIn("no-think prefill", note)
        self.assertIn("max_tokens 8192", note)
        self.assertIn("2 truncated", note)
        self.assertIn("subagent", lb.row_for(self.scores(run="subagent__x"))["note"])

    def test_only_complete_runs_on_the_current_suite_are_ranked(self):
        full = dict(suite="abc", engines=["sqlite", "postgres"])
        self.assertIsNone(lb.skip_reason(self.scores(**full), "abc"))
        self.assertIn("before", lb.skip_reason(self.scores(engines=["sqlite", "postgres"]), "abc"))
        self.assertIn("another version", lb.skip_reason(self.scores(**full), "xyz"))
        self.assertIn("sqlite only", lb.skip_reason(self.scores(suite="abc"), "abc"))
        self.assertIn("3 answers missing", lb.skip_reason(dict(self.scores(**full), missing=3), "abc"))


class SuiteFingerprintTest(unittest.TestCase):
    Q = [{"question_id": "a_001", "question": "Kaç abone?", "SQL": "SELECT 1"},
         {"question_id": "a_002", "question": "Kaç fatura?", "SQL": "SELECT 2"}]

    def test_order_does_not_matter_but_content_does(self):
        fp = ev.suite_fingerprint(self.Q, {"a": "schema"}, {"a": ""})
        self.assertEqual(fp, ev.suite_fingerprint(self.Q[::-1], {"a": "schema"}, {"a": ""}))
        changed = [self.Q[0], dict(self.Q[1], SQL="SELECT 3")]
        self.assertNotEqual(fp, ev.suite_fingerprint(changed, {"a": "schema"}, {"a": ""}))
        self.assertNotEqual(fp, ev.suite_fingerprint(self.Q, {"a": "schema 2"}, {"a": ""}))
        self.assertNotEqual(fp, ev.suite_fingerprint(self.Q, {"a": "schema"}, {"a": "note"}))


if __name__ == "__main__":
    unittest.main()
