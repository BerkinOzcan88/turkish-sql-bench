"""Validate benchmark questions against SQLite and PostgreSQL.

For every question, on both engines:
  * gold SQL runs and returns a non-empty, not-all-NULL result;
  * gold results match across engines (numbers compared to 2 decimals,
    row order ignored unless the query has a top-level ORDER BY);
  * trap questions: trap_sql gives a different result than gold on at least
    one engine (an error counts as different); the table reports which.
Also checks the file structure: one trap + one control per pair_id,
controls have trap_sql = null, categories are known.

    python scripts/validate.py                      # all files in questions/
    python scripts/validate.py questions/belediye_su.json
    python scripts/validate.py --engines sqlite     # without Postgres

Exits 1 on any failure. Postgres settings: see scripts/load_postgres.py.
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import sqlite3
import sys
from collections import defaultdict
from decimal import Decimal
from pathlib import Path

import psycopg

sys.path.insert(0, str(Path(__file__).resolve().parent))
from load_postgres import conninfo  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
CATEGORIES = {"case_i", "suffix_value", "schema_link", "tr_format", "domain", "control"}
REQUIRED_KEYS = {"question_id", "db_id", "question", "evidence", "SQL", "trap", "trap_sql", "pair_id", "status"}
TOLERANCE = 0.011  # cents; absorbs float vs NUMERIC rounding at the .005 boundary
# non-integer values may also differ by this share: summing amounts rounded per invoice instead of
# rounding the sum drifts by up to ~1.7e-5 on the belediye_su KDV totals; counts stay exact
REL_TOLERANCE = 2e-5


# ---------------------------------------------------------------- result handling

class QueryError(Exception):
    pass


def norm_cell(v):
    if isinstance(v, bool):
        return int(v)
    if isinstance(v, (int, float, Decimal)):
        return float(v)
    if isinstance(v, (dt.date, dt.datetime)):
        return v.isoformat()
    return v


def sort_key(row):
    return tuple((0, round(c, 2), "") if isinstance(c, float) else (-1, 0, "") if c is None else (1, 0, str(c))
                 for c in row)


def cells_equal(a, b) -> bool:
    if isinstance(a, float) and isinstance(b, float):
        diff = abs(a - b)
        if diff <= TOLERANCE:
            return True
        if a.is_integer() and b.is_integer():
            return False
        return diff <= REL_TOLERANCE * max(abs(a), abs(b))
    return a == b


def results_equal(r1: list[tuple], r2: list[tuple], ordered: bool) -> bool:
    if len(r1) != len(r2):
        return False
    if not ordered:
        r1, r2 = sorted(r1, key=sort_key), sorted(r2, key=sort_key)
    return all(len(x) == len(y) and all(cells_equal(a, b) for a, b in zip(x, y)) for x, y in zip(r1, r2))


def has_top_level_order_by(sql: str) -> bool:
    """True if ORDER BY appears outside parentheses and string literals."""
    depth, i, flat = 0, 0, []
    while i < len(sql):
        ch = sql[i]
        if ch == "'":
            j = i + 1
            while j < len(sql):
                if sql[j] == "'" and (j + 1 >= len(sql) or sql[j + 1] != "'"):
                    break
                j += 2 if sql[j] == "'" else 1
            i = j + 1
            flat.append(" ")
            continue
        if ch == "(":
            depth += 1
        elif ch == ")":
            depth -= 1
        elif depth == 0:
            flat.append(ch)
        i += 1
    return "ORDER BY" in " ".join("".join(flat).upper().split())


def describe(rows: list[tuple] | QueryError) -> str:
    if isinstance(rows, QueryError):
        return f"error: {str(rows).splitlines()[0][:70]}"
    if len(rows) == 1 and len(rows[0]) == 1:
        return repr(rows[0][0])
    return f"{len(rows)} rows"


# ---------------------------------------------------------------- engines

class SQLiteEngine:
    name = "sqlite"

    def __init__(self, db_id: str):
        path = ROOT / "databases" / db_id / f"{db_id}.sqlite"
        if not path.exists():
            raise SystemExit(f"missing {path}; run databases/{db_id}/generate.py")
        self.con = sqlite3.connect(f"file:{path.as_posix()}?mode=ro", uri=True)

    def run(self, sql: str) -> list[tuple]:
        try:
            return [tuple(norm_cell(c) for c in r) for r in self.con.execute(sql).fetchall()]
        except sqlite3.Error as e:
            raise QueryError(str(e)) from None


class PostgresEngine:
    name = "postgres"

    def __init__(self, db_id: str):
        try:
            self.con = psycopg.connect(conninfo(db_id), autocommit=True)
        except psycopg.OperationalError as e:
            raise SystemExit(f"cannot connect to Postgres database {db_id!r}: {e}\n"
                             "start it with `docker compose up -d` and run scripts/load_postgres.py") from None
        self.con.execute("SET statement_timeout = '60s'")

    def run(self, sql: str) -> list[tuple]:
        try:
            return [tuple(norm_cell(c) for c in r) for r in self.con.execute(sql).fetchall()]
        except psycopg.Error as e:
            raise QueryError(str(e)) from None


# ---------------------------------------------------------------- checks

def check_structure(questions: list[dict]) -> list[str]:
    errors = []
    ids = [q.get("question_id") for q in questions]
    for dup in {i for i in ids if ids.count(i) > 1}:
        errors.append(f"duplicate question_id {dup}")
    pairs = defaultdict(list)
    for q in questions:
        qid = q.get("question_id", "?")
        missing = REQUIRED_KEYS - q.keys()
        if missing:
            errors.append(f"{qid}: missing keys {sorted(missing)}")
            continue
        if q["trap"] not in CATEGORIES:
            errors.append(f"{qid}: unknown trap category {q['trap']!r}")
        if q["trap"] == "control" and q["trap_sql"] is not None:
            errors.append(f"{qid}: control question must have trap_sql = null")
        if q["trap"] != "control" and not q["trap_sql"]:
            errors.append(f"{qid}: trap question needs trap_sql")
        pairs[q["pair_id"]].append(q)
    for pid, members in pairs.items():
        kinds = sorted("control" if m["trap"] == "control" else "trap" for m in members)
        if kinds != ["control", "trap"]:
            errors.append(f"pair {pid}: expected one trap + one control, got {[m['question_id'] for m in members]}")
    return errors


def validate_question(q: dict, engines: list) -> dict:
    out = {"id": q["question_id"], "trap": q["trap"], "failures": [], "gold": {}, "trap_diff": {}}
    ordered = has_top_level_order_by(q["SQL"])
    gold = {}
    for eng in engines:
        try:
            rows = eng.run(q["SQL"])
        except QueryError as e:
            out["failures"].append(f"gold fails on {eng.name}: {e}")
            out["gold"][eng.name] = "ERROR"
            continue
        gold[eng.name] = rows
        out["gold"][eng.name] = describe(rows)
        if not rows or all(c is None for r in rows for c in r):
            out["failures"].append(f"gold returns an empty/NULL result on {eng.name}")
    if len(gold) == 2 and not results_equal(gold["sqlite"], gold["postgres"], ordered):
        out["failures"].append(f"gold differs across engines: sqlite={describe(gold['sqlite'])} "
                               f"postgres={describe(gold['postgres'])}")
    if len(engines) < 2:
        out["cross_engine"] = "-"
    else:
        out["cross_engine"] = "ok" if len(gold) == 2 and not any("across" in f for f in out["failures"]) else "FAIL"

    if q["trap"] != "control":
        trap_ordered = ordered or has_top_level_order_by(q["trap_sql"])
        for eng in engines:
            if eng.name not in gold:
                continue
            try:
                rows = eng.run(q["trap_sql"])
            except QueryError as e:
                out["trap_diff"][eng.name] = (True, describe(e))
                continue
            same = results_equal(rows, gold[eng.name], trap_ordered)
            out["trap_diff"][eng.name] = (not same, describe(rows))
        if not any(d for d, _ in out["trap_diff"].values()):
            out["failures"].append("trap_sql returns the gold result on every engine")
    return out


def trap_cell(res: dict) -> str:
    if res["trap"] == "control":
        return "-"
    diff = [e for e, (d, _) in res["trap_diff"].items() if d]
    if not diff:
        return "NONE"
    return "both" if len(diff) == 2 else f"{diff[0]} only"


def main() -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    p = argparse.ArgumentParser(description="Validate benchmark questions against SQLite and PostgreSQL.")
    p.add_argument("files", nargs="*", type=Path, help="question files (default: questions/*.json)")
    p.add_argument("--engines", nargs="+", choices=["sqlite", "postgres"], default=["sqlite", "postgres"],
                   help="--engines sqlite checks without Postgres (no cross-engine check)")
    args = p.parse_args()
    files = args.files or sorted((ROOT / "questions").glob("*.json"))
    engine_classes = [{"sqlite": SQLiteEngine, "postgres": PostgresEngine}[e] for e in args.engines]
    all_results, structure_errors = [], []
    for path in files:
        questions = json.loads(path.read_text(encoding="utf-8"))
        structure_errors += [f"{path.name}: {e}" for e in check_structure(questions)]
        by_db = defaultdict(list)
        for q in questions:
            by_db[q["db_id"]].append(q)
        for db_id, qs in by_db.items():
            engines = [cls(db_id) for cls in engine_classes]
            all_results += [validate_question(q, engines) for q in qs]

    header = f"{'question_id':<18} {'trap':<13} {'gold (sqlite)':<22} {'gold (postgres)':<22} {'engines':<8} {'trap differs':<13} result"
    print(header)
    print("-" * len(header))
    for r in all_results:
        print(f"{r['id']:<18} {r['trap']:<13} {r['gold'].get('sqlite', '-')[:22]:<22} "
              f"{r['gold'].get('postgres', '-')[:22]:<22} {r['cross_engine']:<8} {trap_cell(r):<13} "
              f"{'FAIL' if r['failures'] else 'ok'}")

    print("\ntrap results per engine (trap_sql output vs gold):")
    for r in all_results:
        if r["trap"] == "control":
            continue
        parts = [f"{e}: {'DIFFERS' if d else 'same'} ({desc})" for e, (d, desc) in r["trap_diff"].items()]
        print(f"  {r['id']} [{r['trap']}] " + " | ".join(parts))

    failures = [(r["id"], f) for r in all_results for f in r["failures"]]
    n_trap = sum(r["trap"] != "control" for r in all_results)
    by_engine = defaultdict(int)
    for r in all_results:
        by_engine[trap_cell(r)] += r["trap"] != "control"
    print(f"\n{len(all_results)} questions ({n_trap} trap, {len(all_results) - n_trap} control); "
          f"trap differs on: both={by_engine['both']}, sqlite only={by_engine['sqlite only']}, "
          f"postgres only={by_engine['postgres only']}, none={by_engine['NONE']}")
    if structure_errors or failures:
        print(f"\nFAILED: {len(structure_errors)} structure error(s), {len(failures)} question failure(s)")
        for e in structure_errors:
            print(f"  [structure] {e}")
        for qid, f in failures:
            print(f"  [{qid}] {f}")
        return 1
    print("\nall checks passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
