"""Generate ik_bordro.sqlite: payroll and HR records of a fictional company group.

Deterministic: a fixed `random` seed yields identical data on every run.
Re-running overwrites the existing database.

    python databases/ik_bordro/generate.py
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

SEED = 20250105
HERE = Path(__file__).resolve().parent
ROOT = HERE.parent.parent
DB_PATH = HERE / "ik_bordro.sqlite"
SCHEMA_PATH = HERE / "schema.sql"
IL_ILCE_PATH = ROOT / "common" / "il_ilce.json"

DATA_START = date(2023, 1, 1)
DATA_END = date(2024, 12, 31)      # last payroll month
SNAPSHOT = date(2025, 1, 5)        # extraction date
OLDEST_BIRTH = date(1962, 1, 6)    # nobody is older than 62 at the snapshot

# Asgari ücret, monthly gross (Asgari Ücret Tespit Komisyonu): 10.008,00 for 01-06/2023,
# 13.414,50 from 01.07.2023 (RG 24.06.2023), 20.002,50 for 2024 (RG 30.12.2023).
ASGARI = [(date(2024, 1, 1), Decimal("20002.50")), (date(2023, 7, 1), Decimal("13414.50")),
          (date(2023, 1, 1), Decimal("10008.00"))]
# SGK prime esas kazanç üst sınırı (7.5 × asgari ücret), SGK genelgeleri 2023/3, 2023/22, 2024/2.
SGK_TAVAN = [(date(2024, 1, 1), Decimal("150018.90")), (date(2023, 7, 1), Decimal("100608.90")),
             (date(2023, 1, 1), Decimal("75060.00"))]
SGK_ISCI = Decimal("0.14")        # 5510 md. 81: malullük-yaşlılık-ölüm 9% + genel sağlık 5%
ISSIZLIK_ISCI = Decimal("0.01")   # 4447 md. 49
DAMGA = Decimal("0.00759")        # 488 DVK 1 sayılı tablo IV-1-b: binde 7,59 of gross
# GVK md. 103 tariff for wage income (GV Genel Tebliği Seri No 323 for 2023, 324 for 2024).
GV_TARIFE = {
    2023: [(Decimal(70_000), Decimal("0.15")), (Decimal(150_000), Decimal("0.20")),
           (Decimal(550_000), Decimal("0.27")), (Decimal(1_900_000), Decimal("0.35")), (None, Decimal("0.40"))],
    2024: [(Decimal(110_000), Decimal("0.15")), (Decimal(230_000), Decimal("0.20")),
           (Decimal(870_000), Decimal("0.27")), (Decimal(3_000_000), Decimal("0.35")), (None, Decimal("0.40"))],
}
FAZLA_MESAI_ZAM = Decimal("1.5")  # İş K. md. 41: each overtime hour paid at +50%
AYLIK_SAAT = Decimal(225)         # payroll practice: 30 days × 7.5 h (45-hour week)

AYLAR = ["Ocak", "Şubat", "Mart", "Nisan", "Mayıs", "Haziran",
         "Temmuz", "Ağustos", "Eylül", "Ekim", "Kasım", "Aralık"]

# (il, ilçe, şube kodu, kind, opening date, headcount on 01.01.2023)
BRANCHES = [
    ("İstanbul", "Şişli", "IST01", "GM", date(1994, 3, 1), 300),
    ("İstanbul", "Kadıköy", "IST02", "SATIS", date(2001, 6, 1), 90),
    ("İstanbul", "Esenyurt", "IST03", "DEPO", date(2012, 9, 1), 140),
    ("İstanbul", "Tuzla", "IST04", "FABRIKA", date(1998, 4, 1), 320),
    ("Kocaeli", "Gebze", "KOC01", "FABRIKA", date(2006, 2, 1), 300),
    ("Ankara", "Çankaya", "ANK01", "SATIS", date(1999, 10, 1), 85),
    ("Ankara", "Yenimahalle", "ANK02", "DEPO", date(2015, 3, 1), 90),
    ("İzmir", "Bornova", "IZM01", "SATIS", date(2000, 5, 1), 80),
    ("İzmir", "Kemalpaşa", "IZM02", "FABRIKA", date(2009, 11, 1), 260),
    ("Bursa", "Nilüfer", "BRS01", "SATIS", date(2003, 4, 1), 60),
    ("Antalya", "Muratpaşa", "ANT01", "SATIS", date(2005, 8, 1), 55),
    ("Adana", "Seyhan", "ADA01", "SATIS", date(2007, 1, 1), 50),
    ("Konya", "Selçuklu", "KNY01", "SATIS", date(2010, 6, 1), 45),
    ("Kayseri", "Melikgazi", "KYS01", "SATIS", date(2014, 4, 1), 40),
    ("Gaziantep", "Şehitkamil", "GAZ01", "DEPO", date(2013, 9, 1), 80),
    ("Eskişehir", "Tepebaşı", "ESK01", "SATIS", date(2016, 2, 1), 35),
    ("Samsun", "İlkadım", "SMS01", "SATIS", date(2011, 10, 1), 35),
    ("Trabzon", "Ortahisar", "TRB01", "SATIS", date(2017, 5, 1), 30),
    ("Diyarbakır", "Kayapınar", "DYB01", "SATIS", date(2012, 3, 1), 35),
    ("Erzurum", "Yakutiye", "ERZ01", "SATIS", date(2018, 7, 1), 25),
    ("Iğdır", "Iğdır", "IGD01", "SATIS", date(2019, 4, 1), 20),
    ("Isparta", "Isparta", "ISP01", "SATIS", date(2015, 9, 1), 25),
    ("Kırıkkale", "Kırıkkale", "KRK01", "DEPO", date(2016, 11, 1), 70),
    ("Çankırı", "Çankırı", "CNK01", "SATIS", date(2020, 2, 1), 18),
    ("Ağrı", "Ağrı", "AGR01", "SATIS", date(2021, 6, 1), 18),
]

DEPARTMENTS = ["Genel Müdürlük", "İnsan Kaynakları", "Bilgi İşlem", "Muhasebe", "Satın Alma",
               "Pazarlama", "Ar-Ge", "Satış", "Üretim", "Kalite Kontrol", "Lojistik", "İdari İşler"]

DEPT_MIX = {
    "GM": {"Genel Müdürlük": 4, "İnsan Kaynakları": 12, "Bilgi İşlem": 20, "Muhasebe": 14,
           "Satın Alma": 10, "Pazarlama": 12, "Ar-Ge": 18, "İdari İşler": 10},
    "FABRIKA": {"Üretim": 62, "Kalite Kontrol": 10, "Lojistik": 12, "İdari İşler": 8,
                "İnsan Kaynakları": 3, "Muhasebe": 3, "Satın Alma": 2},
    "DEPO": {"Lojistik": 75, "İdari İşler": 10, "Muhasebe": 5, "Satış": 10},
    "SATIS": {"Satış": 70, "Lojistik": 12, "Muhasebe": 8, "İdari İşler": 10},
}

# (unvan, weight, salary low, salary high as multiples of the 2024 minimum wage, share paid exactly the minimum wage)
OFFICE_LADDER = [("Müdür", 2, 4.0, 6.0, 0), ("Müdür Yardımcısı", 3, 3.0, 4.0, 0), ("Şef", 5, 2.3, 3.0, 0),
                 ("Kıdemli Uzman", 12, 2.4, 3.3, 0), ("Uzman", 30, 1.7, 2.5, 0), ("Uzman Yardımcısı", 15, 1.3, 1.7, 0)]
ROLES = {
    "Genel Müdürlük": [("Asistan", 6, 1.3, 1.8, 0), ("Uzman", 4, 1.7, 2.5, 0)],
    "İnsan Kaynakları": OFFICE_LADDER + [("İnsan Kaynakları Uzmanı", 20, 1.8, 2.6, 0)],
    "Bilgi İşlem": OFFICE_LADDER + [("Yazılım Geliştirici", 25, 2.4, 4.5, 0), ("Sistem Yöneticisi", 8, 2.2, 3.4, 0)],
    "Muhasebe": OFFICE_LADDER + [("Muhasebe Uzmanı", 20, 1.8, 2.6, 0)],
    "Satın Alma": OFFICE_LADDER + [("Satın Alma Uzmanı", 20, 1.8, 2.6, 0)],
    "Pazarlama": OFFICE_LADDER + [("Pazarlama Uzmanı", 20, 1.8, 2.8, 0)],
    "Ar-Ge": OFFICE_LADDER + [("Mühendis", 35, 2.2, 3.6, 0)],
    "Kalite Kontrol": OFFICE_LADDER + [("Kalite Kontrol Uzmanı", 20, 1.7, 2.4, 0), ("Laborant", 15, 1.2, 1.6, 0)],
    "Satış": [("Müdür", 2, 4.0, 6.0, 0), ("Müdür Yardımcısı", 2, 3.0, 4.0, 0), ("Şef", 5, 2.2, 2.9, 0),
              ("Uzman", 8, 1.7, 2.4, 0), ("Satış Temsilcisi", 55, 1.3, 2.2, 0)],
    "Üretim": [("Müdür", 1, 4.0, 6.0, 0), ("Müdür Yardımcısı", 1, 3.0, 4.0, 0), ("Şef", 3, 2.2, 2.9, 0),
               ("Vardiya Amiri", 6, 1.8, 2.4, 0), ("Mühendis", 8, 2.2, 3.4, 0), ("Teknisyen", 15, 1.3, 1.8, 0),
               ("Operatör", 55, 1.0, 1.25, 0.75)],
    "Lojistik": [("Müdür", 1, 4.0, 5.5, 0), ("Şef", 4, 2.2, 2.8, 0), ("Lojistik Uzmanı", 8, 1.7, 2.4, 0),
                 ("Depo Sorumlusu", 12, 1.4, 1.9, 0), ("Şoför", 20, 1.15, 1.5, 0.2), ("Depo Görevlisi", 45, 1.0, 1.2, 0.7)],
    "İdari İşler": [("Müdür", 1, 3.5, 5.0, 0), ("Şef", 3, 2.0, 2.6, 0), ("İdari İşler Uzmanı", 10, 1.5, 2.1, 0),
                    ("Aşçı", 8, 1.1, 1.4, 0), ("Güvenlik Görevlisi", 30, 1.0, 1.2, 0.8),
                    ("Temizlik Görevlisi", 30, 1.0, 1.1, 0.9)],
}
# Common Turkish names with rough frequency weights (most frequent first). Curated rather than
# Faker's tr_TR lists, which are dominated by rare names.
MALE_NAMES = {
    "Mehmet": 30, "Mustafa": 22, "Ahmet": 20, "Ali": 16, "Hüseyin": 12, "Hasan": 12, "İbrahim": 10,
    "Murat": 10, "Emre": 10, "İsmail": 9, "Burak": 9, "Yusuf": 9, "Ömer": 8, "Osman": 7, "Fatih": 7,
    "Hakan": 7, "Furkan": 6, "Ramazan": 6, "Süleyman": 6, "Abdullah": 6, "Onur": 6, "Halil": 5,
    "Mahmut": 5, "Serkan": 5, "Can": 5, "Barış": 5, "Gökhan": 5, "Uğur": 5, "Enes": 5, "Kadir": 4,
    "Recep": 4, "Kemal": 4, "Cem": 4, "Kaan": 4, "Volkan": 4, "Tolga": 4, "Özgür": 4, "Erkan": 4,
    "Yasin": 4, "Oğuzhan": 4, "Eren": 4, "Umut": 4, "Selim": 3, "Engin": 3, "Levent": 3, "Orhan": 3,
    "Erdem": 3, "Sinan": 3, "Okan": 3, "Ercan": 3, "Ferhat": 3, "Metin": 3, "Berk": 3, "Batuhan": 3,
    "Kerem": 3, "Serdar": 3, "Ümit": 3, "Alper": 3, "Anıl": 3, "Tuncay": 2, "Cengiz": 2, "Taner": 2,
    "Arda": 2, "Sercan": 2, "Önder": 2, "Çağrı": 2, "Buğra": 2, "Ertuğrul": 2, "Doğan": 2, "İlker": 2,
    "Aydın": 2, "Erdal": 2, "Bülent": 2, "Zafer": 2, "Cihan": 2, "Tarık": 2, "Sedat": 2, "Tayfun": 2,
    "Koray": 2, "Selçuk": 2, "Şahin": 2, "Ilgaz": 1, "Halit": 1, "Şenol": 1,
}
FEMALE_NAMES = {
    "Fatma": 20, "Ayşe": 20, "Emine": 14, "Zeynep": 14, "Elif": 14, "Hatice": 12, "Merve": 12,
    "Esra": 9, "Büşra": 8, "Meryem": 7, "Özlem": 7, "Kübra": 7, "Zehra": 6, "Yasemin": 6, "Dilek": 6,
    "Ebru": 6, "Burcu": 6, "Pınar": 6, "Gizem": 6, "Tuğba": 6, "Şerife": 5, "Derya": 5, "Seda": 5,
    "Selin": 5, "Gamze": 5, "Hülya": 5, "Sibel": 5, "Aylin": 5, "Deniz": 5, "Damla": 5, "Duygu": 5,
    "İrem": 5, "Aslı": 5, "Özge": 5, "Sevgi": 4, "Tuba": 4, "Canan": 4, "Şule": 4, "Dilara": 4,
    "Ece": 4, "Arzu": 4, "Ceren": 4, "Çiğdem": 4, "Cansu": 4, "Betül": 4, "Rabia": 4, "Gül": 3,
    "Nur": 3, "Sevim": 3, "İpek": 3, "Işıl": 3, "Irmak": 3, "Sıla": 3, "Çağla": 3, "Gülşen": 3,
    "Songül": 3, "Melek": 3, "Hacer": 3, "Havva": 3, "Sultan": 3, "Selma": 3, "Hande": 3, "Nazlı": 3,
    "Melis": 3, "Sinem": 3, "Esma": 3, "Hilal": 3, "Serap": 3, "Filiz": 3, "Nermin": 2, "Esin": 2,
    "Ilgın": 2, "Yıldız": 2, "Ümran": 2, "Ülkü": 2, "Nilay": 2, "Begüm": 2, "Nurcan": 2, "Gülay": 2,
}
SURNAMES = {
    "Yılmaz": 30, "Kaya": 22, "Demir": 21, "Şahin": 18, "Çelik": 18, "Yıldız": 16, "Yıldırım": 16,
    "Öztürk": 15, "Aydın": 15, "Özdemir": 14, "Arslan": 13, "Doğan": 13, "Kılıç": 12, "Aslan": 11,
    "Çetin": 11, "Kara": 10, "Koç": 10, "Kurt": 10, "Özkan": 9, "Şimşek": 9, "Polat": 8, "Özcan": 8,
    "Korkmaz": 8, "Çakır": 8, "Erdoğan": 8, "Yavuz": 7, "Can": 7, "Acar": 7, "Şen": 7, "Aktaş": 7,
    "Güler": 7, "Yalçın": 7, "Güneş": 6, "Bozkurt": 6, "Bulut": 6, "Keskin": 6, "Ünal": 6, "Turan": 6,
    "Gül": 6, "Özer": 5, "Işık": 5, "Kaplan": 5, "Avcı": 5, "Sarı": 5, "Tekin": 5, "Taş": 5, "Köse": 5,
    "Yüksel": 5, "Ateş": 5, "Aksoy": 5, "Akar": 4, "Sezer": 4, "Duran": 4, "Yaman": 4, "Akın": 4,
    "Erdem": 4, "Karaca": 4, "Coşkun": 4, "Soylu": 3, "Zorlu": 3, "Uçar": 3, "Tunç": 3, "Uysal": 3,
    "Toprak": 3, "Altun": 3, "Ekinci": 3, "Akgül": 3, "Sönmez": 3, "Kalkan": 2, "Durmaz": 2, "Kocabaş": 2,
}

OVERTIME_DEPTS = {"Üretim", "Lojistik", "Bilgi İşlem", "Kalite Kontrol", "İdari İşler"}
NO_OVERTIME_TITLES = {"Müdür", "Müdür Yardımcısı", "Genel Müdür", "Genel Müdür Yardımcısı"}
LEGACY_BEFORE = date(2019, 1, 1)   # records older than this came over from the previous HR system
LEGACY_RATE = 0.45

TWO = Decimal("0.01")


def money(x: Decimal | float) -> Decimal:
    return Decimal(str(x)).quantize(TWO, rounding=ROUND_HALF_UP)


def round50(x: Decimal) -> Decimal:
    return (x / 50).quantize(Decimal(1), rounding=ROUND_HALF_UP) * 50


def tr_upper(s: str) -> str:
    """Turkish-correct upper case: i -> İ, ı -> I."""
    return s.replace("i", "İ").replace("ı", "I").upper()


def tr_hours(h: Decimal) -> str:
    """Decimal('2.5') -> '2,5', Decimal('8.0') -> '8'."""
    return f"{h.normalize():f}".replace(".", ",")


def rate_on(table: list[tuple[date, Decimal]], on: date) -> Decimal:
    return next(v for start, v in table if on >= start)


def add_months(d: date, n: int) -> date:
    y, m = divmod(d.month - 1 + n, 12)
    return date(d.year + y, m + 1, 1)


def month_end(d: date) -> date:
    return add_months(d, 1) - timedelta(days=1)


def years_between(a: date, b: date) -> int:
    return b.year - a.year - ((b.month, b.day) < (a.month, a.day))


def rand_date(rng: random.Random, a: date, b: date) -> date:
    return a + timedelta(days=rng.randint(0, (b - a).days))


def weekdays(a: date, b: date) -> int:
    return sum(1 for i in range((b - a).days + 1) if (a + timedelta(days=i)).weekday() < 5)


def tckn(rng: random.Random) -> str:
    d = [rng.randint(1, 9)] + [rng.randint(0, 9) for _ in range(8)]
    d10 = ((d[0] + d[2] + d[4] + d[6] + d[8]) * 7 - (d[1] + d[3] + d[5] + d[7])) % 10
    d.append(d10)
    d.append(sum(d) % 10)
    return "".join(map(str, d))


# ---------------------------------------------------------------- payroll rules

def income_tax(year: int, base: Decimal) -> Decimal:
    """Tax on a cumulative annual base (unrounded)."""
    tax, lower = Decimal(0), Decimal(0)
    for upper, rate in GV_TARIFE[year]:
        if upper is None or base <= upper:
            return tax + (base - lower) * rate
        tax += (upper - lower) * rate
        lower = upper
    raise AssertionError("unreachable")


def minimum_wage_exemptions() -> dict[tuple[int, int], tuple[Decimal, Decimal]]:
    """(year, month) -> (gelir vergisi, damga vergisi) exempt under GVK 23/1-18 and DVK 2 sayılı tablo IV-34.

    The income tax exemption is the tax on the minimum wage's own monthly base
    (gross - SGK - işsizlik), taken on its cumulative base for the year; it is
    granted in full even for part-month pay (GİB Ücret Geliri Vergi Rehberi 2024, 4.3).
    """
    out = {}
    for year in (2023, 2024):
        cum = Decimal(0)
        for month in range(1, 13):
            brut = rate_on(ASGARI, date(year, month, 1))
            base = brut - money(brut * SGK_ISCI) - money(brut * ISSIZLIK_ISCI)
            gv = money(income_tax(year, cum + base) - income_tax(year, cum))
            cum += base
            out[(year, month)] = (gv, money(brut * DAMGA))
    return out


def entitlement(kidem: int, age: int) -> int:
    """Yıllık ücretli izin days (İş K. md. 53)."""
    if kidem < 1:
        return 0
    days = 14 if kidem <= 5 else 20 if kidem < 15 else 26
    return max(days, 20) if age <= 18 or age >= 50 else days


# ---------------------------------------------------------------- entities

@dataclass
class Employee:
    branch: int
    dept: str
    unvan: str
    ad: str
    soyad: str
    cinsiyet: str
    tc: str
    dogum: date
    giris: date
    cikis: date | None
    legacy: bool
    salary: dict[str, Decimal] = field(default_factory=dict)   # period key -> monthly gross
    overtime_rate: float = 0.0                                  # expected sessions per month
    leaves: list[tuple[str, date, date]] = field(default_factory=list)
    overtime: list[tuple[date, Decimal]] = field(default_factory=list)
    personel_id: int = 0
    sicil: str = ""


def period_key(d: date) -> str:
    return "2024" if d.year == 2024 else "2023b" if d.month >= 7 else "2023a"


PERIOD_START = {"2023a": date(2023, 1, 1), "2023b": date(2023, 7, 1), "2024": date(2024, 1, 1)}


def check_places() -> None:
    doc = json.loads(IL_ILCE_PATH.read_text(encoding="utf-8"))
    known = {(i["il"], x["ilce"]) for i in doc["iller"] for x in i["ilceler"]}
    missing = [(il, ilce) for il, ilce, *_ in BRANCHES if (il, ilce) not in known]
    if missing:
        raise SystemExit(f"il/ilçe not found in {IL_ILCE_PATH}: {missing}")


def pick_role(rng: random.Random, dept: str) -> tuple:
    roles = ROLES[dept]
    return rng.choices(roles, [r[1] for r in roles])[0]


def make_salary(rng: random.Random, role: tuple, giris: date) -> dict[str, Decimal]:
    _, _, lo, hi, asgari_share = role
    if rng.random() < asgari_share:
        return {k: rate_on(ASGARI, start) for k, start in PERIOD_START.items()}
    seniority = 1 + min(0.2, years_between(giris, DATA_START) * 0.01) if giris < DATA_START else 1
    s2024 = max(round50(Decimal(str(rng.uniform(lo, hi) * seniority)) * rate_on(ASGARI, date(2024, 1, 1))),
                rate_on(ASGARI, date(2024, 1, 1)))
    s2023b = max(round50(s2024 / Decimal("1.40")), rate_on(ASGARI, date(2023, 7, 1)))
    s2023a = max(round50(s2023b / Decimal("1.25")), rate_on(ASGARI, date(2023, 1, 1)))
    return {"2023a": s2023a, "2023b": s2023b, "2024": s2024}


def weighted(rng: random.Random, table: dict[str, int]) -> str:
    return rng.choices(list(table), list(table.values()))[0]


def make_person(rng: random.Random, branch: int, dept: str, role: tuple,
                giris: date, used_tc: set[str]) -> Employee:
    cinsiyet = "K" if rng.random() < (0.45 if dept not in ("Üretim", "Lojistik") else 0.2) else "E"
    ad = weighted(rng, FEMALE_NAMES if cinsiyet == "K" else MALE_NAMES)
    soyad = weighted(rng, SURNAMES)
    r = rng.random()
    age = rng.randint(20, 32) if r < 0.7 else rng.randint(33, 45) if r < 0.92 else rng.randint(46, 56)
    age = min(age, years_between(OLDEST_BIRTH, giris))
    offset = rng.randint(0, 364)
    dogum = giris - timedelta(days=age * 365 + offset)
    if dogum < OLDEST_BIRTH:  # keep the age cap without piling births onto one day
        dogum = OLDEST_BIRTH + timedelta(days=offset)
    while True:
        tc = tckn(rng)
        if tc not in used_tc:
            used_tc.add(tc)
            break
    legacy = giris < LEGACY_BEFORE and rng.random() < LEGACY_RATE
    return Employee(branch, dept, role[0], ad, soyad, cinsiyet, tc, dogum, giris, None, legacy,
                    make_salary(rng, role, giris))


def build_people(rng: random.Random) -> list[Employee]:
    people: list[Employee] = []
    used_tc: set[str] = set()
    for b, (_, _, _, kind, opened, headcount) in enumerate(BRANCHES):
        mix = DEPT_MIX[kind]
        depts = list(mix)
        start = max(opened, date(1994, 3, 1))
        span = (date(2022, 12, 31) - start).days
        if kind == "GM":  # the executive team
            for unvan, mult in [("Genel Müdür", 12.0)] + [("Genel Müdür Yardımcısı", 8.5)] * 3:
                giris = date(2022, 12, 31) - timedelta(days=int(span * rng.uniform(0.3, 0.9)))
                e = make_person(rng, b, "Genel Müdürlük", (unvan, 0, mult, mult * 1.1, 0), giris, used_tc)
                people.append(e)
        # staff on 01.01.2023, mostly recent hires
        for _ in range(headcount):
            dept = rng.choices(depts, [mix[d] for d in depts])[0]
            giris = date(2022, 12, 31) - timedelta(days=int(span * rng.random() ** 2.2))
            people.append(make_person(rng, b, dept, pick_role(rng, dept), giris, used_tc))
        # hires during 2023-2024
        for _ in range(round(headcount * rng.uniform(0.18, 0.30))):
            dept = rng.choices(depts, [mix[d] for d in depts])[0]
            giris = rand_date(rng, DATA_START, date(2024, 12, 16))
            people.append(make_person(rng, b, dept, pick_role(rng, dept), giris, used_tc))
    for e in people:  # leavers
        if e.unvan == "Genel Müdür":
            continue
        if rng.random() < (0.20 if e.giris < DATA_START else 0.15):
            first = max(e.giris + timedelta(days=30), DATA_START)
            if first < DATA_END:
                e.cikis = rand_date(rng, first, DATA_END)
    return people


# ---------------------------------------------------------------- leave and overtime

def window(e: Employee, year: int) -> tuple[date, date] | None:
    a = max(e.giris, date(year, 1, 1))
    b = min(e.cikis or DATA_END, date(year, 12, 31))
    return (a, b) if a <= b else None


def place_leave(rng: random.Random, e: Employee, kind: str, days: int, a: date, b: date,
                busy: set[date]) -> bool:
    """Put a leave of `days` working days inside [a, b], avoiding busy days."""
    if days < 1:
        return False
    for _ in range(30):
        start = rand_date(rng, a, b)
        if start.weekday() >= 5:
            continue
        end, n = start, 0
        while True:
            if end.weekday() < 5:
                n += 1
            if n == days:
                break
            end += timedelta(days=1)
        span = [start + timedelta(days=i) for i in range((end - start).days + 1)]
        if end > b or any(d in busy for d in span):
            continue
        busy.update(span)
        e.leaves.append((kind, start, end))
        return True
    return False


def build_leaves(rng: random.Random, e: Employee, busy: set[date]) -> None:
    for year in (2023, 2024):
        w = window(e, year)
        if not w:
            continue
        a, b = w
        anniv = date(year, e.giris.month, min(e.giris.day, 28))
        kidem = years_between(e.giris, anniv)
        if kidem >= 1 and anniv <= b:
            days = round(entitlement(kidem, years_between(e.dogum, anniv)) * rng.uniform(0.4, 1.0))
            parts = [days] if days < 12 or rng.random() < 0.5 else [10 + rng.randint(0, days - 12), 0]
            if len(parts) == 2:
                parts[1] = days - parts[0]
            for p in parts:
                place_leave(rng, e, "YILLIK", p, a, b, busy)
        r = rng.random()
        for _ in range(2 if r < 0.10 else 1 if r < 0.45 else 0):
            place_leave(rng, e, "RAPOR", rng.choice([1, 1, 2, 2, 3, 3, 4, 5]) if rng.random() > 0.03
                        else rng.randint(10, 20), a, b, busy)
        if rng.random() < 0.12:
            place_leave(rng, e, "MAZERET", rng.choice([1, 2, 3, 3, 5]), a, b, busy)
        if rng.random() < 0.02:
            place_leave(rng, e, "UCRETSIZ", rng.randint(5, 15), a, b, busy)


WEEKDAY_HOURS = [("1", 3), ("1.5", 4), ("2", 6), ("2.5", 4), ("3", 5)]
SATURDAY_HOURS = [("4", 4), ("5", 3), ("6", 3), ("7.5", 6), ("8", 4), ("9", 2), ("10", 2), ("11", 1)]


def build_overtime(rng: random.Random, e: Employee, busy: set[date]) -> None:
    if e.dept not in OVERTIME_DEPTS or e.unvan in NO_OVERTIME_TITLES or rng.random() > 0.5:
        return
    r = rng.random()
    e.overtime_rate = rng.uniform(0.3, 2.5) if r < 0.85 else rng.uniform(2.5, 6.0) if r < 0.97 else rng.uniform(6.0, 9.5)
    m = date(max(e.giris, DATA_START).year, max(e.giris, DATA_START).month, 1)
    last = e.cikis or DATA_END
    while m <= last:
        a, b = max(m, e.giris), min(month_end(m), last)
        sessions = sum(rng.random() < e.overtime_rate / 20 for _ in range(20))
        for _ in range(sessions):
            saturday = rng.random() < 0.25
            for _ in range(15):
                d = rand_date(rng, a, b)
                if d not in busy and (d.weekday() == 5 if saturday else d.weekday() < 5):
                    busy.add(d)
                    pool = SATURDAY_HOURS if saturday else WEEKDAY_HOURS
                    e.overtime.append((d, Decimal(rng.choices([h for h, _ in pool], [w for _, w in pool])[0])))
                    break
        m = add_months(m, 1)


# ---------------------------------------------------------------- payroll

def month_days(e: Employee, m: date) -> int:
    """SGK days worked in month m: 30 for a full month, calendar days in a part month,
    minus days on sick report or unpaid leave."""
    first, last = m, month_end(m)
    a, b = max(first, e.giris), min(last, e.cikis or last)
    days = 30 if (a == first and b == last) else min(30, (b - a).days + 1)
    absent = 0
    for kind, s, t in e.leaves:
        if kind in ("RAPOR", "UCRETSIZ"):
            lo, hi = max(s, a), min(t, b)
            if lo <= hi:
                absent += (hi - lo).days + 1
    return max(0, days - absent)


def payroll(e: Employee, exemptions: dict) -> list[tuple]:
    """Monthly payslips: (period date, days, gross, overtime, sgk, işsizlik, gv, dv, net)."""
    rows = []
    cum = Decimal(0)
    ot_by_month: dict[date, Decimal] = {}
    for d, hours in e.overtime:
        key = date(d.year, d.month, 1)
        ot_by_month[key] = ot_by_month.get(key, Decimal(0)) + overtime_pay(e, d, hours)
    m = date(max(e.giris, DATA_START).year, max(e.giris, DATA_START).month, 1)
    last = e.cikis or DATA_END
    while m <= last:
        if m.month == 1:
            cum = Decimal(0)
        days = month_days(e, m)
        if days > 0:
            salary = e.salary[period_key(m)]
            gross = salary if days == 30 else money(salary * days / 30)
            ot = ot_by_month.get(m, Decimal("0.00"))
            total = gross + ot
            prime_base = min(total, money(rate_on(SGK_TAVAN, m) * days / 30))
            sgk, issizlik = money(prime_base * SGK_ISCI), money(prime_base * ISSIZLIK_ISCI)
            base = total - sgk - issizlik
            gv_full = money(income_tax(m.year, cum + base) - income_tax(m.year, cum))
            cum += base
            gv_ex, dv_ex = exemptions[(m.year, m.month)]
            gv = max(Decimal("0.00"), gv_full - gv_ex)
            dv = max(Decimal("0.00"), money(total * DAMGA) - dv_ex)
            rows.append((m, days, gross, ot, sgk, issizlik, gv, dv, total - sgk - issizlik - gv - dv))
        m = add_months(m, 1)
    return rows


def overtime_pay(e: Employee, d: date, hours: Decimal) -> Decimal:
    return money(hours * e.salary[period_key(d)] / AYLIK_SAAT * FAZLA_MESAI_ZAM)


# ---------------------------------------------------------------- build

def build() -> dict[str, list[tuple]]:
    check_places()
    rng = random.Random(SEED)
    people = build_people(rng)
    for e in people:
        busy: set[date] = set()
        build_leaves(rng, e, busy)
        build_overtime(rng, e, busy)

    # sicil numbers follow hire order within each hire year; personel_id is shuffled
    people.sort(key=lambda e: (e.giris, e.tc))
    seq: dict[int, int] = {}
    for e in people:
        seq[e.giris.year] = seq.get(e.giris.year, 0) + 1
        e.sicil = f"{e.giris.year}{seq[e.giris.year]:04d}"
    ids = list(range(1, len(people) + 1))
    rng.shuffle(ids)
    for e, pid in zip(people, ids):
        e.personel_id = pid
    people.sort(key=lambda e: e.personel_id)

    sube_rows = [(i, code, "Genel Müdürlük" if kind == "GM" else f"{ilce} Şubesi", il, ilce, opened.isoformat())
                 for i, (il, ilce, code, kind, opened, _) in enumerate(BRANCHES, start=1)]
    departman_rows = [(i, name) for i, name in enumerate(DEPARTMENTS, start=1)]
    dept_id = {name: i for i, name in departman_rows}

    personel_rows = []
    for e in people:
        ad, soyad, unvan = (tr_upper(e.ad), tr_upper(e.soyad), tr_upper(e.unvan)) if e.legacy else (e.ad, e.soyad, e.unvan)
        personel_rows.append((e.personel_id, e.sicil, ad, soyad, e.tc, e.cinsiyet, e.dogum.isoformat(),
                              e.giris.isoformat(), e.cikis.isoformat() if e.cikis else None, e.branch + 1,
                              dept_id[e.dept], unvan, "ESKI_AKTARIM" if e.legacy else "YENI"))

    exemptions = minimum_wage_exemptions()
    slips = sorted(((m, e.personel_id, row) for e in people for (m, *row) in payroll(e, exemptions)),
                   key=lambda t: (t[0], t[1]))
    bordro_rows = [(i, pid, f"{AYLAR[m.month - 1]} {m.year}", days, *(float(x) for x in money_cols))
                   for i, (m, pid, (days, *money_cols)) in enumerate(slips, start=1)]

    leaves = sorted((s, e.personel_id, kind, t) for e in people for kind, s, t in e.leaves)
    izin_rows = [(i, pid, kind, s.isoformat(), t.isoformat(), weekdays(s, t))
                 for i, (s, pid, kind, t) in enumerate(leaves, start=1)]

    sessions = sorted((d, e.personel_id, h, overtime_pay(e, d, h)) for e in people for d, h in e.overtime)
    mesai_rows = [(i, pid, d.isoformat(), tr_hours(h), float(pay))
                  for i, (d, pid, h, pay) in enumerate(sessions, start=1)]

    return {"sube": sube_rows, "departman": departman_rows, "personel": personel_rows,
            "bordro": bordro_rows, "izin": izin_rows, "fazla_mesai": mesai_rows}


def write_sqlite(tables: dict[str, list[tuple]]) -> None:
    if DB_PATH.exists():
        DB_PATH.unlink()
    con = sqlite3.connect(DB_PATH)
    con.execute("PRAGMA foreign_keys = ON")
    # NUMERIC affinity would store whole amounts as integers (45100.00 -> 45100), so `x * rate / 100`
    # would do integer division on SQLite only; REAL keeps every amount a float, like NUMERIC in Postgres
    con.executescript(re.sub(r"NUMERIC\(\d+,\s*\d+\)", "REAL", SCHEMA_PATH.read_text(encoding="utf-8")))
    for name, rows in tables.items():
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
