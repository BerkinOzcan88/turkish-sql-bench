# Turkish-native Text-to-SQL benchmark

Existing Turkish Text-to-SQL sets (TURSpider, BIRDTurk) translate English questions and schemas,
but the data inside the databases stays English. This benchmark uses three Turkish databases
filled with Turkish data, so models run into the problems real Turkish systems have: İ/ı
casing, suffixed values, ASCII-fied identifiers, Turkish number and date formats, and Turkish
domain rules.

**v0.1:** 300 questions, 150 trap/control pairs, scored on SQLite and PostgreSQL 16.

## How it works

Every question has one trap and a **control twin**: the same question without the trap. A
model that answers the control but not the trap has fallen for the trap, and that is the
headline number.

```text
trap     Iğdır’da toplam kaç aktif abonemiz var?
control  Bursa’da toplam kaç aktif abonemiz var?
```

The city is stored as `Iğdır`, or `IĞDIR` in rows migrated from the old system. On SQLite
`LOWER('IĞDIR')` gives `'iĞdir'`, so `LOWER(sehir) = LOWER('Iğdır')` misses those rows. Bursa
has no Turkish letters, so the same query works for the control.

| trap | what goes wrong |
|---|---|
| `case_i` | İ/ı casing: SQLite folds ASCII only, Postgres depends on the collation |
| `suffix_value` | values appear inflected or spelled out (`Esenler’deki`, `saklama kaplarından`, `Anonim Şirketi`) but are stored bare or abbreviated |
| `schema_link` | ASCII-fied identifiers and codes (`vkn`, `'UCRETSIZ'`), and words that point to one of two similar columns (net vs brüt) |
| `tr_format` | `'DD.MM.YYYY'` text dates, `'1.250,50'` text amounts, `'Şubat 2024'` periods, Turkish formats in the question (`1.500 TL`) |
| `domain` | KDV and other tax rates and their change dates, labour law, cayma süresi, desi, il/ilçe, dates relative to the data extract |

| database | what it is | pairs |
|---|---|---|
| `belediye_su` | municipal water billing: subscribers, meters, readings, invoices, payments | 50 |
| `ik_bordro` | HR and payroll: staff, branches, payslips, leave, overtime | 50 |
| `eticaret` | a single-shop online retailer: orders, products, shipping, returns | 50 |

The data is synthetic, generated deterministically. The tax rates and legal rules are real
and were checked against the legislation; [docs/databases.md](docs/databases.md) lists the
sources.

## Results

| model | EX SQLite | EX Postgres | controls | traps | fell for trap |
|---|---|---|---|---|---|
| Claude Opus 5.5 ¹ | 99% | 100% | 100% | 99% | 3/300 (1%) |
| Claude Sonnet 5.5 ¹ | 99% | 99% | 100% | 98% | 5/299 (2%) |
| Claude Haiku 5.5 ¹ | 98% | 93% | 100% | 91% | 26/300 (9%) ² |
| NVIDIA Nemotron 3.5 Bolt (NIM API) | 51% | 52% | 67% | 36% | 100/201 (50%) |

**EX** is execution accuracy: the query's result equals the gold result. Extra output columns
are allowed. **Fell for trap** counts pairs where the control was right and the trap wrong,
out of the pairs whose control was right, summed over both engines.

¹ Answered by Claude Code subagents, not through the API. Each subagent answered a batch of
150 questions (one engine, traps or controls only) in one go, so these rows are not directly
comparable with API runs. ² In its Postgres trap batch, Haiku took to excluding cancelled
orders from questions that didn't ask for it: 17 of its 21 Postgres trap misses have that
filter. Its control batches didn't do it.

Nemotron was run with `max_tokens` 8192 and temperature 0. `python scripts/leaderboard.py`
rebuilds this table from `results/`.

## Quick start

Requires Python 3.11+ and Docker.

```bash
pip install -r requirements.txt
python databases/belediye_su/generate.py     # builds the SQLite databases (~25 s in total)
python databases/ik_bordro/generate.py
python databases/eticaret/generate.py
docker compose up -d --wait                  # Postgres 16 on localhost:5433
python scripts/load_postgres.py              # copies the databases into Postgres
python scripts/validate.py                   # checks every question on both engines
```

Run a model through any OpenAI-compatible API:

```bash
export NVIDIA_API_KEY=nvapi-...
python scripts/evaluate.py --provider nim --model <model-id>      # or --provider lmstudio / ollama / openrouter
python scripts/evaluate.py --base-url http://host/v1 --api-key-env MY_KEY --model <model-id>
python scripts/leaderboard.py
```

Answers are cached in `results/<run>/`, so an interrupted run resumes where it stopped.
Without Docker, add `--engines sqlite`.

## Limitations

- **Synthetic data.** The databases are generated, not real company data. They model the
  quirks of real Turkish systems, but their distributions are made up.
- **Single-turn questions only**, one SQL query per question, scored by execution accuracy.
  No partial credit, no multi-turn clarification.
- **Small.** 50 pairs per database, 10 per trap category, so a difference of one or two
  pairs between models is noise.
- **One prompt format.** Every model gets the schema without comments, 3 example rows per
  table and a short data dictionary. Other prompt formats may change the ranking.
- **Known shortcuts.** Row IDs follow date order, so in two `tr_format` questions a model can
  sort by ID instead of parsing the Turkish date. Some traps are weaker than others; the
  known ones are listed in [docs/methodology.md](docs/methodology.md).

## Documentation

- [docs/databases.md](docs/databases.md): data sources, the three databases, where the traps
  live, rates and rules with sources, Postgres collation.
- [docs/methodology.md](docs/methodology.md): repo layout, question format and fairness
  rules, validator, evaluation harness and metrics.

## License

Code: MIT ([LICENSE](LICENSE)). Data (questions, databases, `common/il_ilce.json`):
CC BY-SA 4.0 ([LICENSE-DATA](LICENSE-DATA)), because the il/ilçe list comes from Turkish
Wikipedia.
