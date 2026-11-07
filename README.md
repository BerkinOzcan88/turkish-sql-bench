# Turkish-native Text-to-SQL benchmark

Existing Turkish Text-to-SQL sets (TURSpider, BIRDTurk) translate English
questions and schemas, but the data inside the databases stays English. This
benchmark uses Turkish databases filled with Turkish data, so models run into
the problems real Turkish systems have: İ/ı casing, suffixed values, ASCII-fied
identifiers, Turkish number and date formats, and Turkish domain rules.

Every question has exactly one trap category and a **control twin** (same
intent, no trap) linked by `pair_id`:

| trap | what goes wrong |
|---|---|
| `case_i` | İ/ı casing. SQLite's `LOWER()`/`UPPER()`/`LIKE` fold ASCII only (`LOWER('IĞDIR')` = `'iĞdir'`); Postgres depends on the collation. |
| `suffix_value` | Values appear inflected in the question (`Esenler'deki`, `Gül Sokağı'nda`, `Mahallesi'nde`) but are stored bare. |
| `schema_link` | ASCII-fied or abbreviated identifiers and codes (`vkn`, `tc_no`, `ctv_tutari`, `'RESMI'`). |
| `tr_format` | Dates as `'DD.MM.YYYY'` text, amounts as `'1.250,50'` text, Turkish month names. |
| `domain` | KDV, ÇTV, il/ilçe hierarchy, due-date (vade) logic. |
| `control` | No trap; the twin of a trap question. |

## Layout

```
common/il_ilce.json                    81 il / 973 ilçe (Wikipedia, CC BY-SA 4.0)
databases/belediye_su/schema.sql       portable DDL (SQLite + Postgres)
databases/belediye_su/generate.py      deterministic generator
databases/belediye_su/belediye_su.sqlite   build output
questions/belediye_su.json             50 draft questions (25 trap + 25 control)
scripts/build_il_ilce.py               refreshes common/il_ilce.json from Wikipedia
scripts/load_postgres.py               copies a built SQLite DB into Postgres
scripts/validate.py                    checks every question on both engines
scripts/evaluate.py                    runs a model on the questions, scores it on both engines
scripts/leaderboard.py                 compares evaluation runs
results/<run>/                         evaluation output (one folder per model run)
tests/test_sqlite_lower.py             proves LOWER('IĞDIR') != 'ığdır' in SQLite
docker-compose.yml                     Postgres 16
```

## Quick start

Requires Python 3.11+ and Docker.

```bash
pip install -r requirements.txt
python databases/belediye_su/generate.py     # -> belediye_su.sqlite (~15 MB, ~5 s)
docker compose up -d --wait                  # Postgres 16 on localhost:5433
python scripts/load_postgres.py belediye_su  # drops + recreates database belediye_su
python scripts/validate.py                   # exit 1 on any failure
python -m unittest discover tests
```

All scripts can be re-run safely: the generator overwrites the SQLite file,
and the loader drops and recreates the Postgres database. Postgres connection
settings come from `PGHOST`/`PGPORT`/`PGUSER`/`PGPASSWORD` (defaults
`localhost:5433`, `postgres`/`postgres`).

## Data sources and licenses

- **il/ilçe list:** `common/il_ilce.json` comes from the Turkish Wikipedia
  article [Türkiye'nin ilçeleri](https://tr.wikipedia.org/wiki/T%C3%BCrkiye%27nin_il%C3%A7eleri),
  revision 37373921, and is licensed **CC BY-SA 4.0**
  (<https://creativecommons.org/licenses/by-sa/4.0/>). Attribution:
  "Wikipedia contributors, *Türkiye'nin ilçeleri*, Vikipedi". The file holds
  81 il, 973 ilçe, region and 2025 ilçe population (TÜİK figures as cited by
  the article). Any redistribution of this file or a derivative must stay
  under CC BY-SA 4.0. `scripts/build_il_ilce.py` regenerates it.
  - Wikipedia names a province's central district after the province
    (`Iğdır` ilçesi in `Iğdır` ili), not "Merkez" as e-İçişleri does. The
    data follows Wikipedia.
- **People and streets:** [Faker](https://github.com/joke2k/faker) `tr_TR`
  (MIT) provides first names and surnames. Faker's `tr_TR` `street_name()`
  returns English-style names ("Lezize Vista"), so streets are built from
  Faker surnames plus Turkish street types (`Sokak`, `Caddesi`, `Sokağı`,
  `Bulvarı`) or numbered streets (`1453. Sokak`).
- **Businesses:** Faker's `tr_TR` `company()` returns real company names, so
  business subscribers are built from Faker surnames plus templates
  (`… Gıda San. ve Tic. Ltd. Şti.`, `… Eczanesi`). All T.C. kimlik and vergi
  numbers are random values with valid checksums. They are not real people's
  numbers.

## Postgres collation

`docker-compose.yml` runs the official `postgres:16` image (validated on
**PostgreSQL 16.15**, Debian). `load_postgres.py` creates the database without
`LOCALE`/`COLLATE`, so it inherits the image default, which is
**`en_US.utf8` with the libc provider** (`pg_database.datlocprovider = 'c'`,
`datcollate = datctype = 'en_US.utf8'`). The loader prints these values on
every run. Observed behaviour:

| expression | Postgres 16 / en_US.utf8 | SQLite 3.50 |
|---|---|---|
| `LOWER('IĞDIR')` | `iğdir` | `iĞdir` |
| `LOWER('İSTANBUL')` | `istanbul` | `İstanbul` |
| `UPPER('Iğdır')` | `IĞDIR` | `IğDıR` |
| `UPPER('istanbul')` | `ISTANBUL` (not `İSTANBUL`) | `ISTANBUL` |
| `'AĞRI' LIKE 'ağrı'` | false (`LIKE` is case-sensitive) | false (ASCII-only folding) |
| `ORDER BY` on text | linguistic, ignores punctuation | byte order |

Neither engine does Turkish casing right. They fail in *different* places,
which is why `validate.py` reports which engine each trap fails on.

## Database: `belediye_su` (municipal water billing)

A consolidated billing system shared by the water utilities of 12
municipalities. One utility serves only one province, so this setup is what
lets a single database span several il. The provinces were chosen for traps:

- the 8 with İ/ı in their names: İstanbul, İzmir, Iğdır, Isparta, Kırıkkale,
  Çankırı, Ağrı, Diyarbakır
- Ankara, Bursa, Mersin, Antalya
- `Yenişehir` exists in Bursa, Mersin and Diyarbakır, and `Aksu` in Antalya
  and Isparta

Each municipality joined the platform on a different date between Jan 2023
and Jul 2024 (`PROVINCES` in `generate.py`). Readings start at that date, so
for example İstanbul has no 2023 data. The data covers Jan 2023 – Dec 2024,
and the extract was taken on **05.01.2025**.

| table | rows | notes |
|---|---:|---|
| `adres` | 5,000 | `sehir`, `ilce`, `mahalle`, `sokak`, `bina_no`, `kaynak_sistem` |
| `abone` | 5,000 | `abone_no`, `abone_adi`, `abone_soyadi`, `tc_no`, `vkn`, `abone_tipi`, `abonelik_tarihi`, `durum` |
| `sayac` | 5,224 | `sayac_no`, `marka`, `cap_mm`, `takilma_tarihi`, `sokulme_tarihi` (about 5% of meters were replaced) |
| `okuma` | 61,981 | `okuma_tarihi` **TEXT `DD.MM.YYYY`**, `ilk_endeks`, `son_endeks`, `tuketim_m3`, `okuma_tipi` |
| `fatura` | 61,981 | one per reading; `su_bedeli` + `su_kdv_orani`, `atiksu_bedeli` + `atiksu_kdv_orani` (percents), `ctv_tutari`; ISO dates |
| `odeme` | 56,940 | `odenen_tutar` **TEXT `'1.250,50'`** next to NUMERIC `gecikme_zammi`; ISO dates |

### Where the traps live

- **Legacy import (case_i):** 15% of `adres` rows (`kaynak_sistem =
  'ESKI_AKTARIM'`) store `sehir`/`ilce`/`mahalle`/`sokak` in Turkish-correct
  ALL CAPS (`IĞDIR`, `İSTANBUL`, `DİYARBAKIR`, `IŞIKLAR MAH.`). A correct
  filter needs both spellings, e.g. `sehir IN ('Iğdır', 'IĞDIR')`.
- **Codes (schema_link):** `abone_tipi` (`KONUT`/`ISYERI`/`RESMI`), `durum`,
  `okuma_tipi` and `odeme_kanali` hold ASCII upper-case codes. Turkish-correct
  upper case (`'RESMİ'`) matches nothing.
- **Two date formats (tr_format):** `okuma.okuma_tarihi` is `'DD.MM.YYYY'`
  text, so string comparison and `MAX()` give wrong answers. Every other date
  is ISO.
- **Text amounts (tr_format):** `odeme.odenen_tutar` is `'1.250,50'`. The
  portable parse is
  `CAST(REPLACE(REPLACE(odenen_tutar, '.', ''), ',', '.') AS NUMERIC)`.
  Getting it wrong silently truncates in SQLite and raises an error in
  Postgres.
- **KDV (domain):** a bill has two KDV rates and no stored total. Both rates
  are stored as percents: water is 1%, and atıksu is 8% for invoices before
  10.07.2023 and 10% from then on. The amount payable is
  `su_bedeli * (1 + su_kdv_orani / 100.0) + atiksu_bedeli * (1 + atiksu_kdv_orani / 100.0) + ctv_tutari`,
  because KDV does not apply to ÇTV.
- **Hierarchy (domain):** ilçe names are not unique across il (Yenişehir,
  Aksu), so the il filter matters.

### Rates (checked against legislation, November 2026)

| item | value used | source |
|---|---|---|
| KDV on water (`su_kdv_orani`) | 1% throughout | Water (GTİP 22.01) moved to list I (1%) by 5189 sayılı CK, in force 14.02.2022; 7346 left 1% unchanged |
| KDV on atıksu (`atiksu_kdv_orani`) | 8% → 10% on 10.07.2023 | List II item 27 "belediyeler … atık su hizmetleri"; 7346 sayılı CK raised list II from 8% to 10% |
| ÇTV and KDV | ÇTV is outside the KDV base | GİB özelge B.07.0.GEL.0.52/5220-9/12448 (2001) |
| ÇTV konut (TL/m³, büyükşehir / other) | 2023: 1.50 / 1.10<br>2024: 2.30 / 1.70 | BGK Genel Tebliği Seri No 56 and 57 |
| ÇTV for işyeri | 0 on water bills | 2464 mükerrer md. 44: işyeri ÇTV is assessed yearly in January and paid in emlak vergisi installments, not with the water bill |
| ÇTV for resmi | 0 | md. 44 exempts buildings that public bodies use only for public service |
| Gecikme zammı (monthly, accrued daily) | 2.5% → 3.5% from 14.11.2023 → 4.5% from 21.05.2024 | 6183 md. 51; 5801 and 8484 sayılı CK |

Known simplifications:
- **ÇTV reduced rate not applied:** municipalities in kalkınmada öncelikli
  yöreler get a 50% reduced ÇTV rate (0.55 TL/m³ in 2023, 0.85 in 2024), but
  I couldn't verify which of the 12 provinces qualify, so the full rate is
  used everywhere.
- **Gecikme zammı on water debts:** charging the 6183 rate on su bedeli (not
  only on ÇTV) follows common practice, not a specific provision.
- **Prices:** water prices are synthetic. Each municipality has its own price
  level, prices rise 9% per quarter, konut tariffs have two tiers (above
  10 m³ costs 1.5×), and atıksu is 50% of the water price.
- **Payments:** about 7% of invoices are never paid, about 2% are paid in two
  installments, and about 18% are paid late. Payments after 05.01.2025 don't
  exist yet.

### Determinism

`SEED = 20240710` drives both `random` and Faker (`faker==40.44.0` is pinned).
The generator never iterates over sets, and IDs are assigned after
deterministic sorts. Two consecutive builds produce a byte-identical
`belediye_su.sqlite`. The SQLite and Postgres copies have identical row
contents in every table (checked by hash, with numerics normalized).

## Questions

`questions/belediye_su.json` uses BIRD-style records:

```json
{"question_id": "belediye_su_001", "db_id": "belediye_su", "question": "...",
 "evidence": "", "SQL": "<gold>", "trap": "case_i", "trap_sql": "<plausible wrong query>",
 "pair_id": "belediye_su_p01", "status": "draft"}
```

- There are 25 pairs: 5 per trap category, each with a control twin.
  Controls have `trap_sql: null`.
- `evidence` holds only facts the database can't provide (the extract date).
  It deliberately doesn't reveal the trap.
- Rules for gold SQL, so it runs on both engines:
  - no `LOWER`/`UPPER`/`LIKE` on letters
  - no `ORDER BY` on text (the collations differ)
  - ISO dates compared only with ranges, and cast to text before `SUBSTR`
  - numeric results wrapped in `ROUND(…, 2)`
  - `ORDER BY` with a unique key whenever row order matters

## Validator

`scripts/validate.py` checks, for each question and each engine:
1. Gold SQL runs and returns a non-empty, not-all-NULL result.
2. Gold results match across engines. Numbers are compared with ±0.011
   tolerance, and row order is ignored unless the query has a top-level
   `ORDER BY`.
3. For trap questions, `trap_sql` differs from gold on at least one engine.
   An error counts as a difference. The table shows `both`, `sqlite only` or
   `postgres only`.

It also checks the file structure: required keys, known categories, and
exactly one trap plus one control per `pair_id`. It prints a summary table and
exits 1 on any failure.

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
python scripts/leaderboard.py                                                     # compare runs
```

Model names are the provider's own IDs (NIM: see build.nvidia.com; LM Studio:
the identifier of the loaded model). Useful options: `--engines sqlite` (no
Postgres needed), `--ids`, `--workers N`, `--rpm N`, `--max-tokens` (raise it
for reasoning models), `--extra-body '{...}'` for model-specific request
fields, `--run-name`.

**Prompt.** One user message (no system message, since some models reject
it): the engine name (SQLite 3 or PostgreSQL 16), the `CREATE TABLE`
statements from `schema.sql` with all comments removed (they would give the
traps away), 3 example rows per table (the first rows by id, BIRD style), the
`evidence` when non-empty, the question, and a request for one ```sql block.
Temperature 0. Bump `PROMPT_VERSION` in the script when changing the template.

**Answer parsing.** Reasoning before `</think>` is dropped; an answer whose
reasoning never closes counts as no SQL. The last ```sql block is used,
otherwise the text from the first `SELECT`/`WITH`.

**Running model SQL safely.** SQLite is opened read-only; Postgres runs in a
read-only session, one statement at a time, rolled back after each query. Both
have a 30 s limit and a 10,000-row cap.

**Metrics** (per engine):
- **EX**: the model's result equals the gold result, compared like the
  validator (numbers to 2 decimals; row order only when gold has `ORDER BY`).
- **EX relaxed**: the same, but extra output columns are allowed.
- **Control / trap accuracy** and accuracy per trap category.
- **Pairs**: *fell for trap* = control right and trap wrong, which isolates
  the trap; *both wrong* means the model failed for some other reason.
- **trap_sql reproduced**: how often the model's result equals the
  anticipated wrong result, i.e. whether the traps predict real failures.

**Output** in `results/<provider>__<model>/`: `responses.jsonl` (raw answers;
also the cache, so an interrupted run resumes and nothing is asked twice),
`details.jsonl` (per question and engine: SQL, result, status), `report.md`
(summary plus every wrong answer with its SQL), `scores.json`, and the exact
prompt in `prompt_example_<engine>.txt`. Scores are recomputed from the cache on
every run. The script exits 1 if any answer is missing; re-run the same command
to fill the gaps.

