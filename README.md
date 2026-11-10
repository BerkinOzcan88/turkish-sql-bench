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
| `suffix_value` | Values appear inflected in the question (`Esenler'deki`, `Turgut Özal Mahallesi'nde`, `depo görevlimiz`, `Mahir Bey`) but are stored bare. |
| `schema_link` | ASCII-fied or abbreviated identifiers and codes (`vkn`, `sicil_no`, `ctv_tutari`, `'RESMI'`, `'UCRETSIZ'`), and words that point to one of two similar columns (net vs brüt). |
| `tr_format` | Dates as `'DD.MM.YYYY'` text, amounts as `'1.250,50'` text, periods as `'Şubat 2024'`, hours as `'2,5'`. |
| `domain` | KDV, ÇTV, il/ilçe hierarchy, due-date (vade) logic; payroll and labour law (asgari ücret, income and stamp tax, overtime and leave rules). |
| `control` | No trap; the twin of a trap question. |

## Layout

```
common/il_ilce.json                    81 il / 973 ilçe (Wikipedia, CC BY-SA 4.0)
databases/belediye_su/schema.sql       portable DDL (SQLite + Postgres)
databases/belediye_su/generate.py      deterministic generator
databases/belediye_su/prompt_notes.json  data notes + example-row choice for model prompts
databases/belediye_su/belediye_su.sqlite   build output
databases/ik_bordro/...                same four files for the HR / payroll database
questions/belediye_su.json             50 draft questions (25 trap + 25 control)
questions/ik_bordro.json               50 draft questions (25 trap + 25 control)
scripts/build_il_ilce.py               refreshes common/il_ilce.json from Wikipedia
scripts/load_postgres.py               copies the built SQLite DBs into Postgres and checks the copy
scripts/validate.py                    checks every question on both engines
scripts/evaluate.py                    runs a model on the questions, scores it on both engines
scripts/leaderboard.py                 compares evaluation runs
results/<run>/                         evaluation output (one folder per model run)
tests/test_sqlite_lower.py             proves LOWER('IĞDIR') != 'ığdır' in SQLite
tests/test_scoring.py                  answer parsing, result comparison, pairs, leaderboard
tests/test_databases.py                money storage in SQLite, Postgres copy check
docker-compose.yml                     Postgres 16
```

## Quick start

Requires Python 3.11+ and Docker.

```bash
pip install -r requirements.txt
python databases/belediye_su/generate.py     # -> belediye_su.sqlite (~15 MB, ~5 s)
python databases/ik_bordro/generate.py       # -> ik_bordro.sqlite (~8 MB, ~3 s)
docker compose up -d --wait                  # Postgres 16 on localhost:5433
python scripts/load_postgres.py              # drops + recreates both databases, checks the copy
python scripts/validate.py                   # all question files; exit 1 on any failure
python -m unittest discover tests
```

All scripts can be re-run safely: the generator overwrites the SQLite file,
and the loader drops and recreates the Postgres database
(`load_postgres.py ik_bordro` loads just one). Postgres connection settings
come from `PGHOST`/`PGPORT`/`PGUSER`/`PGPASSWORD` (defaults `localhost:5433`,
`postgres`/`postgres`). The loader also creates a read-only role
(`PGREADER_USER`/`PGREADER_PASSWORD`, default `bench_reader`) with `SELECT`
only; `evaluate.py` runs model SQL as that role. Without Docker,
`validate.py --engines sqlite` and `evaluate.py --engines sqlite` still work.

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
- **People and streets (`belediye_su`):** [Faker](https://github.com/joke2k/faker) `tr_TR`
  (MIT) provides first names and surnames. Faker's `tr_TR` `street_name()`
  returns English-style names ("Lezize Vista"), so streets are built from
  Faker surnames plus Turkish street types (`Sokak`, `Caddesi`, `Sokağı`,
  `Bulvarı`) or numbered streets (`1453. Sokak`).
- **Businesses:** Faker's `tr_TR` `company()` returns real company names, so
  business subscribers are built from Faker surnames plus templates
  (`… Gıda San. ve Tic. Ltd. Şti.`, `… Eczanesi`). All T.C. kimlik and vergi
  numbers are random values with valid checksums. They are not real people's
  numbers.
- **People (`ik_bordro`):** Faker's `tr_TR` first names are mostly rare
  (Heyvetullah, Minibe) and miss Mehmet, Ayşe and Fatma, so `ik_bordro` draws
  from short hand-written lists of common Turkish first names and surnames,
  weighted roughly by frequency. It doesn't use Faker.

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
contents in every table: `load_postgres.py` checks this by hash, with numbers
and dates normalized, every time it loads.

Money columns are `NUMERIC(p,2)` in `schema.sql` and in Postgres, but the
generator creates them as `REAL` in SQLite. With `NUMERIC` affinity SQLite
stores a whole amount like 45100.00 as the integer 45100, and
`brut_ucret * 2 / 100` then does integer division on SQLite only.

## Database: `ik_bordro` (HR and payroll)

The HR and payroll records of a fictional company group with 25 locations in
20 il: the head office (`Genel Müdürlük`, Şişli), 3 factories, 4 depots and
17 sales offices. The İ/ı provinces from `belediye_su` are among them
(İstanbul, İzmir, Iğdır, Isparta, Kırıkkale, Çankırı, Ağrı, Diyarbakır).
Payroll covers Jan 2023 – Dec 2024, and the extract was taken on
**05.01.2025**. 2,344 people are still employed at the snapshot; 531 left
during 2023–2024 and 565 were hired in that period.

| table | rows | notes |
|---|---:|---|
| `sube` | 25 | `sube_kodu` (`IZM01`), `sube_adi` (`Bornova Şubesi`, `Genel Müdürlük`), `sehir`, `ilce`, `acilis_tarihi` |
| `departman` | 12 | `departman_adi` (`İnsan Kaynakları`, `Ar-Ge`, … and also `Genel Müdürlük`) |
| `personel` | 2,875 | `sicil_no` (hire year + sequence), `ad`, `soyad`, `tc_no`, `cinsiyet` `E`/`K`, birth/hire/exit dates, `unvan`, `kaynak_sistem` |
| `bordro` | 57,166 | one payslip per person and month; `donem` **TEXT `'Şubat 2024'`**, `calisma_gunu`, `brut_ucret`, `fazla_mesai_ucreti`, the four employee deductions, `net_ucret` |
| `izin` | 8,968 | `izin_turu` `YILLIK`/`RAPOR`/`MAZERET`/`UCRETSIZ`, start/end dates, `gun_sayisi` (Monday–Friday days) |
| `fazla_mesai` | 31,527 | one overtime session per row; `saat` **TEXT `'2,5'`**, `ucret` |

`net_ucret = brut_ucret + fazla_mesai_ucreti − sgk_isci_payi −
issizlik_isci_payi − gelir_vergisi − damga_vergisi` holds on every payslip,
and `fazla_mesai_ucreti` equals the month's sum of `fazla_mesai.ucret`.

### Where the traps live

- **Legacy import (case_i):** 468 people (16%), all hired before 2019, came
  from the previous HR system (`kaynak_sistem = 'ESKI_AKTARIM'`) with `ad`,
  `soyad` and `unvan` in Turkish-correct ALL CAPS (`IŞIL`, `ALİ`, `DEMİR`,
  `MÜDÜR YARDIMCISI`).
- **Inflected values (suffix_value):** questions say `depo görevlimiz`,
  `satış temsilcilerimiz`, `Kadir Bey`, `Duygu Hanım’ın`, `Bornova’daki
  şubemiz`; the data holds `Depo Görevlisi`, `Satış Temsilcisi`, `Kadir`,
  `Duygu`, `Bornova Şubesi`.
- **Codes and look-alike columns (schema_link):** `izin_turu` holds ASCII
  codes (`'UCRETSIZ'`, not `'ÜCRETSİZ'`); `sicil_no` is not `personel_id`;
  "eline geçen" is `net_ucret`, not `brut_ucret`; `Genel Müdürlük` is both a
  location (`sube`) and a department.
- **Text periods and hours (tr_format):** `bordro.donem` is `'Şubat 2024'`,
  so `BETWEEN`, `MAX()` and `ORDER BY donem` compare month names
  alphabetically (and differently on each engine). Month order needs a
  `CASE` mapping. `fazla_mesai.saat` is `'2,5'`. The portable parse is
  `CAST(REPLACE(saat, ',', '.') AS NUMERIC)`. Getting it wrong silently
  truncates in SQLite and raises an error in Postgres.
- **Payroll rules (domain):** the 2024 minimum wage, hourly pay as monthly
  gross / 225, the 270-hour yearly overtime limit, leave entitlement by
  seniority *and* age, and "vergi" meaning income plus stamp tax (SGK
  premiums are not taxes).

### Rates (checked against legislation, November 2026)

| item | value used | source |
|---|---|---|
| Asgari ücret, monthly gross | 10.008,00 (Jan–Jun 2023)<br>13.414,50 (Jul–Dec 2023)<br>20.002,50 (2024) | Asgari Ücret Tespit Komisyonu decisions; RG 24.06.2023 for Jul 2023 and RG 30.12.2023 for 2024 |
| SGK prime esas kazanç tavanı (7.5 × asgari ücret) | 75.060,00 / 100.608,90 / 150.018,90 | SGK genelgeleri 2023/3, 2023/22, 2024/2 |
| SGK employee share | 14% (MYÖ 9% + GSS 5%) | 5510 md. 81 |
| İşsizlik sigortası employee share | 1% | 4447 md. 49 |
| Damga vergisi on wages | binde 7,59 of gross | 488 DVK 1 sayılı tablo IV-1-b |
| Gelir vergisi tariff on wages | 2023: 70k / 150k / 550k / 1.9M<br>2024: 110k / 230k / 870k / 3M<br>at 15 / 20 / 27 / 35 / 40% | GVK md. 103; GV Genel Tebliği Seri No 323 (2023) and 324 (2024) |
| Minimum-wage exemptions | income tax on the minimum wage's own cumulative base, deducted every month and in full for part months; damga vergisi on the gross minimum wage | GVK 23/1-18 (7349 sayılı Kanun); DVK 2 sayılı tablo IV-34; GİB Ücret Geliri Vergi Rehberi 2024, 4.3 |
| Fazla mesai | each hour paid at +50%; at most 270 hours a year | İş Kanunu md. 41 |
| Hourly wage | monthly gross / 225 (30 days × 7.5 h of a 45-hour week) | payroll practice under İş Kanunu md. 63 |
| Yıllık izin | 1–5 years: 14 days, 6–14: 20, 15+: 26; at least 20 for age ≤ 18 or ≥ 50 | İş Kanunu md. 53 |

The generator reproduces published net figures for a minimum-wage employee:
8.506,80 (Jan 2023), 11.402,32 (Jul 2023) and 17.002,12 (2024).

Known simplifications:
- **Employee side only:** employer premiums, BES, union dues, disability
  allowance and garnishments are not modelled.
- **Sick leave:** salary is cut for every `RAPOR` and `UCRETSIZ` day. In
  reality SGK pays an allowance from the third day of a report and some
  employers top it up.
- **Raises:** salaries change only on 01.07.2023 and 01.01.2024, and never
  fall below the minimum wage of the period.
- **Leave:** each employee takes 40–100% of the year's entitlement; no
  carry-over, and no leave crosses a year boundary.
- **Overtime:** about half of the staff in production, logistics, IT, quality
  and admin work overtime; a few exceed the 270-hour limit on purpose.

### Determinism

`SEED = 20250105` drives `random`; no other source of randomness is used. Two
consecutive builds produce a byte-identical `ik_bordro.sqlite`, and the SQLite
and Postgres copies have identical row contents in every table (checked by
`load_postgres.py`). Money columns are `REAL` in SQLite, as in `belediye_su`.

## Questions

`questions/<db_id>.json` uses BIRD-style records:

```json
{"question_id": "belediye_su_001", "db_id": "belediye_su", "question": "...",
 "evidence": "", "SQL": "<gold>", "trap": "case_i", "trap_sql": "<plausible wrong query>",
 "pair_id": "belediye_su_p01", "status": "draft"}
```

- Each database has 25 pairs: 5 per trap category, each with a control twin.
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
    names exactly which amounts are summed.
- Rules for gold SQL, so it runs on both engines:
  - no `LOWER`/`UPPER`/`LIKE` on letters
  - no `ORDER BY` on text (the collations differ)
  - ISO dates compared only with ranges, and cast to text before `SUBSTR`
  - numeric results wrapped in `ROUND(…, 2)`
  - divide by a decimal literal (`/ 100.0`) whenever the dividend can be an
    integer column (`su_kdv_orani / 100` is 0 on both engines); money columns
    are `REAL` in SQLite, so they never divide as integers
  - `ORDER BY` with a unique key whenever row order matters

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

