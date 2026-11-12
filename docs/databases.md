# Databases

How the three databases are built, where their traps live, and the sources for every rate and rule they use. Back to the [README](../README.md).

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
- **Customers, brands and carriers (`eticaret`):** customer names come from
  the `ik_bordro` lists plus a few names common among younger people. Brands,
  publishers and the three carriers (`Kanat`, `Pusula`, `Ulak Kargo`) are
  made up. Delivery addresses are spread over il and ilçe by the 2025 ilçe
  population in `common/il_ilce.json`.

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

## Database: `eticaret` (online shop)

The orders of a fictional online shop that sells only its own stock: home and
lifestyle goods in 8 main groups (Elektronik, Küçük Ev Aletleri, Mutfak, Ev
Tekstili, Giyim, Ayakkabı ve Çanta, Kitap, Gurme Gıda), 46 categories and 674
products. It ships from two warehouses (`İstanbul Deposu`, `Ankara Deposu`) to
all 81 il through three carriers. Orders cover Jan 2023 – Dec 2024, and the
extract was taken on **05.01.2025**.

| table | rows | notes |
|---|---:|---|
| `kategori` | 46 | `kategori_adi` in the singular (`Kulaklık`, `Saklama Kabı`), `ana_kategori` |
| `urun` | 674 | `stok_kodu` (`ELK-0012`), `marka`, `liste_fiyati` (today's price, KDV included), `kdv_orani` (today's rate), weight, package size in cm, `satista` |
| `musteri` | 21,000 | `musteri_no` (`M0012345`), `ad`, `soyad`, `cinsiyet` `E`/`K`, `dogum_tarihi` (empty for 35%), `kayit_tarihi`, `eposta_izni`, `kaynak_sistem` |
| `adres` | 28,630 | `adres_basligi` (`Ev`, `İş`, `Annemler`, `Evim`, `Yazlık`), `sehir`, `ilce`, `kaynak_sistem` |
| `siparis` | 51,986 | `siparis_no`, delivery address, `kanal`, `odeme_yontemi`, `taksit_sayisi`, `kupon_kodu`, amounts, `durum` |
| `siparis_kalemi` | 91,403 | `adet`, `birim_fiyat` (price paid, KDV included), `kdv_orani` (rate on the order date) |
| `kargo` | 50,219 | one per shipped order; `cikis_deposu`, `kargoya_verilis_tarihi`, `teslim_zamani` **TEXT `'DD.MM.YYYY HH:MM'`**, `desi`, `agirlik_kg`, `kargo_maliyeti` (KDV excluded) |
| `iade` | 4,713 | one per return request; `iade_nedeni`, `durum`, `iade_tutari` **TEXT `'1.299,90'`** (approved requests only) |

`odenen_tutar = urun_toplami − indirim_tutari + kargo_ucreti` holds on every
order, and `urun_toplami` equals the sum of `adet × birim_fiyat` over its lines.

### Where the traps live

- **Legacy import (case_i):** customers who registered before the platform
  move on 01.09.2022 (11,264 of 21,000) and the addresses created before it
  (13,004 of 28,630) store `ad`, `soyad`, `sehir` and `ilce` in
  Turkish-correct ALL CAPS (`İSMAİL`, `KILIÇ`, `İZMİR`, `KARŞIYAKA`).
- **Singular values (suffix_value):** questions say `saklama kaplarından`,
  `çocuk kitaplarımızdan`, `spor ayakkabılarımızdan`, `Ankara depomuzdan`; the
  data holds `Saklama Kabı`, `Çocuk Kitabı`, `Spor Ayakkabı`, `Ankara Deposu`.
- **Look-alike columns (schema_link):** the price paid
  (`siparis_kalemi.birim_fiyat`) vs today's list price, the KDV rate on the
  order vs today's rate, the delivery address vs any of the customer's
  addresses, units (`SUM(adet)`) vs order lines, the carrier's charge
  (`kargo_maliyeti`) vs the shipping fee the customer paid (`kargo_ucreti`),
  an order with a returned item vs `durum = 'IADE_EDILDI'` (every item
  returned).
- **Text timestamps and amounts (tr_format):** `kargo.teslim_zamani` is
  `'DD.MM.YYYY HH:MM'` text, so `LIKE '2024-11%'`, string `MAX()` and
  `ORDER BY` give wrong answers; `iade.iade_tutari` is `'1.299,90'`.
- **E-commerce rules (domain):** prices include KDV, so the amount without KDV
  is `tutar * 100 / (100 + oran)`, not `tutar * (1 − oran / 100)`; KDV rates
  rose on 10.07.2023; the cayma süresi runs 14 days from delivery, not from the
  order; the billable weight is the greater of desi and kilograms, and desi is
  `en × boy × yükseklik / 3000`; Efsane Cuma 2024 was 29 November, not the
  fourth Friday (22 November); carrier charges carry 20% KDV.

### Rates and rules (checked against legislation, November 2026)

| item | value used | source |
|---|---|---|
| KDV, general rate | 18% → 20% on 10.07.2023: electronics, small appliances, kitchenware, carrier services | 7346 sayılı CK (RG 07.07.2023) |
| KDV, list (II) | 8% → 10% on 10.07.2023: clothing and home textiles (item 5), shoes (7), bags (8), books (14) | KDV Oranları Kararnamesi (II) sayılı liste; 7346 sayılı CK |
| KDV, list (I) | 1% throughout: coffee and tea (item A-7), olive oil (A-11), honey (A-2), chocolate (A-14), pasta (A-15) | KDV Oranları Kararnamesi (I) sayılı liste |
| Prices on display | include KDV | Fiyat Etiketi Yönetmeliği |
| Cayma süresi | 14 days from the day the goods are delivered; the evidence says the last day is 14 days after the start | Mesafeli Sözleşmeler Yönetmeliği md. 9(2) |
| Desi | `en × boy × yükseklik / 3000`, billed on the greater of desi and kg | Turkish carriers' published convention |
| Efsane Cuma | 24.11.2023 and 29.11.2024 | the Turkish name for Black Friday, the day after US Thanksgiving (the fourth Thursday of November) |

Known simplifications:
- **Prices:** synthetic. List prices follow a monthly inflation index (4.2% in
  2023, 3.1% in 2024) and are repriced every 40–110 days. On 10.07.2023 they
  rise by the KDV change, and 35% of the products are discounted in November.
- **Shipping:** the fee is 39.90 TL, free from 400 TL, in 2023, and 69.90 TL,
  free from 750 TL, in 2024. Carrier tariffs are synthetic.
- **Returns:** return rates depend on the main group. Requests for
  `VAZGECTIM` or `BEDEN_UYMADI` after the cayma süresi are rejected. Requests
  from 30.12.2024 on are still `BEKLIYOR`. Refunds are reduced in proportion
  to any coupon discount, and an order becomes `IADE_EDILDI` once every item
  has been returned.

### Determinism

`SEED = 20250107` drives `random`; no other source of randomness is used. Two
consecutive builds produce a byte-identical `eticaret.sqlite`, and the SQLite
and Postgres copies have identical row contents in every table (checked by
`load_postgres.py`). Money columns are `REAL` in SQLite.
