"""Evaluate a model on the benchmark: generate SQL, run it, compare with gold.

Talks to any OpenAI-compatible chat-completions endpoint (NVIDIA NIM, a local
NIM container, LM Studio, Ollama, OpenRouter, vLLM, ...) using only the
standard library. Every question is asked once per engine (SQLite and
PostgreSQL), with the engine named in the prompt.

    set NVIDIA_API_KEY=nvapi-...
    python scripts/evaluate.py --provider nim --model qwen/qwen2.5-coder-32b-instruct
    python scripts/evaluate.py --provider lmstudio --model qwen2.5-coder-14b-instruct
    python scripts/evaluate.py --provider ollama --model qwen2.5-coder:14b
    python scripts/evaluate.py --base-url https://host/v1 --api-key-env MY_KEY --model org/model
    python scripts/evaluate.py --provider nim --model x --show-prompt     # print one prompt, exit

Model answers are cached in results/<run>/responses.jsonl, so an interrupted
run resumes where it stopped and re-running never re-queries a finished item.
Scoring is recomputed from the cache on every run and written to
results/<run>/{scores.json, details.jsonl, report.md}.

Metrics, per engine:
  EX       model result == gold result (numbers to 2 decimals; row order only
           matters when the gold SQL has a top-level ORDER BY)
  relaxed  EX, but extra output columns are allowed (each gold column must
           appear among the model's columns)
  pairs    control right + trap wrong = the model fell for the trap
  trap_sql reproduced: the model's result equals the anticipated wrong result
"""
from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import re
import sqlite3
import sys
import threading
import time
import urllib.error
import urllib.request
from collections import Counter, defaultdict
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

import psycopg

sys.path.insert(0, str(Path(__file__).resolve().parent))
from load_postgres import conninfo  # noqa: E402
from validate import (QueryError, cells_equal, describe, has_top_level_order_by,  # noqa: E402
                      norm_cell, results_equal, sort_key)

ROOT = Path(__file__).resolve().parent.parent
PROMPT_VERSION = 2  # bump when the prompt template changes; invalidates cached answers

# name: (base_url, api-key env var, default requests/minute)
PROVIDERS = {
    "nim": ("https://integrate.api.nvidia.com/v1", "NVIDIA_API_KEY", 35),
    "nim-local": ("http://localhost:8000/v1", None, None),
    "lmstudio": ("http://localhost:1234/v1", None, None),
    "ollama": ("http://localhost:11434/v1", None, None),
    "openrouter": ("https://openrouter.ai/api/v1", "OPENROUTER_API_KEY", None),
}

DIALECTS = {"sqlite": "SQLite 3", "postgres": "PostgreSQL 16"}

PROMPT = """\
You are an expert SQL developer. Write one {dialect} query that answers the question about the database below.

Database schema{rows_note}:

{schema}

{notes}{evidence}Question: {question}

Answer with the SQL query only, in a single ```sql code block."""

MAX_ROWS = 10_000        # larger results are scored as errors (no gold result is that big)
QUERY_TIMEOUT_S = 30
RETRY_STATUS = {408, 409, 425, 429, 500, 502, 503, 504}


# ---------------------------------------------------------------- prompt

def strip_sql_comments(text: str) -> str:
    text = re.sub(r"/\*.*?\*/", "", text, flags=re.S)
    lines = [re.sub(r"--.*$", "", ln).rstrip() for ln in text.splitlines()]
    return "\n".join(ln for ln in lines if ln.strip())


def load_notes(db_id: str) -> dict:
    path = ROOT / "databases" / db_id / "prompt_notes.json"
    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}


def example_rows(con: sqlite3.Connection, table: str, n: int, conditions: list[str]) -> tuple[list, list]:
    """The first matching row for each condition, topped up with the first rows by id."""
    cols = [d[0] for d in con.execute(f'SELECT * FROM "{table}" LIMIT 0').description]
    picked = {}
    for cond in conditions:
        for r in con.execute(f'SELECT * FROM "{table}" WHERE {cond} ORDER BY 1 LIMIT 1'):
            picked[r[0]] = r
    for r in con.execute(f'SELECT * FROM "{table}" ORDER BY 1 LIMIT {n}'):
        if len(picked) >= n:
            break
        picked.setdefault(r[0], r)
    return cols, [picked[k] for k in sorted(picked)]


def schema_text(db_id: str, n_rows: int) -> str:
    """CREATE TABLE statements without comments, each followed by example rows."""
    db_dir = ROOT / "databases" / db_id
    ddl = strip_sql_comments((db_dir / "schema.sql").read_text(encoding="utf-8"))
    wanted = load_notes(db_id).get("example_rows", {})
    con = sqlite3.connect(f"file:{(db_dir / f'{db_id}.sqlite').as_posix()}?mode=ro", uri=True)
    parts = []
    for stmt in ddl.split(";"):
        stmt = stmt.strip()
        if not stmt.upper().startswith("CREATE TABLE"):
            continue
        block = stmt + ";"
        if n_rows:
            table = stmt.split()[2]
            cols, rows = example_rows(con, table, n_rows, wanted.get(table, []))
            lines = [f"{len(rows)} example rows:", " | ".join(cols)]
            lines += [" | ".join("NULL" if v is None else str(v) for v in r) for r in rows]
            block += "\n/*\n" + "\n".join(lines) + "\n*/"
        parts.append(block)
    con.close()
    return "\n\n".join(parts)


def notes_text(db_id: str) -> str:
    notes = load_notes(db_id).get("notes", [])
    return "Data notes:\n" + "\n".join(f"- {n}" for n in notes) + "\n\n" if notes else ""


def build_prompt(q: dict, engine: str, schema: str, notes: str, n_rows: int) -> str:
    return PROMPT.format(
        dialect=DIALECTS[engine],
        rows_note=f", with {n_rows} example rows per table" if n_rows else "",
        schema=schema,
        notes=notes,
        evidence=f"Evidence: {q['evidence']}\n" if q["evidence"] else "",
        question=q["question"],
    )


# ---------------------------------------------------------------- model client

class FatalAPIError(Exception):
    pass


class RateLimiter:
    def __init__(self, rpm: float | None):
        self.interval = 60.0 / rpm if rpm else 0.0
        self.lock = threading.Lock()
        self.next_at = 0.0

    def wait(self) -> None:
        if not self.interval:
            return
        with self.lock:
            now = time.monotonic()
            start = max(now, self.next_at)
            self.next_at = start + self.interval
        time.sleep(max(0.0, start - now))


class ChatClient:
    def __init__(self, base_url: str, api_key: str | None, rpm: float | None,
                 timeout: float, max_retries: int):
        self.url = base_url.rstrip("/") + "/chat/completions"
        self.headers = {"Content-Type": "application/json", "User-Agent": "turkish-sql-bench/1"}
        if api_key:
            self.headers["Authorization"] = f"Bearer {api_key}"
        self.limiter = RateLimiter(rpm)
        self.timeout = timeout
        self.max_retries = max_retries

    def complete(self, body: dict) -> dict:
        data = json.dumps(body).encode("utf-8")
        for attempt in range(self.max_retries + 1):
            self.limiter.wait()
            started = time.monotonic()
            req = urllib.request.Request(self.url, data=data, headers=self.headers, method="POST")
            try:
                with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                    payload = json.load(resp)
                return parse_completion(payload, time.monotonic() - started)
            except urllib.error.HTTPError as e:
                detail = e.read().decode("utf-8", "replace")[:500]
                if e.code not in RETRY_STATUS:
                    raise FatalAPIError(f"HTTP {e.code} from {self.url}: {detail}") from None
                retry_after = e.headers.get("Retry-After", "")
                wait = float(retry_after) if retry_after.replace(".", "", 1).isdigit() else 2.0 * 2 ** attempt
                reason = f"HTTP {e.code}"
            except urllib.error.URLError as e:
                if isinstance(e.reason, ConnectionRefusedError):
                    raise FatalAPIError(f"cannot connect to {self.url}; is the server running?") from None
                wait, reason = 2.0 * 2 ** attempt, str(e.reason)
            except (TimeoutError, ConnectionError) as e:
                wait, reason = 2.0 * 2 ** attempt, type(e).__name__
            if attempt == self.max_retries:
                raise RuntimeError(f"gave up after {attempt + 1} attempts ({reason})")
            wait = min(wait, 120.0)
            print(f"  ... {reason} after {time.monotonic() - started:.0f}s, retry {attempt + 1}/{self.max_retries} "
                  f"in {wait:.0f}s")
            time.sleep(wait)
        raise AssertionError("unreachable")


def parse_completion(payload: dict, latency: float) -> dict:
    choice = (payload.get("choices") or [{}])[0]
    msg = choice.get("message") or {}
    return {
        "content": msg.get("content"),
        "reasoning": msg.get("reasoning_content") or msg.get("reasoning"),
        "finish_reason": choice.get("finish_reason"),
        "usage": payload.get("usage"),
        "latency_s": round(latency, 2),
    }


def extract_sql(text: str | None) -> str | None:
    """SQL from a model answer: the last ```sql block, else from the first SELECT/WITH."""
    if not text:
        return None
    if "</think>" in text:
        text = text.rsplit("</think>", 1)[1]
    if "<think>" in text:  # reasoning never closed: the answer was cut off
        return None
    blocks = re.findall(r"```[ \t]*(?:sql|sqlite|postgresql|postgres)?[ \t]*\n?(.*?)```", text, flags=re.S | re.I)
    if blocks:
        sql = blocks[-1]
    else:
        m = re.search(r"\b(WITH|SELECT)\b.*", text, flags=re.S | re.I)
        if not m:
            return None
        sql = m.group(0)
    sql = sql.strip().rstrip(";").strip()
    return sql or None


# ---------------------------------------------------------------- running model SQL

def has_multiple_statements(sql: str) -> bool:
    i, n = 0, len(sql)
    while i < n:
        ch = sql[i]
        if ch in "'\"":
            j = sql.find(ch, i + 1)
            while j != -1 and j + 1 < n and sql[j + 1] == ch:
                j = sql.find(ch, j + 2)
            if j == -1:
                return False
            i = j + 1
            continue
        if sql.startswith("--", i):
            j = sql.find("\n", i)
            i = n if j == -1 else j
            continue
        if sql.startswith("/*", i):
            j = sql.find("*/", i + 2)
            i = n if j == -1 else j + 2
            continue
        if ch == ";":
            return bool(strip_sql_comments(sql[i + 1:]).strip())
        i += 1
    return False


def finish_rows(rows: list) -> list[tuple]:
    if len(rows) > MAX_ROWS:
        raise QueryError(f"result has more than {MAX_ROWS} rows")
    return [tuple(norm_cell(c) for c in r) for r in rows]


class SQLiteRunner:
    """Read-only connection with a time limit."""
    name = "sqlite"

    def __init__(self, db_id: str):
        path = ROOT / "databases" / db_id / f"{db_id}.sqlite"
        if not path.exists():
            raise SystemExit(f"missing {path}; run databases/{db_id}/generate.py")
        self.con = sqlite3.connect(f"file:{path.as_posix()}?mode=ro", uri=True)

    def run(self, sql: str) -> list[tuple]:
        deadline = time.monotonic() + QUERY_TIMEOUT_S
        self.con.set_progress_handler(lambda: time.monotonic() > deadline, 10_000)
        try:
            rows = self.con.execute(sql).fetchmany(MAX_ROWS + 1)
        except sqlite3.Error as e:
            raise QueryError(str(e)) from None
        finally:
            self.con.set_progress_handler(None, 0)
        return finish_rows(rows)


class PostgresRunner:
    """Read-only session, one statement per query, rolled back afterwards."""
    name = "postgres"

    def __init__(self, db_id: str):
        try:
            self.con = psycopg.connect(conninfo(db_id))
        except psycopg.OperationalError as e:
            raise SystemExit(f"cannot connect to Postgres database {db_id!r}: {e}\n"
                             "start it with `docker compose up -d` and run scripts/load_postgres.py, "
                             "or pass --engines sqlite") from None
        self.con.execute("SET default_transaction_read_only = on")
        self.con.execute(f"SET statement_timeout = '{QUERY_TIMEOUT_S}s'")
        self.con.commit()

    def run(self, sql: str) -> list[tuple]:
        if has_multiple_statements(sql):
            raise QueryError("multiple statements are not allowed")
        try:
            with self.con.cursor() as cur:
                cur.execute(sql)
                if cur.description is None:
                    raise QueryError("statement returned no result set")
                rows = cur.fetchmany(MAX_ROWS + 1)
        except psycopg.Error as e:
            raise QueryError(str(e)) from None
        finally:
            self.con.rollback()
        return finish_rows(rows)


def execute(runner, sql: str) -> list[tuple] | QueryError:
    try:
        return runner.run(sql)
    except QueryError as e:
        return e


# ---------------------------------------------------------------- scoring

def relaxed_equal(pred: list[tuple], gold: list[tuple], ordered: bool) -> bool:
    """Like results_equal, but the prediction may have extra columns."""
    if results_equal(pred, gold, ordered):
        return True
    if not pred or len(pred) != len(gold) or len(pred[0]) <= len(gold[0]):
        return False

    def same_column(a, b):
        if not ordered:
            a, b = sorted(a, key=lambda c: sort_key((c,))), sorted(b, key=lambda c: sort_key((c,)))
        return all(cells_equal(x, y) for x, y in zip(a, b))

    pred_cols, gold_cols = list(zip(*pred)), list(zip(*gold))
    return all(any(same_column(p, g) for p in pred_cols) for g in gold_cols)


def error_line(e: QueryError) -> str:
    return str(e).splitlines()[0] if str(e) else ""


def score_item(q: dict, engine: str, runner, response: dict | None, gold, trap) -> dict:
    item = {"question_id": q["question_id"], "engine": engine, "trap": q["trap"], "pair_id": q["pair_id"],
            "question": q["question"], "status": "missing", "correct": False, "relaxed": False,
            "trap_hit": None, "pred_sql": None, "pred_result": None, "gold_result": describe(gold)}
    if response is None:
        return item
    item.update(finish_reason=response.get("finish_reason"), latency_s=response.get("latency_s"),
                usage=response.get("usage"))
    sql = extract_sql(response.get("content"))
    item["pred_sql"] = sql
    if sql is None:
        item["status"] = "no_sql"
        return item
    pred = execute(runner, sql)
    item["pred_result"] = describe(pred)
    ordered = has_top_level_order_by(q["SQL"])
    if isinstance(pred, QueryError):
        item["status"] = "error"
    else:
        item["correct"] = results_equal(pred, gold, ordered)
        item["relaxed"] = relaxed_equal(pred, gold, ordered)
        item["status"] = "correct" if item["correct"] else "wrong"
    if trap is not None:  # trap_sql differs from gold on this engine
        if isinstance(trap, QueryError):
            item["trap_hit"] = isinstance(pred, QueryError) and error_line(pred) == error_line(trap)
        else:
            item["trap_hit"] = not isinstance(pred, QueryError) and results_equal(
                pred, trap, ordered or has_top_level_order_by(q["trap_sql"]))
    return item


def summarize(items: list[dict], engines: list[str]) -> dict:
    out = {}
    for eng in engines:
        rows = [i for i in items if i["engine"] == eng]
        cats = defaultdict(lambda: [0, 0])
        for i in rows:
            cats[i["trap"]][0] += i["correct"]
            cats[i["trap"]][1] += 1
        traps = [i for i in rows if i["trap"] != "control"]
        controls = [i for i in rows if i["trap"] == "control"]
        by_pair = defaultdict(dict)
        for i in rows:
            by_pair[i["pair_id"]]["control" if i["trap"] == "control" else "trap"] = i["correct"]
        pairs = Counter()
        for p in by_pair.values():
            if len(p) == 2:
                pairs[{(True, True): "both_correct", (True, False): "fell_for_trap",
                       (False, True): "control_only_wrong", (False, False): "both_wrong"}[(p["control"], p["trap"])]] += 1
        hits = [i["trap_hit"] for i in rows if i["trap_hit"] is not None]
        lat = [i["latency_s"] for i in rows if i.get("latency_s") is not None]
        out[eng] = {
            "n": len(rows),
            "ex": sum(i["correct"] for i in rows),
            "relaxed": sum(i["relaxed"] for i in rows),
            "trap": [sum(i["correct"] for i in traps), len(traps)],
            "control": [sum(i["correct"] for i in controls), len(controls)],
            "categories": {k: v for k, v in sorted(cats.items())},
            "pairs": dict(pairs),
            "trap_sql_reproduced": [sum(hits), len(hits)],
            "status": dict(Counter(i["status"] for i in rows)),
            "mean_latency_s": round(sum(lat) / len(lat), 2) if lat else None,
        }
    return out


def pct(a: int, b: int) -> str:
    return f"{a}/{b} ({100 * a / b:.0f}%)" if b else "-"


def render_report(meta: dict, summary: dict, items: list[dict]) -> str:
    engines = list(summary)
    lines = [f"# {meta['run']}", "",
             f"- model: `{meta['model']}` via {meta['provider']} ({meta['base_url']})",
             f"- questions: {', '.join(meta['question_files'])}; prompt v{meta['prompt_version']}, "
             f"{meta['sample_rows']} example rows, temperature {meta['temperature']}, max_tokens {meta['max_tokens']}",
             f"- scored: {meta['scored_at']}", ""]
    head = "| metric | " + " | ".join(engines) + " |"
    lines += [head, "|---" * (len(engines) + 1) + "|"]

    def row(label, fn):
        lines.append(f"| {label} | " + " | ".join(fn(summary[e]) for e in engines) + " |")

    row("**EX (all)**", lambda s: pct(s["ex"], s["n"]))
    row("EX relaxed (extra columns ok)", lambda s: pct(s["relaxed"], s["n"]))
    row("control questions", lambda s: pct(*s["control"]))
    row("trap questions", lambda s: pct(*s["trap"]))
    cats = sorted({c for s in summary.values() for c in s["categories"]} - {"control"})
    for c in cats:
        row(f"&nbsp;&nbsp;{c}", lambda s, c=c: pct(*s["categories"].get(c, [0, 0])))
    row("pairs: both correct", lambda s: str(s["pairs"].get("both_correct", 0)))
    row("pairs: **fell for trap** (control ok, trap wrong)", lambda s: str(s["pairs"].get("fell_for_trap", 0)))
    row("pairs: control wrong, trap ok", lambda s: str(s["pairs"].get("control_only_wrong", 0)))
    row("pairs: both wrong", lambda s: str(s["pairs"].get("both_wrong", 0)))
    row("trap_sql result reproduced", lambda s: pct(*s["trap_sql_reproduced"]))
    row("status", lambda s: ", ".join(f"{k} {v}" for k, v in sorted(s["status"].items())))
    row("mean latency (s)", lambda s: str(s["mean_latency_s"]))

    wrong = [i for i in items if not i["correct"]]
    lines += ["", f"## Not correct ({len(wrong)})", ""]
    for i in wrong:
        tag = " — matches trap_sql" if i["trap_hit"] else ""
        lines += [f"**{i['question_id']}** [{i['trap']}] {i['engine']}: {i['status']}{tag}",
                  f"> {i['question']}", "",
                  f"gold: {i['gold_result']} · model: {i['pred_result']}", ""]
        if i["pred_sql"]:
            lines += ["```sql", i["pred_sql"], "```", ""]
    return "\n".join(lines) + "\n"


# ---------------------------------------------------------------- main

def slug(text: str) -> str:
    return re.sub(r"[^A-Za-z0-9._-]+", "_", text).strip("_")


def load_cache(path: Path) -> dict:
    cache = {}
    if path.exists():
        for line in path.read_text(encoding="utf-8").splitlines():
            if line.strip():
                rec = json.loads(line)
                cache[rec["key"]] = rec
    return cache


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    p.add_argument("--provider", choices=sorted(PROVIDERS), help="preset for --base-url / API key / rate limit")
    p.add_argument("--base-url", help="OpenAI-compatible base URL (overrides the preset)")
    p.add_argument("--api-key-env", help="environment variable holding the API key (overrides the preset)")
    p.add_argument("--model", required=True)
    p.add_argument("--run-name", help="results/<run-name>; default <provider>__<model>")
    p.add_argument("--questions", nargs="+", type=Path, help="question files (default: questions/*.json)")
    p.add_argument("--ids", nargs="+", help="only these question_ids")
    p.add_argument("--limit", type=int, help="only the first N questions (smoke test)")
    p.add_argument("--engines", nargs="+", choices=list(DIALECTS), default=list(DIALECTS))
    p.add_argument("--sample-rows", type=int, default=3, help="example rows per table in the prompt (0 = none)")
    p.add_argument("--temperature", type=float, default=0.0)
    p.add_argument("--max-tokens", type=int, default=2048, help="raise for reasoning models")
    p.add_argument("--extra-body", default="{}", help='JSON merged into the request, e.g. \'{"top_p": 1}\'')
    p.add_argument("--rpm", type=float, help="max requests per minute (nim preset: 35)")
    p.add_argument("--workers", type=int, default=1, help="parallel requests")
    p.add_argument("--timeout", type=float, default=180, help="seconds per request")
    p.add_argument("--max-retries", type=int, default=4)
    p.add_argument("--show-prompt", action="store_true", help="print the first prompt and exit")
    return p.parse_args()


def main() -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", line_buffering=True)
    args = parse_args()
    if not args.provider and not args.base_url:
        raise SystemExit("give --provider or --base-url")
    base_url, key_env, rpm = PROVIDERS.get(args.provider, (None, None, None))
    base_url = args.base_url or base_url
    key_env = args.api_key_env or key_env
    rpm = args.rpm if args.rpm is not None else rpm
    provider = args.provider or "custom"
    extra = json.loads(args.extra_body)

    files = args.questions or sorted((ROOT / "questions").glob("*.json"))
    questions = [q for f in files for q in json.loads(Path(f).read_text(encoding="utf-8"))]
    if args.ids:
        questions = [q for q in questions if q["question_id"] in set(args.ids)]
    if args.limit:
        questions = questions[:args.limit]
    if not questions:
        raise SystemExit("no questions selected")

    schemas = {db: schema_text(db, args.sample_rows) for db in sorted({q["db_id"] for q in questions})}
    notes = {db: notes_text(db) for db in schemas}
    tasks = []  # (question, engine, key, body)
    for q in questions:
        for eng in args.engines:
            prompt = build_prompt(q, eng, schemas[q["db_id"]], notes[q["db_id"]], args.sample_rows)
            body = {"model": args.model, "messages": [{"role": "user", "content": prompt}],
                    "temperature": args.temperature, "max_tokens": args.max_tokens, **extra}
            key = hashlib.sha256(json.dumps([PROMPT_VERSION, body], sort_keys=True,
                                            ensure_ascii=False).encode("utf-8")).hexdigest()[:24]
            tasks.append((q, eng, key, body))

    if args.show_prompt:
        print(tasks[0][3]["messages"][0]["content"])
        return 0

    api_key = None
    if key_env:
        import os
        api_key = os.environ.get(key_env)
        if not api_key:
            raise SystemExit(f"set the {key_env} environment variable (API key for {base_url})")

    run = args.run_name or slug(f"{provider}__{args.model}")
    run_dir = ROOT / "results" / run
    run_dir.mkdir(parents=True, exist_ok=True)
    cache_path = run_dir / "responses.jsonl"
    cache = load_cache(cache_path)

    # ---- generation
    todo = [t for t in tasks if t[2] not in cache]
    print(f"run {run}: {len(tasks)} prompts, {len(tasks) - len(todo)} cached, {len(todo)} to query "
          f"({base_url}, model {args.model})")
    fatal = None
    if todo:
        client = ChatClient(base_url, api_key, rpm, args.timeout, args.max_retries)
        lock = threading.Lock()
        done = 0
        with ThreadPoolExecutor(max_workers=max(1, args.workers)) as pool, \
                cache_path.open("a", encoding="utf-8") as fh:
            futures = {pool.submit(client.complete, body): (q, eng, key) for q, eng, key, body in todo}
            for fut in as_completed(futures):
                q, eng, key = futures[fut]
                done += 1
                try:
                    resp = fut.result()
                except FatalAPIError as e:
                    fatal = str(e)
                    pool.shutdown(wait=False, cancel_futures=True)
                    break
                except Exception as e:  # transient failure after retries: stays missing
                    print(f"  [{done}/{len(todo)}] {q['question_id']} {eng}: FAILED ({e})")
                    continue
                rec = {"key": key, "question_id": q["question_id"], "engine": eng, "model": args.model,
                       "response": resp}
                with lock:
                    fh.write(json.dumps(rec, ensure_ascii=False) + "\n")
                    fh.flush()
                    cache[key] = rec
                print(f"  [{done}/{len(todo)}] {q['question_id']} {eng}: {resp['latency_s']}s "
                      f"{'sql' if extract_sql(resp['content']) else 'NO SQL'} ({resp['finish_reason']})")
    if fatal:
        print(f"\nstopped: {fatal}")

    # ---- scoring
    runners = {"sqlite": SQLiteRunner, "postgres": PostgresRunner}
    engines_by_db = {db: {e: runners[e](db) for e in args.engines} for db in schemas}
    items = []
    for q, eng, key, _ in tasks:
        runner = engines_by_db[q["db_id"]][eng]
        gold = runner.run(q["SQL"])  # validated by scripts/validate.py
        trap = None
        if q["trap"] != "control":
            t = execute(runner, q["trap_sql"])
            ordered = has_top_level_order_by(q["SQL"]) or has_top_level_order_by(q["trap_sql"])
            if isinstance(t, QueryError) or not results_equal(t, gold, ordered):
                trap = t
        rec = cache.get(key)
        items.append(score_item(q, eng, runner, rec["response"] if rec else None, gold, trap))

    meta = {"run": run, "provider": provider, "base_url": base_url, "model": args.model,
            "question_files": [Path(f).name for f in files], "n_questions": len(questions),
            "engines": args.engines, "prompt_version": PROMPT_VERSION, "sample_rows": args.sample_rows,
            "temperature": args.temperature, "max_tokens": args.max_tokens, "extra_body": extra,
            "scored_at": dt.datetime.now().isoformat(timespec="seconds")}
    summary = summarize(items, args.engines)
    missing = sum(i["status"] == "missing" for i in items)
    (run_dir / "scores.json").write_text(json.dumps({"meta": meta, "summary": summary, "missing": missing},
                                                    ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    (run_dir / "details.jsonl").write_text("".join(json.dumps(i, ensure_ascii=False) + "\n" for i in items),
                                           encoding="utf-8")
    report = render_report(meta, summary, items)
    (run_dir / "report.md").write_text(report, encoding="utf-8")
    for eng in args.engines:
        q0 = next(t for t in tasks if t[1] == eng)
        (run_dir / f"prompt_example_{eng}.txt").write_text(q0[3]["messages"][0]["content"] + "\n", encoding="utf-8")

    print("\n" + report.split("\n## ")[0].strip())
    print(f"\nwritten to {run_dir.relative_to(ROOT)}/ (report.md, details.jsonl, scores.json)")
    if missing:
        print(f"INCOMPLETE: {missing} item(s) without a model answer; re-run the same command to fill them")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
