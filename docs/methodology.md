# Methodology

Question format, fairness rules, the validator and the evaluation harness. Back to the [README](../README.md).

## Layout

```
common/il_ilce.json                    81 il / 973 ilçe (Wikipedia, CC BY-SA 4.0)
databases/belediye_su/schema.sql       portable DDL (SQLite + Postgres)
databases/belediye_su/generate.py      deterministic generator
databases/belediye_su/prompt_notes.json  data notes + example-row choice for model prompts
databases/belediye_su/belediye_su.sqlite   build output
databases/ik_bordro/...                same four files for the HR / payroll database
databases/eticaret/...                 same four files for the online shop database
questions/belediye_su.json             100 questions (50 trap + 50 control)
questions/ik_bordro.json               100 questions (50 trap + 50 control)
questions/eticaret.json                100 questions (50 trap + 50 control)
scripts/build_il_ilce.py               refreshes common/il_ilce.json from Wikipedia
scripts/load_postgres.py               copies the built SQLite DBs into Postgres and checks the copy
scripts/validate.py                    checks every question on both engines
scripts/evaluate.py                    runs a model on the questions, scores it on both engines
scripts/leaderboard.py                 compares evaluation runs
results/<run>/                         evaluation output (one folder per model run)
tests/test_sqlite_lower.py             proves LOWER('IĞDIR') != 'ığdır' in SQLite
tests/test_scoring.py                  answer parsing, result comparison, pairs, leaderboard
tests/test_databases.py                money storage in SQLite, eticaret data formats, Postgres copy check
docker-compose.yml                     Postgres 16
docs/                                  databases.md, methodology.md (this file)
LICENSE, LICENSE-DATA                  MIT for code, CC BY-SA 4.0 for data
```

## Questions

`questions/<db_id>.json` uses BIRD-style records:

```json
{"question_id": "belediye_su_001", "db_id": "belediye_su", "question": "...",
 "evidence": "", "SQL": "<gold>", "trap": "case_i", "trap_sql": "<plausible wrong query>",
 "pair_id": "belediye_su_p01", "status": "v0.1"}
```

- Each database has 50 pairs: 10 per trap category, each with a control twin.
  Controls have `trap_sql: null`.
- `evidence` holds only facts the database can't provide (the extract date,
  how seniority is counted). It deliberately doesn't reveal the trap.
- Fairness rules, learned from model runs:
  - a trap value must have one stored form a model can infer. Streets are
    stored as both `Gül Sokak` and `Gül Sokağı` (different streets), so no
    question depends on a street suffix;
  - the control must carry the same Turkish letters as its trap. An extra
    İ-name in only one twin (`İzmir’in Kemalpaşa…`) turns a pair into a
    case test;
  - wording must be unambiguous: subscriber counts say "aktif", "KDV hariç"
    names exactly which amounts are summed;
  - a control carries no trap of its own. Outside `case_i`, neither twin
    depends on the ALL CAPS legacy rows: the pair is narrowed to rows that
    have none (closed accounts, hires since 2019, …), so the gold result is
    the same with or without the upper-case spellings. `case_i` controls do
    need the legacy rows, but use ASCII-only names (`Bursa`, `Uzman`)
    that any case-folding handles, so the Turkish letter is the only
    difference between the twins;
  - controls write numbers plainly (`45 bin TL`, `40005 TL`), never in
    Turkish thousands or decimal format; that format is a `tr_format` trap
    (`1.500 TL’yi aşan`).
- Rules for gold SQL, so it runs on both engines:
  - no `LOWER`/`UPPER`/`LIKE` on letters
  - no `ORDER BY` on text (the collations differ)
  - ISO dates compared only with whole dates (`BETWEEN '2024-11-01' AND
    '2024-11-30'`, `= '2024-11-29'`), and cast to text before `SUBSTR`
  - date arithmetic has no form both engines accept, so such a question adds
    `SQL_postgres` (and `trap_sql_postgres` for a trap), which `validate.py`
    and `evaluate.py` use on Postgres instead of `SQL` / `trap_sql`
    (`julianday(…) + 14` on SQLite, `TO_DATE(…, 'DD.MM.YYYY') + 14` on
    Postgres)
  - numeric results wrapped in `ROUND(…, 2)`
  - divide by a decimal literal (`/ 100.0`) whenever the dividend can be an
    integer column (`su_kdv_orani / 100` is 0 on both engines); money columns
    are `REAL` in SQLite, so they never divide as integers
  - `ORDER BY` with a unique key whenever row order matters

### Known weak spots in v0.1

A read-only review before the freeze found these. They are kept, and listed so results can be
read with them in mind:

- **ID order.** Row IDs follow date order, so `ik_bordro_083` (first payslip) and
  `belediye_su_081` (first reading) can be answered by sorting on the ID instead of parsing
  the Turkish date.
- **Weak traps.** `belediye_su_025` and `ik_bordro_025` (`'RESMİ'`/`'ÜCRETSİZ'` instead of
  the ASCII value) are easy now that the stored values appear in the example rows.
  `ik_bordro_075` (`sube_id` for a branch code), `belediye_su_073` and `belediye_su_075` encode
  mistakes few models make. `ik_bordro_091` asks for the minimum wage, which one example row
  shows.
- **Untested conditions.** In `belediye_su_069`/`070` and `092`, dropping the `aktif` filter
  gives the same count. No employee in `ik_bordro_099`/`100` sits exactly on the 3-year notice
  boundary.
- **Readings.** `ik_bordro_061` ("muhasebe uzmanlarımız") can also be read as everyone with
  the title `Uzman` in the Muhasebe department. `ik_bordro_087`/`088` compare the prorated
  `brut_ucret`, not the contract salary.

## Validator

`scripts/validate.py` checks, for each question and each engine:
1. Gold SQL runs and returns a non-empty, not-all-NULL result.
2. Gold results match across engines. Numbers are compared with ±0.011
   tolerance; non-integer values may also differ by 0.002% of their size,
   which absorbs rounding each invoice before summing instead of rounding the
   sum (up to 4.15 TL on a 248,870 TL total). Whole numbers such as counts must
   match exactly. Row order is ignored unless the query has a top-level
   `ORDER BY`.
3. For trap questions, `trap_sql` differs from gold on at least one engine.
   An error counts as a difference. The table shows `both`, `sqlite only` or
   `postgres only`.

It also checks the file structure: required keys, known categories, and
exactly one trap plus one control per `pair_id`. It prints a summary table and
exits 1 on any failure. `--engines sqlite` runs it without Postgres, skipping
the cross-engine check.

## Evaluation

`scripts/evaluate.py` sends every question to a model once per engine,
runs the returned SQL and compares it with the gold result. It speaks the
OpenAI-compatible chat API using only the standard library, so it works with:

| `--provider` | endpoint | API key |
|---|---|---|
| `nim` | NVIDIA NIM API, `https://integrate.api.nvidia.com/v1` (35 requests/min by default) | `NVIDIA_API_KEY` |
| `nim-local` | self-hosted NIM container, `http://localhost:8000/v1` | none |
| `lmstudio` | LM Studio server, `http://localhost:1234/v1` | none |
| `ollama` | Ollama, `http://localhost:11434/v1` | none |
| `openrouter` | OpenRouter, `https://openrouter.ai/api/v1` | `OPENROUTER_API_KEY` |
| *(none)* | any other provider: `--base-url URL --api-key-env VAR` | your variable |

```bash
set NVIDIA_API_KEY=nvapi-...                          # PowerShell: $env:NVIDIA_API_KEY="nvapi-..."
python scripts/evaluate.py --provider nim --model qwen/qwen2.5-coder-32b-instruct --limit 4   # smoke test
python scripts/evaluate.py --provider nim --model qwen/qwen2.5-coder-32b-instruct
python scripts/evaluate.py --provider lmstudio --model qwen2.5-coder-14b-instruct
python scripts/evaluate.py --provider nim --model x --show-prompt                # print one prompt
python scripts/evaluate.py --provider nim --model qwen/qwen2.5-coder-32b-instruct --rescore  # re-score the cache only
python scripts/leaderboard.py                                                     # compare runs
```

Model names are the provider's own IDs (NIM: see build.nvidia.com; LM Studio:
the identifier of the loaded model). Useful options: `--engines sqlite` (no
Postgres needed), `--ids`, `--workers N`, `--rpm N`, `--max-tokens` (raise it
for reasoning models), `--extra-body '{...}'` for model-specific request
fields, `--run-name`, `--no-think-prefill` (starts the reply with an empty
`<think></think>` block; Qwen3.5 in LM Studio ignores the usual reasoning-off
settings and otherwise thinks for ~1,000 tokens per question; such a model
may still reason in its visible answer, so the leaderboard marks the run),
`--rescore` (score the cached answers after a harness or data change, sending
no requests).

**Run folders.** One folder holds one combination of settings: re-running into
a folder that was scored with a different `--temperature`, `--max-tokens`,
`--sample-rows`, `--extra-body` or `--no-think-prefill` stops with an error
(use `--run-name` for a separate run). A smoke test (`--limit`, `--ids`) or any
subset of an already scored run writes `scores.partial.json`,
`details.partial.jsonl` and `report.partial.md`, so the full run's scores and
the leaderboard are left alone. Both share the answer cache.

**Prompt.** One user message (no system message, since some models reject
it), followed by the empty-think assistant message when `--no-think-prefill`
is set (`--show-prompt` and `prompt_example_<engine>.txt` show both): the engine name (SQLite 3 or PostgreSQL 16), the `CREATE TABLE`
statements from `schema.sql` with all comments removed (they would give the
traps away), 3 example rows per table, the data notes, the `evidence` when
non-empty, the question, and a request for one ```sql block. Temperature 0.
Bump `PROMPT_VERSION` in the script when changing the template.

Each database can have a `prompt_notes.json` next to its schema:
- `notes`: a short data dictionary of the legacy quirks (upper-case migrated
  addresses, text dates, Turkish-format text amounts). Without it a model has
  no way to know the upper-case rows exist, so trap and control fail alike and
  the pair measures nothing. The notes never hint at a trap.
- `example_rows`: per table, conditions whose first matching row must be among
  the example rows (e.g. one migrated upper-case address, one amount over
  1.000); the remaining rows are the first by id.

**Answer parsing.** An answer cut off by `max_tokens` (`finish_reason`
`length`) is scored *truncated*, never right: any query in it may be a draft.
Reasoning before `</think>` is dropped; an answer whose reasoning never closes
counts as no SQL. Code fences count only at the start of a line, whatever
their tag (` ```sql `, ` ```sqlite `, ` ```mysql `); the last block that starts
with `SELECT`/`WITH` is used (so a trailing ` ```bash ` block is ignored),
otherwise the text from the first line that starts with `SELECT`/`WITH`.

**Running model SQL safely.** SQLite is opened read-only; Postgres runs as a
role that may only `SELECT`, in a read-only session, rolled back after each
query. Both refuse multiple statements and have a 30 s limit and a 10,000-row
cap.

**Metrics** (per engine):
- **EX**: the model's result equals the gold result, compared like the
  validator (numbers to 2 decimals; row order only when gold has `ORDER BY`).
  Extra output columns are allowed and column order doesn't matter: every
  gold column must appear, row by row, so answering "abonemiz kim?" with name,
  surname *and* subscriber number still counts. Adjacent text columns may come
  joined by a space: `ad || ' ' || soyad` matches gold `(ad, soyad)`. Dates
  count as equal whatever their form: `'09.12.2024'` text, a `DATE` and a
  midnight timestamp of the same day all match.
- **EX strict**: the same, but the output columns must match exactly. Shown
  for reference only; every other metric and the leaderboard use EX.
- **Control / trap accuracy** and accuracy per trap category.
- **Pairs**: *fell for trap* = control right and trap wrong, which isolates
  the trap; *both wrong* means the model failed for some other reason. A pair
  with a missing answer is left out.
- **trap_sql reproduced**: how often the model's result equals the
  anticipated wrong result, i.e. whether the traps predict real failures.

**Read the pairs first.** *Fell for trap* is the headline number; raw
accuracy per category is only a rough guide. The leaderboard gives it as a
share of the pairs whose control was right (`32/80 (40%)`): a model that
fails most controls has few chances to fall for a trap, so the raw count
would rank it too well. Its note column lists what makes a row not directly
comparable: thinking switched off by prefill, a raised `max_tokens`, answers
cut off by the token limit, answers collected outside the harness. Turkish place names are full of
Ç, Ğ, İ, Ş, Ü, Ö, and every place also has upper-case migrated rows, so on
SQLite any `LOWER(...)` match on such a name (Bağlar, Kemalpaşa, Yenişehir,
Çankaya) fails whatever the question's category. That is the benchmark's
premise, not a data bug. Because the trap and control in a pair use the same
place, such a failure shows up as *both wrong* and does not count as falling
for the trap; it does lower the per-category accuracy of suffix_value and
domain on SQLite. Similarly, *trap_sql reproduced* is low by design of
real models: they fall into a trap in many ways (`ILIKE 'İğdır'` instead of
`LOWER`), and only one of them is the anticipated query.

**Output** in `results/<provider>__<model>/`: `responses.jsonl` (raw answers;
also the cache, so an interrupted run resumes and nothing is asked twice),
`details.jsonl` (per question and engine: SQL, result, status), `report.md`
(summary plus every wrong answer with its SQL), `scores.json`, and the exact
prompt in `prompt_example_<engine>.txt`. Scores are recomputed from the cache on
every run. The script exits 1 if any answer is missing; re-run the same command
to fill the gaps.
