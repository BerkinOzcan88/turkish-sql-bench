"""Generate belediye_su.sqlite: consolidated municipal water billing data.

Deterministic: a fixed seed for both `random` and Faker (pinned in
requirements.txt) yields identical data on every run. Re-running overwrites
the existing database.

    python databases/belediye_su/generate.py
"""
from __future__ import annotations

import json
import random
import re
import sqlite3
from dataclasses import dataclass, field
from datetime import date, timedelta
from decimal import ROUND_HALF_UP, Decimal
from pathlib import Path

from faker import Faker

SEED = 20240710
HERE = Path(__file__).resolve().parent
ROOT = HERE.parent.parent
DB_PATH = HERE / "belediye_su.sqlite"
SCHEMA_PATH = HERE / "schema.sql"
IL_ILCE_PATH = ROOT / "common" / "il_ilce.json"

DATA_START = date(2023, 1, 1)
DATA_END = date(2024, 12, 31)      # last reading month
SNAPSHOT = date(2025, 1, 5)        # extraction date: later payments do not exist yet
# KDV (2007/13033 BKK lists): içme suyu is on list I (1%) since 14.02.2022 (5189 sayılı CK);
# belediye atık su hizmetleri are on list II, 8% -> 10% from 10.07.2023 (7346 sayılı CK).
SU_KDV = 1
ATIKSU_KDV_CHANGE = date(2023, 7, 10)
# Konut ÇTV, TL per m³ (büyükşehir, diğer): BGK Genel Tebliği Seri No 56 (2023), 57 (2024).
# İşyeri ÇTV is assessed yearly and paid with emlak vergisi installments, so it is not on
# water bills (2464 mükerrer md. 44); public buildings are mostly exempt.
CTV_KONUT = {2023: (Decimal("1.50"), Decimal("1.10")), 2024: (Decimal("2.30"), Decimal("1.70"))}
# 6183 md. 51 gecikme zammı, monthly %: 2.5 (5801 sayılı CK), 3.5 from 14.11.2023, 4.5 from 21.05.2024 (8484 sayılı CK)
GECIKME_ORANLARI = [(date(2024, 5, 21), Decimal("4.5")), (date(2023, 11, 14), Decimal("3.5")), (date(2000, 1, 1), Decimal("2.5"))]
LEGACY_SHARE = 0.15

# il -> (subscriber count, month the municipality went live on the platform, büyükşehir?)
PROVINCES: dict[str, tuple[int, date, bool]] = {
    "İstanbul":   (1300, date(2024, 1, 1), True),
    "İzmir":      (600,  date(2023, 7, 1), True),
    "Ankara":     (650,  date(2024, 4, 1), True),
    "Bursa":      (400,  date(2023, 1, 1), True),
    "Mersin":     (300,  date(2024, 7, 1), True),
    "Antalya":    (350,  date(2023, 10, 1), True),
    "Diyarbakır": (300,  date(2024, 1, 1), True),
    "Iğdır":      (200,  date(2023, 4, 1), False),
    "Isparta":    (220,  date(2024, 3, 1), False),
    "Kırıkkale":  (220,  date(2023, 1, 1), False),
    "Çankırı":    (200,  date(2024, 6, 1), False),
    "Ağrı":       (260,  date(2023, 9, 1), False),
}
# relative water price level per municipality
PRICE_FACTOR = {
    "İstanbul": 1.25, "İzmir": 1.15, "Ankara": 1.10, "Bursa": 1.05, "Mersin": 1.00,
    "Antalya": 1.05, "Diyarbakır": 0.85, "Iğdır": 0.70, "Isparta": 0.80,
    "Kırıkkale": 0.80, "Çankırı": 0.75, "Ağrı": 0.70,
}

MAHALLE_NAMES = [
    "Cumhuriyet", "Atatürk", "Fatih", "Hürriyet", "İstiklal", "Barbaros", "Mimar Sinan",
    "Gazi", "Zafer", "Yıldız", "İnönü", "Fevzi Çakmak", "Yunus Emre", "Mevlana", "Çamlık",
    "Esentepe", "Kocatepe", "Sakarya", "İsmetpaşa", "Işıklar", "Ilıca", "Yeşilyurt",
    "Selçuklu", "Bahçelievler", "Karşıyaka", "Kazım Karabekir", "Akşemsettin", "Bağlarbaşı",
    "Gülbahar", "Saray", "Yenidoğan", "Ihlamur", "Şirinevler", "Kültür", "Çiçekli",
    "Dumlupınar", "Sümer", "Turgut Özal", "Pınarbaşı", "İçmeler",
]
STREET_KINDS = ["Sokak"] * 6 + ["Caddesi"] * 2 + ["Sokağı", "Bulvarı"]
METER_BRANDS = ["Baylan", "Manas", "Elektromed", "Itron", "Sensus", "Zenner"]
# Faker's tr_TR company() returns real company names; build fictional ones instead.
ISYERI_TEMPLATES = [
    "{soyad} Gıda San. ve Tic. Ltd. Şti.", "{soyad} İnşaat Ltd. Şti.", "{soyad} Tekstil A.Ş.",
    "{soyad} Otomotiv Ltd. Şti.", "{soyad} Eczanesi", "{soyad} Market", "{soyad} Fırını",
    "{soyad} Mobilya San. Tic. A.Ş.", "{soyad} Oto Yıkama", "{soyad} Lokantası",
    "{soyad} Turizm Otelcilik A.Ş.", "{soyad} Kuaför Salonu",
]
RESMI_TEMPLATES = [
    "{ilce} Kaymakamlığı", "{ilce} İlçe Milli Eğitim Müdürlüğü", "{ilce} Devlet Hastanesi",
    "{soyad} İlkokulu", "{soyad} Ortaokulu", "{ilce} Halk Kütüphanesi",
    "{ilce} Müftülüğü", "{ilce} Emniyet Müdürlüğü", "{ilce} Toplum Sağlığı Merkezi",
]

TWO = Decimal("0.01")


def money(x: Decimal | float) -> Decimal:
    return Decimal(str(x)).quantize(TWO, rounding=ROUND_HALF_UP)


def tr_upper(s: str) -> str:
    """Turkish-correct upper case: i -> İ, ı -> I."""
    return s.replace("i", "İ").replace("ı", "I").upper()


def tr_amount(x: Decimal) -> str:
    """Decimal('1250.5') -> '1.250,50'."""
    whole, frac = f"{x:.2f}".split(".")
    groups = []
    while len(whole) > 3:
        groups.insert(0, whole[-3:])
        whole = whole[:-3]
    groups.insert(0, whole)
    return ".".join(groups) + "," + frac


def tr_date(d: date) -> str:
    return d.strftime("%d.%m.%Y")


def add_months(d: date, n: int) -> date:
    y, m = divmod(d.month - 1 + n, 12)
    return date(d.year + y, m + 1, 1)


def months_between(a: date, b: date) -> int:
    return (b.year - a.year) * 12 + (b.month - a.month)


def rand_date(rng: random.Random, a: date, b: date) -> date:
    return a + timedelta(days=rng.randint(0, (b - a).days))


def tckn(rng: random.Random) -> str:
    d = [rng.randint(1, 9)] + [rng.randint(0, 9) for _ in range(8)]
    d10 = ((d[0] + d[2] + d[4] + d[6] + d[8]) * 7 - (d[1] + d[3] + d[5] + d[7])) % 10
    d.append(d10)
    d.append(sum(d) % 10)
    return "".join(map(str, d))


def vkn(rng: random.Random) -> str:
    d = [rng.randint(0, 9) for _ in range(9)]
    total = 0
    for i, digit in enumerate(d):
        v = (digit + 9 - i) % 10
        total += 9 if v == 9 else (v * 2 ** (9 - i)) % 9
    d.append((10 - total % 10) % 10)
    return "".join(map(str, d))


# ---------------------------------------------------------------- pricing

def unit_prices(il: str, tipi: str, on: date) -> tuple[Decimal, Decimal]:
    """(su, atıksu) TL/m³; prices step up every quarter (high-inflation period)."""
    base = {"KONUT": 14.0, "ISYERI": 26.0, "RESMI": 20.0}[tipi] * PRICE_FACTOR[il]
    quarters = months_between(DATA_START, on) // 3
    su = money(base * 1.09 ** quarters)
    return su, money(su * Decimal("0.5"))


def ctv_amount(il: str, tipi: str, m3: int, on: date) -> Decimal:
    """Çevre temizlik vergisi on the water bill: konut only, per m³ of consumption."""
    if tipi != "KONUT":
        return Decimal("0.00")
    return money(CTV_KONUT[on.year][0 if PROVINCES[il][2] else 1] * m3)


def charges(il: str, tipi: str, m3: int, on: date) -> tuple[Decimal, Decimal]:
    """(su bedeli, atıksu bedeli), both KDV excluded."""
    su, atiksu = unit_prices(il, tipi, on)
    if tipi == "KONUT":  # two-step tariff: above 10 m³ costs 1.5x
        billed = min(m3, 10) + (m3 - min(m3, 10)) * Decimal("1.5")
    else:
        billed = Decimal(m3)
    return money(billed * su), money(billed * atiksu)


def late_fee(amount: Decimal, due: date, paid: date) -> Decimal:
    """6183 gecikme zammı, accrued daily at the monthly rate in force on each day."""
    total = Decimal(0)
    d = due + timedelta(days=1)
    while d <= paid:
        rate = next(r for start, r in GECIKME_ORANLARI if d >= start)
        total += rate / 100 / 30
        d += timedelta(days=1)
    return money(amount * total)


# ---------------------------------------------------------------- entities

@dataclass
class Meter:
    marka: str
    cap_mm: int
    takilma: date
    sokulme: date | None = None
    start_index: int = 0
    sayac_id: int = 0
    sayac_no: str = ""


@dataclass
class Subscriber:
    il: str
    ilce: str
    tipi: str
    adi: str
    soyadi: str | None
    tc_no: str | None
    vkn: str | None
    abonelik: date
    kapanis: date | None
    address: dict
    meters: list[Meter] = field(default_factory=list)
    abone_id: int = 0
    abone_no: str = ""


def load_provinces() -> dict[str, list[tuple[str, int]]]:
    doc = json.loads(IL_ILCE_PATH.read_text(encoding="utf-8"))
    by_il = {i["il"]: [(x["ilce"], x["nufus_2025"] or 1) for x in i["ilceler"]] for i in doc["iller"]}
    missing = [il for il in PROVINCES if il not in by_il]
    if missing:
        raise SystemExit(f"il not found in {IL_ILCE_PATH}: {missing}")
    return {il: by_il[il] for il in PROVINCES}


def build_subscribers(rng: random.Random, fake: Faker) -> list[Subscriber]:
    ilceler = load_provinces()
    # a fixed mahalle pool per ilçe so addresses cluster realistically
    mahalle_pool = {
        (il, ilce): rng.sample(MAHALLE_NAMES, rng.randint(4, 9))
        for il in PROVINCES for ilce, _ in ilceler[il]
    }
    subs: list[Subscriber] = []
    for il, (count, golive, _) in PROVINCES.items():
        names = [n for n, _ in ilceler[il]]
        weights = [w for _, w in ilceler[il]]
        for _ in range(count):
            ilce = rng.choices(names, weights)[0]
            r = rng.random()
            tipi = "KONUT" if r < 0.85 else "ISYERI" if r < 0.97 else "RESMI"
            if tipi == "KONUT":
                adi, soyadi = fake.first_name(), fake.last_name()
                tc, vk = tckn(rng), None
            elif tipi == "ISYERI":
                adi = rng.choice(ISYERI_TEMPLATES).format(soyad=fake.last_name())
                soyadi, tc, vk = None, None, vkn(rng)
            else:
                adi = rng.choice(RESMI_TEMPLATES).format(ilce=ilce, soyad=fake.last_name())
                soyadi, tc, vk = None, None, vkn(rng)

            if rng.random() < 0.18:   # joined after the municipality went live
                abonelik = rand_date(rng, golive, date(2024, 11, 30))
            else:
                abonelik = rand_date(rng, date(2005, 1, 1), golive - timedelta(days=1))
            kapanis = None
            first_bill = max(add_months(abonelik, 1), golive)
            if rng.random() < 0.04 and first_bill < date(2024, 9, 1):
                kapanis = rand_date(rng, first_bill + timedelta(days=60), date(2024, 11, 30))

            street_stem = fake.last_name() if rng.random() < 0.8 else f"{rng.randint(1, 3999)}."
            address = {
                "sehir": il,
                "ilce": ilce,
                "mahalle": rng.choice(mahalle_pool[(il, ilce)]) + " Mah.",
                "sokak": f"{street_stem} {rng.choice(STREET_KINDS)}",
                "bina_no": str(rng.randint(1, 180)) + (f"/{rng.choice('ABCD')}" if rng.random() < 0.15 else ""),
                "kaynak_sistem": "YENI",
            }
            if rng.random() < LEGACY_SHARE:
                address = {k: tr_upper(v) if k in ("sehir", "ilce", "mahalle", "sokak") else v
                           for k, v in address.items()}
                address["kaynak_sistem"] = "ESKI_AKTARIM"
            subs.append(Subscriber(il, ilce, tipi, adi, soyadi, tc, vk, abonelik, kapanis, address))
    return subs


def cap_for(rng: random.Random, tipi: str) -> int:
    return {"KONUT": rng.choice([15, 15, 15, 20]),
            "ISYERI": rng.choice([20, 20, 25, 32]),
            "RESMI": rng.choice([25, 32, 40, 50])}[tipi]


def monthly_m3(rng: random.Random, tipi: str, month: int) -> int:
    seasonal = 1.25 if month in (6, 7, 8) else 0.9 if month in (12, 1, 2) else 1.0
    mean, sd = {"KONUT": (12, 5), "ISYERI": (28, 14), "RESMI": (85, 35)}[tipi]
    if rng.random() < 0.02:
        return 0
    return max(0, round(rng.gauss(mean * seasonal, sd)))


def build() -> dict[str, list[tuple]]:
    rng = random.Random(SEED)
    fake = Faker("tr_TR")
    fake.seed_instance(SEED)

    subs = build_subscribers(rng, fake)

    # ids: shuffled so abone_id != adres_id and ids do not encode the province
    abone_ids = list(range(1, len(subs) + 1))
    adres_ids = list(range(1, len(subs) + 1))
    rng.shuffle(abone_ids)
    rng.shuffle(adres_ids)
    abone_nos = rng.sample(range(10_000_000, 99_999_999), len(subs))
    for s, aid, no in zip(subs, abone_ids, abone_nos):
        s.abone_id, s.abone_no = aid, str(no)
    subs.sort(key=lambda s: s.abone_id)
    adres_rows, abone_rows = [], []
    for s, adid in zip(subs, adres_ids):
        a = s.address
        adres_rows.append((adid, a["sehir"], a["ilce"], a["mahalle"], a["sokak"], a["bina_no"], a["kaynak_sistem"]))
        abone_rows.append((s.abone_id, s.abone_no, s.adi, s.soyadi, s.tc_no, s.vkn, s.tipi, adid,
                           s.abonelik.isoformat(), "KAPALI" if s.kapanis else "AKTIF"))
    adres_rows.sort()

    # readings: one per month from the first billable month until closing / end of data
    readings = []   # (date, sub, meter, ilk, son, m3, tipi)
    for s in subs:
        golive = PROVINCES[s.il][1]
        first = max(add_months(s.abonelik, 1), golive)
        last = min(s.kapanis or DATA_END, DATA_END)
        months = []
        m = date(first.year, first.month, 1)
        while m <= last:
            months.append(m)
            m = add_months(m, 1)
        meter = Meter(rng.choice(METER_BRANDS), cap_for(rng, s.tipi),
                      takilma=s.abonelik if s.abonelik >= golive or rng.random() < 0.5
                      else rand_date(rng, s.abonelik, golive - timedelta(days=1)))
        meter.start_index = 0 if meter.takilma >= DATA_START else rng.randint(150, 4000)
        s.meters.append(meter)
        replace_at = rng.randint(2, len(months) - 2) if len(months) >= 5 and rng.random() < 0.05 else None
        route_day = rng.randint(3, 24)   # each address is on a reading route with a fixed day
        index = meter.start_index
        for i, m in enumerate(months):
            day = min(28, max(1, route_day + rng.randint(-2, 2)))
            d = date(m.year, m.month, day)
            if s.kapanis and d > s.kapanis:
                d = s.kapanis
            if replace_at is not None and i == replace_at:
                swap = d - timedelta(days=rng.randint(3, 12))
                meter.sokulme = swap
                meter = Meter(rng.choice(METER_BRANDS), cap_for(rng, s.tipi), takilma=swap)
                s.meters.append(meter)
                index = 0
            m3 = monthly_m3(rng, s.tipi, m.month)
            tipi = "TAHMINI" if rng.random() < 0.07 else "GERCEK"
            readings.append((d, s, meter, index, index + m3, m3, tipi))
            index += m3
        if s.kapanis:
            meter.sokulme = s.kapanis

    # meters
    sayac_rows = []
    all_meters = [(m.takilma, s.abone_id, k, m) for s in subs for k, m in enumerate(s.meters)]
    all_meters.sort(key=lambda t: (t[0], t[1], t[2]))
    serials = rng.sample(range(100_000, 999_999), len(all_meters))
    for sid, ((tak, abone_id, _, m), serial) in enumerate(zip(all_meters, serials), start=1):
        m.sayac_id = sid
        m.sayac_no = f"{m.marka[:2].upper()}{tak.year % 100:02d}{serial}"
        sayac_rows.append((sid, m.sayac_no, abone_id, m.marka, m.cap_mm, tak.isoformat(),
                           m.sokulme.isoformat() if m.sokulme else None))

    # readings -> invoices -> payments, ids in chronological order
    readings.sort(key=lambda r: (r[0], r[1].abone_id, r[2].sayac_id))
    okuma_rows, fatura_rows, payments = [], [], []
    for oid, (d, s, meter, ilk, son, m3, tipi) in enumerate(readings, start=1):
        okuma_rows.append((oid, meter.sayac_id, tr_date(d), ilk, son, m3, tipi))
        f_date = d + timedelta(days=rng.randint(0, 3))
        due = f_date + timedelta(days=15)
        su, atiksu = charges(s.il, s.tipi, m3, d)
        atiksu_kdv = 8 if f_date < ATIKSU_KDV_CHANGE else 10
        ctv = ctv_amount(s.il, s.tipi, m3, d)
        total = money(su * (100 + SU_KDV) / 100 + atiksu * (100 + atiksu_kdv) / 100 + ctv)
        fatura_rows.append([oid, None, s.abone_id, oid, f_date, due, su, SU_KDV, atiksu, atiksu_kdv, ctv])
        payments.extend(plan_payments(rng, oid, f_date, due, total))

    fatura_rows.sort(key=lambda r: (r[4], r[2], r[0]))
    for fid, r in enumerate(fatura_rows, start=1):
        r[0] = fid
        r[1] = f"SF{r[4].year}{fid:07d}"
    okuma_to_fatura = {r[3]: r[0] for r in fatura_rows}
    fatura_out = [(r[0], r[1], r[2], r[3], r[4].isoformat(), r[5].isoformat(),
                   float(r[6]), r[7], float(r[8]), r[9], float(r[10])) for r in fatura_rows]

    payments = [(pd, okuma_to_fatura[oid], amt, fee, ch) for (pd, oid, amt, fee, ch) in payments]
    payments.sort(key=lambda p: (p[0], p[1], p[2]))
    odeme_rows = [(i, fid, pd.isoformat(), tr_amount(amt), float(fee), ch)
                  for i, (pd, fid, amt, fee, ch) in enumerate(payments, start=1)]

    return {
        "adres": adres_rows, "abone": abone_rows, "sayac": sayac_rows,
        "okuma": okuma_rows, "fatura": fatura_out, "odeme": odeme_rows,
    }


def plan_payments(rng: random.Random, oid: int, f_date: date, due: date, total: Decimal) -> list[tuple]:
    """Return [(date, okuma_id, amount_incl_fee, late_fee, channel)]; may be empty (unpaid)."""
    r = rng.random()
    if r < 0.07:
        return []
    channel = rng.choices(["VEZNE", "BANKA", "INTERNET", "OTOMATIK"], [15, 30, 40, 15])[0]
    if channel == "OTOMATIK":
        parts = [(due, total)]
    elif r < 0.09:   # paid in two installments
        first = money(total / 2)
        parts = [(rand_date(rng, f_date, due), first),
                 (due + timedelta(days=rng.randint(-5, 40)), total - first)]
    elif r < 0.27:   # late
        parts = [(due + timedelta(days=rng.randint(1, 95)), total)]
    else:
        parts = [(rand_date(rng, f_date + timedelta(days=1), due), total)]
    out = []
    for pd, amount in parts:
        if pd > SNAPSHOT:
            continue
        fee = late_fee(amount, due, pd)
        out.append((pd, oid, amount + fee, fee, channel))
    return out


def write_sqlite(tables: dict[str, list[tuple]]) -> None:
    if DB_PATH.exists():
        DB_PATH.unlink()
    con = sqlite3.connect(DB_PATH)
    con.execute("PRAGMA foreign_keys = ON")
    # NUMERIC affinity would store whole amounts as integers (45100.00 -> 45100), so `x * rate / 100`
    # would do integer division on SQLite only; REAL keeps every amount a float, like NUMERIC in Postgres
    con.executescript(re.sub(r"NUMERIC\(\d+,\s*\d+\)", "REAL", SCHEMA_PATH.read_text(encoding="utf-8")))
    for name in ("adres", "abone", "sayac", "okuma", "fatura", "odeme"):
        rows = tables[name]
        marks = ",".join("?" * len(rows[0]))
        con.executemany(f"INSERT INTO {name} VALUES ({marks})", rows)
    con.commit()
    bad = con.execute("PRAGMA foreign_key_check").fetchall()
    if bad:
        raise SystemExit(f"foreign key violations: {bad[:5]}")
    con.execute("VACUUM")
    con.close()


def main() -> None:
    tables = build()
    write_sqlite(tables)
    size_mb = DB_PATH.stat().st_size / 1e6
    counts = ", ".join(f"{k}={len(v)}" for k, v in tables.items())
    print(f"wrote {DB_PATH.name} ({size_mb:.1f} MB): {counts}")


if __name__ == "__main__":
    main()
