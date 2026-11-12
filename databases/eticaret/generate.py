"""Generate eticaret.sqlite: orders of a fictional single online home and lifestyle shop.

One seller with two warehouses. Orders run from 01.01.2023 to 31.12.2024 and
the data is extracted on 05.01.2025. Customers and addresses created before
the shop's platform move on 01.09.2022 came over from the old platform in
upper case.

Deterministic: a fixed `random` seed yields identical data on every run.
Re-running overwrites the existing database.

    python databases/eticaret/generate.py
"""
from __future__ import annotations

import bisect
import itertools
import json
import math
import random
import re
import sqlite3
from dataclasses import dataclass, field
from datetime import date, timedelta
from decimal import ROUND_HALF_UP, Decimal
from pathlib import Path

SEED = 20250107
HERE = Path(__file__).resolve().parent
ROOT = HERE.parent.parent
DB_PATH = HERE / "eticaret.sqlite"
SCHEMA_PATH = HERE / "schema.sql"
IL_ILCE_PATH = ROOT / "common" / "il_ilce.json"

DATA_START = date(2023, 1, 1)
DATA_END = date(2024, 12, 31)      # last order date
SNAPSHOT = date(2025, 1, 5)        # extraction date
SHOP_OPEN = date(2017, 3, 1)
MIGRATION = date(2022, 9, 1)       # platform move: older customers and addresses came over in upper case

# KDV rates (Karar 2007/13033 as amended; TÜRMOB consolidated list of 10.07.2023). Karar 7346
# (RG 07.07.2023) raised the general rate 18% -> 20% and list (II) 8% -> 10% from 10.07.2023;
# list (I) stayed at 1%. List (I): food chapters 9 (coffee, tea), 15 (oils), 18 (chocolate),
# 19 (pasta), honey. List (II): clothing and home textiles (item 5), footwear (7), bags (8),
# books (14). Electronics, appliances and kitchenware are on neither list.
KDV_CHANGE = date(2023, 7, 10)
KDV = {"GENEL": (18, 20), "II": (8, 10), "I": (1, 1)}

# Shop policy: shipping fee charged to the customer, free at or above the threshold
# (product total after the coupon).
SHIPPING = [(date(2024, 1, 1), Decimal("69.90"), Decimal(750)), (date(2023, 1, 1), Decimal("39.90"), Decimal(400))]

# Cayma hakkı (Mesafeli Sözleşmeler Yönetmeliği md. 9): 14 days from the day the goods are received.
CAYMA_GUN = 14

TWO = Decimal("0.01")


def money(x) -> Decimal:
    return Decimal(str(x)).quantize(TWO, rounding=ROUND_HALF_UP)


def shelf_price(x: float) -> Decimal:
    """Round to a shelf price: 79.90, 449.90, 1299.90."""
    if x < 100:
        return Decimal(int(x)) + Decimal("0.90")
    step = 10 if x < 1000 else 50
    return Decimal(max(1, round(x / step)) * step) - Decimal("0.10")


def tr_upper(s: str) -> str:
    """Turkish-correct upper case: i -> İ, ı -> I."""
    return s.replace("i", "İ").replace("ı", "I").upper()


def tr_money(x: Decimal) -> str:
    """Decimal('1299.9') -> '1.299,90'."""
    return f"{x:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")


def rand_date(rng: random.Random, a: date, b: date) -> date:
    return a + timedelta(days=rng.randint(0, (b - a).days))


def days(a: date, b: date):
    return (a + timedelta(days=i) for i in range((b - a).days + 1))


# ---------------------------------------------------------------- catalogue

@dataclass
class Cat:
    name: str
    n: int
    price: tuple[int, int]          # shelf price range at the snapshot, KDV included
    kg: tuple[float, float]
    dims: tuple[int, int, int]      # typical package en, boy, yükseklik (cm)
    pop: float
    season: str | None
    names: list[str]
    variant: str                    # 'model' | 'renk' | '' (none) | list name in VARIANTS


@dataclass
class Ana:
    name: str
    group: str                      # KDV key
    prefix: str
    iade_orani: float
    brands: list[str]
    cats: list[Cat]


COLORS = ["Siyah", "Beyaz", "Lacivert", "Gri", "Bej", "Haki", "Bordo", "Mavi", "Yeşil", "Kırmızı", "Antrasit", "Ekru"]
VARIANTS = {"kahve": ["Kolombiya", "Brezilya", "Etiyopya", "Guatemala", "Kenya"],
            "zeytin": ["Ayvalık", "Edremit", "Milas", "Gemlik", "Akhisar"],
            "metal": ["Mat", "Parlak", "Altın", "Siyah"],
            "boyut": ["50x70", "40x60", "60x80"]}

CATALOGUE = [
    Ana("Elektronik", "GENEL", "ELK", 0.05, ["Voltis", "Sesra", "Teknova", "Dalgalı", "Işıkpro"], [
        Cat("Kulaklık", 30, (350, 4500), (0.2, 0.6), (18, 15, 8), 10, None,
            ["Kablosuz Kulak İçi Kulaklık", "Bluetooth Kulak Üstü Kulaklık", "Aktif Gürültü Engelleyici Kulaklık",
             "Oyuncu Kulaklığı", "Kablolu Mikrofonlu Kulaklık", "Sporcu Kulaklığı"], "model"),
        Cat("Akıllı Saat", 15, (1200, 9000), (0.2, 0.5), (12, 10, 8), 5, None,
            ["Akıllı Saat", "Akıllı Bileklik", "Sporcu Akıllı Saati", "Çocuk Akıllı Saati"], "model"),
        Cat("Powerbank", 15, (350, 1600), (0.3, 0.6), (16, 10, 4), 6, None,
            ["Powerbank 10000 mAh", "Powerbank 20000 mAh", "Hızlı Şarj Powerbank 10000 mAh",
             "Kablosuz Powerbank 5000 mAh"], "model"),
        Cat("Bluetooth Hoparlör", 15, (500, 5000), (0.4, 1.5), (22, 12, 12), 4, None,
            ["Taşınabilir Bluetooth Hoparlör", "Su Geçirmez Bluetooth Hoparlör", "Mini Bluetooth Hoparlör",
             "Parti Hoparlörü"], "model"),
        Cat("Şarj Aleti", 15, (150, 900), (0.1, 0.3), (12, 8, 5), 7, None,
            ["Hızlı Şarj Adaptörü 20W", "Hızlı Şarj Adaptörü 65W", "Kablosuz Şarj Cihazı", "Araç Şarj Cihazı",
             "Type-C Şarj Kablosu"], "model"),
    ]),
    Ana("Küçük Ev Aletleri", "GENEL", "KEA", 0.05, ["Evora", "Kıvılcım", "Pratika", "Lumen", "Arıcan"], [
        Cat("Kahve Makinesi", 15, (1500, 15000), (2.0, 6.0), (35, 25, 35), 4, None,
            ["Filtre Kahve Makinesi", "Türk Kahvesi Makinesi", "Espresso Makinesi", "Kapsüllü Kahve Makinesi"], "model"),
        Cat("Süpürge", 15, (2500, 22000), (3.0, 8.0), (60, 35, 30), 4, None,
            ["Toz Torbasız Süpürge", "Dikey Şarjlı Süpürge", "Robot Süpürge", "Islak Kuru Süpürge"], "model"),
        Cat("Ütü", 12, (800, 4500), (1.2, 2.5), (32, 16, 18), 4, None,
            ["Buharlı Ütü", "Buhar Kazanlı Ütü", "Seyahat Ütüsü", "Dikey Buharlı Ütü"], "model"),
        Cat("Blender", 12, (700, 4000), (1.5, 3.5), (25, 22, 40), 3, None,
            ["El Blender Seti", "Smoothie Blender", "Doğrayıcılı Blender", "Vakumlu Blender"], "model"),
        Cat("Tost Makinesi", 10, (900, 3500), (2.0, 4.0), (35, 30, 15), 3, None,
            ["Tost Makinesi", "Izgaralı Tost Makinesi", "Sandviç Tost Makinesi"], "model"),
        Cat("Su Isıtıcı", 12, (450, 2500), (1.0, 1.8), (25, 20, 28), 4, None,
            ["Su Isıtıcı 1,7 L", "Çelik Su Isıtıcı", "Cam Su Isıtıcı", "Sıcaklık Ayarlı Su Isıtıcı"], "model"),
        Cat("Saç Kurutma Makinesi", 10, (500, 3800), (0.6, 1.2), (28, 22, 10), 3, None,
            ["Saç Kurutma Makinesi", "İyonik Saç Kurutma Makinesi", "Katlanabilir Saç Kurutma Makinesi"], "model"),
    ]),
    Ana("Mutfak", "GENEL", "MTF", 0.03, ["Çini Ev", "Granitaş", "Sofra Sanat", "Bakırhan", "Kaşıkçı"], [
        Cat("Tencere", 15, (500, 6000), (1.5, 7.0), (30, 30, 25), 5, None,
            ["Granit Tencere Seti 7 Parça", "Çelik Tencere Seti 9 Parça", "Düdüklü Tencere 6 L",
             "Döküm Tencere 24 cm", "Granit Tencere 20 cm"], "renk"),
        Cat("Tava", 12, (350, 2500), (0.8, 2.2), (45, 28, 8), 5, None,
            ["Granit Tava 26 cm", "Döküm Tava 28 cm", "Wok Tava 30 cm", "Krep Tavası 24 cm", "Izgara Tavası 28 cm"], "renk"),
        Cat("Çaydanlık", 12, (450, 3200), (1.0, 2.5), (24, 24, 30), 5, None,
            ["Çelik Çaydanlık Takımı", "Emaye Çaydanlık", "Porselen Demlikli Çaydanlık", "Cam Demlikli Çaydanlık"], "renk"),
        Cat("Bardak", 12, (120, 900), (0.6, 2.5), (30, 20, 15), 4, None,
            ["Çay Bardağı 6’lı", "Su Bardağı 6’lı", "Kahve Fincanı Takımı 6’lı", "Kupa 4’lü"], "renk"),
        Cat("Tabak", 12, (200, 1800), (1.5, 6.0), (30, 30, 15), 3, None,
            ["Yemek Takımı 24 Parça", "Servis Tabağı", "Kahvaltı Tabağı 6’lı", "Pasta Tabağı 6’lı"], "renk"),
        Cat("Çatal Bıçak Takımı", 8, (450, 4000), (1.0, 3.5), (40, 30, 8), 2, None,
            ["Çatal Bıçak Takımı 36 Parça", "Çatal Bıçak Takımı 84 Parça", "Tatlı Çatalı 6’lı"], "metal"),
        Cat("Saklama Kabı", 10, (120, 800), (0.4, 1.5), (25, 20, 15), 4, None,
            ["Cam Saklama Kabı 3’lü", "Vakumlu Saklama Kabı Seti", "Erzak Saklama Kabı 5’li",
             "Sızdırmaz Saklama Kabı 1 L"], "renk"),
    ]),
    Ana("Ev Tekstili", "II", "TKS", 0.05, ["Keten Ev", "Pamukça", "Lale Home", "Nergis", "Yumuşak Düş"], [
        Cat("Nevresim Takımı", 15, (600, 3500), (1.2, 2.5), (40, 30, 10), 4, None,
            ["Çift Kişilik Nevresim Takımı", "Tek Kişilik Nevresim Takımı", "Ranforce Nevresim Takımı",
             "Saten Nevresim Takımı"], "renk"),
        Cat("Havlu", 12, (120, 900), (0.3, 1.2), (30, 25, 8), 4, None,
            ["Banyo Havlusu", "El Yüz Havlusu", "Plaj Havlusu", "Bornoz"], "renk"),
        Cat("Battaniye", 10, (450, 2500), (1.5, 3.0), (45, 35, 15), 3, "kis",
            ["Welsoft Battaniye", "Pamuklu Battaniye", "Tek Kişilik Battaniye", "Çift Kişilik Battaniye"], "renk"),
        Cat("Yastık", 10, (200, 1200), (0.6, 1.5), (50, 70, 15), 3, None,
            ["Visco Yastık", "Elyaf Yastık", "Ortopedik Yastık", "Kaz Tüyü Yastık"], "boyut"),
        Cat("Perde", 10, (350, 2500), (1.0, 3.0), (40, 30, 10), 2, None,
            ["Fon Perde", "Tül Perde", "Stor Perde", "Karartma Perde"], "renk"),
    ]),
    Ana("Giyim", "II", "GYM", 0.13, ["Rüzgar", "Lacivert", "Ayaz", "Pamukçu", "Kuzey"], [
        Cat("Tişört", 25, (180, 900), (0.2, 0.35), (30, 25, 3), 7, "yaz",
            ["Basic Tişört", "Oversize Tişört", "Polo Yaka Tişört", "Baskılı Tişört", "V Yaka Tişört"], "renk"),
        Cat("Kazak", 20, (400, 1800), (0.4, 0.8), (35, 28, 6), 5, "kis",
            ["Balıkçı Yaka Kazak", "Bisiklet Yaka Kazak", "Triko Kazak", "Yün Kazak"], "renk"),
        Cat("Mont", 15, (1200, 6500), (0.9, 2.0), (45, 35, 10), 4, "kis",
            ["Şişme Mont", "Kapüşonlu Mont", "Su Geçirmez Mont", "Kaban"], "renk"),
        Cat("Pantolon", 20, (450, 2000), (0.4, 0.8), (35, 28, 5), 5, None,
            ["Kot Pantolon", "Chino Pantolon", "Kumaş Pantolon", "Kargo Pantolon"], "renk"),
        Cat("Çorap", 15, (50, 300), (0.05, 0.3), (20, 15, 3), 6, None,
            ["Pamuklu Çorap 3’lü", "Patik Çorap 5’li", "Kışlık Yün Çorap", "Spor Çorap 3’lü"], "renk"),
        Cat("Eşofman", 12, (500, 2500), (0.5, 1.0), (35, 30, 8), 4, None,
            ["Eşofman Takımı", "Eşofman Altı", "Kapüşonlu Eşofman Üstü"], "renk"),
    ]),
    Ana("Ayakkabı ve Çanta", "II", "AYK", 0.11, ["Adımlar", "Yürük", "Patika", "Deri Sanat", "Çantacı"], [
        Cat("Spor Ayakkabı", 20, (1000, 5500), (0.7, 1.3), (33, 22, 12), 6, None,
            ["Koşu Ayakkabısı", "Günlük Spor Ayakkabı", "Yürüyüş Ayakkabısı", "Basketbol Ayakkabısı"], "renk"),
        Cat("Bot", 12, (1500, 6500), (1.0, 2.0), (35, 25, 14), 3, "kis",
            ["Deri Bot", "Su Geçirmez Bot", "Postal Bot", "Kar Botu"], "renk"),
        Cat("Terlik", 10, (150, 800), (0.3, 0.6), (30, 15, 10), 3, "yaz",
            ["Ev Terliği", "Plaj Terliği", "Ortopedik Terlik"], "renk"),
        Cat("Sırt Çantası", 12, (350, 2500), (0.4, 1.2), (45, 30, 15), 4, "okul",
            ["Laptop Sırt Çantası", "Okul Çantası", "Outdoor Sırt Çantası", "Günlük Sırt Çantası"], "renk"),
        Cat("Valiz", 10, (1500, 7500), (2.5, 5.0), (70, 45, 28), 2, "yaz",
            ["Kabin Boy Valiz", "Orta Boy Valiz", "Büyük Boy Valiz", "Valiz Seti 3’lü"], "renk"),
    ]),
    Ana("Kitap", "II", "KTP", 0.01, ["Çınaraltı Yayınları", "Kırlangıç Kitap", "Ilgın Yayınevi", "Pusula Kitap",
                                     "Ada Yayınları"], [
        Cat("Roman", 40, (120, 450), (0.3, 0.7), (20, 14, 3), 6, None, [], "kitap"),
        Cat("Çocuk Kitabı", 30, (80, 350), (0.2, 0.6), (24, 20, 2), 5, "okul", [], "kitap"),
        Cat("Kişisel Gelişim", 20, (120, 400), (0.3, 0.5), (20, 14, 2), 3, None, [], "kitap"),
        Cat("Tarih", 20, (150, 600), (0.4, 0.9), (22, 15, 3), 2, None, [], "kitap"),
    ]),
    Ana("Gurme Gıda", "I", "GDA", 0.006, ["Ege Bahçe", "Karadeniz Hasadı", "Kervansaray", "Arıkovanı",
                                          "Anadolu Lezzet"], [
        Cat("Türk Kahvesi", 10, (90, 400), (0.1, 0.5), (12, 8, 15), 4, None,
            ["Türk Kahvesi 100 g", "Türk Kahvesi 250 g", "Dibek Kahvesi 250 g", "Menengiç Kahvesi 250 g"], ""),
        Cat("Filtre Kahve", 10, (180, 750), (0.25, 1.0), (15, 8, 22), 3, None,
            ["Filtre Kahve 250 g", "Filtre Kahve 500 g", "Çekirdek Kahve 1 kg", "Espresso Çekirdek 500 g"], "kahve"),
        Cat("Çay", 12, (120, 550), (0.5, 1.1), (20, 10, 25), 5, None,
            ["Siyah Çay 1 kg", "Rize Turist Çayı 500 g", "Earl Grey Çay 500 g", "Yeşil Çay 100 g",
             "Organik Çay 1 kg"], ""),
        Cat("Zeytinyağı", 12, (350, 1600), (0.6, 5.0), (10, 10, 30), 4, None,
            ["Natürel Sızma Zeytinyağı 1 L", "Natürel Sızma Zeytinyağı 5 L", "Erken Hasat Zeytinyağı 500 ml",
             "Riviera Zeytinyağı 2 L"], "zeytin"),
        Cat("Çikolata", 15, (50, 350), (0.1, 0.5), (18, 10, 3), 5, "kis",
            ["Bitter Çikolata 80 g", "Sütlü Çikolata 80 g", "Antep Fıstıklı Çikolata 150 g",
             "Spesiyal Çikolata Kutusu 250 g"], ""),
        Cat("Makarna", 10, (25, 120), (0.5, 1.0), (25, 10, 5), 3, None,
            ["Spagetti 500 g", "Burgu Makarna 500 g", "Tam Buğday Makarna 500 g", "Erişte 500 g"], ""),
        Cat("Bal", 10, (280, 1300), (0.5, 1.1), (10, 10, 15), 3, None,
            ["Çiçek Balı 850 g", "Kestane Balı 460 g", "Süzme Çam Balı 850 g", "Petek Bal 400 g"], ""),
    ]),
]

BOOK_WORDS = {
    "Roman": (["Sessiz", "Kayıp", "Son", "Uzak", "Kırık", "Mavi", "Eski", "Gizli", "Yalnız", "Unutulmuş",
               "Kuzey", "Gece"],
              ["Liman", "Bahçe", "Mektup", "Yolculuk", "Ada", "Şehir", "Saat", "Köprü", "Ayna", "Rüzgâr", "Ev",
               "Kervan"]),
    "Çocuk Kitabı": (["Minik", "Meraklı", "Uçan", "Cesur", "Uykucu", "Neşeli", "Küçük", "Sihirli"],
                     ["Ayıcık", "Balon", "Kirpi", "Kaplumbağa", "Bulut", "Tavşan", "Fil", "Dede"]),
    "Kişisel Gelişim": (["Zihnin", "Sabahın", "Sessizliğin", "Alışkanlıkların", "Odaklanmanın", "Zamanın",
                         "Değişimin"],
                        ["Gücü", "Sırrı", "Dili", "Yolu", "Sanatı", "Kitabı"]),
    "Tarih": (["Osmanlı’nın", "Bizans’ın", "Selçuklu’nun", "Anadolu’nun", "İpek Yolu’nun", "Akdeniz’in",
               "Cumhuriyet’in"],
              ["Son Yüzyılı", "Kuruluşu", "Kayıp Şehirleri", "Ticaret Yolları", "İlk Yılları", "Unutulan Savaşları"]),
}

# Common Turkish names with rough frequency weights, as in ik_bordro (plus a few names common among younger customers).
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
    "Yiğit": 3, "Efe": 3, "Mert": 3, "Ege": 2, "Alp": 2, "Doruk": 1,
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
    "Defne": 3, "Eylül": 3, "Ecrin": 2, "Asya": 2, "Miray": 2, "Nehir": 2,
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

# ---------------------------------------------------------------- calendar

INFLATION = {2023: 0.042, 2024: 0.031}   # rough monthly price drift, only for realism
# Black Friday ("Efsane Cuma"), 11.11, the week before Mother's Day, Valentine's week;
# Ramazan and Kurban bayramı days (fewer orders).
PEAKS = {date(2023, 11, 24): 3.0, date(2024, 11, 29): 3.0, date(2023, 11, 11): 2.0, date(2024, 11, 11): 2.0}
BAYRAM = [(date(2023, 4, 21), date(2023, 4, 23)), (date(2023, 6, 28), date(2023, 7, 1)),
          (date(2024, 4, 10), date(2024, 4, 12)), (date(2024, 6, 16), date(2024, 6, 19))]
MONTH_FACTOR = [0.95, 0.85, 0.95, 0.95, 1.0, 0.9, 0.85, 0.95, 1.05, 1.0, 1.55, 1.25]
WEEKDAY_FACTOR = [1.1, 1.05, 1.0, 1.0, 1.0, 0.85, 1.0]
SEASON = {"kis": [1.8, 1.5, 1.0, 0.6, 0.4, 0.3, 0.3, 0.4, 0.7, 1.2, 1.8, 2.0],
          "yaz": [0.5, 0.5, 0.8, 1.0, 1.5, 1.9, 1.9, 1.6, 0.9, 0.6, 0.5, 0.5],
          "okul": [0.9, 0.9, 0.9, 0.9, 0.9, 0.9, 0.9, 2.0, 2.2, 1.0, 0.9, 0.9]}


def price_index(d: date) -> float:
    m = (d.year - 2023) * 12 + d.month - 1 + (d.day - 1) / 31
    m2023 = min(m, 12)
    return (1 + INFLATION[2023]) ** m2023 * (1 + INFLATION[2024]) ** max(0.0, m - 12)


def order_intensity(d: date) -> float:
    f = 1 + 0.45 * (d - DATA_START).days / 730
    f *= MONTH_FACTOR[d.month - 1] * WEEKDAY_FACTOR[d.weekday()] * PEAKS.get(d, 1.0)
    mothers_day = {2023: date(2023, 5, 14), 2024: date(2024, 5, 12)}[d.year]
    if 1 <= (mothers_day - d).days <= 6 or (d.month == 2 and 7 <= d.day <= 13):
        f *= 1.3
    if any(a <= d <= b for a, b in BAYRAM):
        f *= 0.7
    return f


# ---------------------------------------------------------------- places

def load_places():
    doc = json.loads(IL_ILCE_PATH.read_text(encoding="utf-8"))
    flat, by_il = [], {}
    for il in doc["iller"]:
        rows = [(il["il"], x["ilce"], il["bolge"], x["nufus_2025"] or 1) for x in il["ilceler"]]
        by_il[il["il"]] = rows
        flat += rows
    return flat, by_il


DEPOT_OF_REGION = {"Marmara": "İstanbul Deposu", "Ege": "İstanbul Deposu", "Akdeniz": "İstanbul Deposu",
                   "İç Anadolu": "Ankara Deposu", "Karadeniz": "Ankara Deposu", "Doğu Anadolu": "Ankara Deposu",
                   "GAB": "Ankara Deposu"}
TRANSIT = {("İstanbul Deposu", "Marmara"): ([1, 2], [70, 30]),
           ("İstanbul Deposu", "Ege"): ([1, 2, 3], [30, 50, 20]),
           ("İstanbul Deposu", "Akdeniz"): ([1, 2, 3], [25, 50, 25]),
           ("Ankara Deposu", "İç Anadolu"): ([1, 2], [70, 30]),
           ("Ankara Deposu", "Karadeniz"): ([1, 2, 3], [25, 50, 25]),
           ("Ankara Deposu", "Doğu Anadolu"): ([2, 3, 4, 5], [30, 40, 20, 10]),
           ("Ankara Deposu", "GAB"): ([2, 3, 4, 5], [35, 40, 18, 7])}

CARRIERS = {"Kanat Kargo": (45, Decimal("1.00")), "Pusula Kargo": (35, Decimal("0.94")),
            "Ulak Kargo": (20, Decimal("1.07"))}
# Carrier tariff to the shop, KDV excluded: (max billable desi/kg, price) and the period multipliers.
TARIFF = [(1, 38), (2, 42), (3, 47), (5, 55), (10, 75), (15, 98), (20, 120), (30, 160)]
TARIFF_PERIODS = [(date(2024, 7, 1), Decimal("1.90")), (date(2024, 1, 1), Decimal("1.65")),
                  (date(2023, 7, 1), Decimal("1.30")), (date(2023, 1, 1), Decimal("1.00"))]


def carrier_cost(billable: float, shipped: date, carrier: str) -> Decimal:
    units = math.ceil(billable)
    base = next((p for top, p in TARIFF if units <= top), None)
    if base is None:
        base = 160 + 5 * (units - 30)
    period = next(m for start, m in TARIFF_PERIODS if shipped >= start)
    return money(Decimal(base) * period * CARRIERS[carrier][1])


# ---------------------------------------------------------------- entities

@dataclass
class Product:
    ana: Ana
    cat: Cat
    kategori_id: int
    stok_kodu: str
    adi: str
    marka: str
    kg: Decimal
    dims: tuple[int, int, int]
    eklenme: date
    son_satis: date | None            # discontinued after this day
    pop: float
    steps: list[tuple[date, Decimal]] = field(default_factory=list)
    kampanya: dict[int, float] = field(default_factory=dict)   # year -> November discount
    urun_id: int = 0

    def price_on(self, d: date) -> Decimal:
        i = bisect.bisect_right([s for s, _ in self.steps], d) - 1
        return self.steps[max(i, 0)][1]

    def unit_price(self, d: date) -> Decimal:
        p = self.price_on(d)
        disc = self.kampanya.get(d.year, 0.0) if d.month == 11 else 0.0
        return shelf_price(float(p) * (1 - disc)) if disc else p

    def kdv(self, d: date) -> int:
        return KDV[self.ana.group][0 if d < KDV_CHANGE else 1]


def product_name(rng: random.Random, ana: Ana, cat: Cat, brand: str, used: set[str]) -> str:
    for attempt in range(50):
        if cat.variant == "kitap":
            first, second = BOOK_WORDS[cat.name]
            name = f"{rng.choice(first)} {rng.choice(second)}"
        else:
            desc = rng.choice(cat.names)
            if cat.variant == "model":
                code = "".join(rng.choice("ABCDEFGHKLMNPRSTXZ") for _ in range(rng.randint(1, 2)))
                name = f"{brand} {desc} {code}{rng.choice(range(10, 1000, 10))}"
            elif cat.variant == "renk":
                name = f"{brand} {desc} {rng.choice(COLORS)}"
            elif cat.variant in VARIANTS:
                name = f"{brand} {desc} {rng.choice(VARIANTS[cat.variant])}"
            else:
                name = f"{brand} {desc}"
        if name not in used:
            used.add(name)
            return name
        if attempt > 20 and cat.variant in ("", "kitap"):
            name = f"{name} {attempt - 18}" if cat.variant == "" else f"{name} {attempt - 19}. Kitap"
            if name not in used:
                used.add(name)
                return name
    raise AssertionError(f"no free name in {cat.name}")


def build_catalogue(rng: random.Random) -> tuple[list[tuple], list[Product]]:
    kategori_rows, products, used = [], [], set()
    kid = 0
    for ana in CATALOGUE:
        for cat in ana.cats:
            kid += 1
            kategori_rows.append((kid, cat.name, ana.name))
            ranks = list(range(1, cat.n + 1))
            rng.shuffle(ranks)
            for rank in ranks:
                brand = rng.choice(ana.brands)
                name = product_name(rng, ana, cat, brand, used)
                kg = Decimal(str(round(rng.uniform(*cat.kg), 2)))
                dims = tuple(max(2, round(x * rng.uniform(0.8, 1.25))) for x in cat.dims)
                new = rng.random() < 0.3
                eklenme = rand_date(rng, DATA_START, date(2024, 11, 30)) if new else rand_date(rng, SHOP_OPEN, date(2022, 12, 31))
                son = None
                first_stop = max(eklenme + timedelta(days=60), date(2023, 3, 1))
                if rng.random() < 0.08 and first_stop < date(2024, 11, 30):
                    son = rand_date(rng, first_stop, date(2024, 11, 30))
                p = Product(ana, cat, kid, "", name, brand, kg, dims, eklenme, son, (1 / rank ** 0.8))
                target = math.exp(rng.uniform(math.log(cat.price[0]), math.log(cat.price[1])))
                build_prices(rng, p, target)
                for year in (2023, 2024):
                    if rng.random() < 0.35:
                        p.kampanya[year] = rng.choice([0.10, 0.15, 0.15, 0.20, 0.25])
                products.append(p)
    # stock codes: category prefix + running number in order of listing
    seq: dict[str, int] = {}
    for p in sorted(products, key=lambda p: (p.eklenme, p.adi)):
        seq[p.ana.prefix] = seq.get(p.ana.prefix, 0) + 1
        p.stok_kodu = f"{p.ana.prefix}-{seq[p.ana.prefix]:04d}"
    products.sort(key=lambda p: (p.kategori_id, p.stok_kodu))
    for i, p in enumerate(products, start=1):
        p.urun_id = i
    return kategori_rows, products


def build_prices(rng: random.Random, p: Product, target: float) -> None:
    """Step prices from the product's first sale day; reprices every 6-16 weeks and on the KDV change."""
    end = p.son_satis or DATA_END
    d = max(p.eklenme, DATA_START)
    stops = []
    while d <= end:
        stops.append(d)
        d += timedelta(days=rng.randint(40, 110))
    if p.ana.group != "I" and stops[0] < KDV_CHANGE <= end:
        stops = sorted(set(stops) | {KDV_CHANGE})
    end_index = price_index(DATA_END)
    for s in stops:
        if s == KDV_CHANGE and p.steps:
            old, new = KDV[p.ana.group]
            price = shelf_price(float(p.steps[-1][1]) * (100 + new) / (100 + old))
        else:
            price = shelf_price(target * price_index(s) / end_index * rng.uniform(0.96, 1.04))
        p.steps.append((s, price))


@dataclass
class Address:
    created: date
    sehir: str
    ilce: str
    bolge: str
    baslik: str
    legacy: bool
    adres_id: int = 0


@dataclass
class Customer:
    kayit: date
    ad: str
    soyad: str
    cinsiyet: str | None
    dogum: date | None
    izin: int
    weight: float
    addresses: list[Address] = field(default_factory=list)
    musteri_id: int = 0
    n_orders: int = 0

    @property
    def legacy(self) -> bool:
        return self.kayit < MIGRATION


REG_YEARS = {2017: 0.03, 2018: 0.06, 2019: 0.08, 2020: 0.14, 2021: 0.13, 2022: 0.14, 2023: 0.19, 2024: 0.23}
N_CUSTOMERS = 21000


def pick_place(rng: random.Random, flat, cum):
    return flat[bisect.bisect_right(cum, rng.random() * cum[-1])]


def build_customers(rng: random.Random, flat, by_il) -> list[Customer]:
    cum = list(itertools.accumulate(w for *_, w in flat))
    il_cum = {il: list(itertools.accumulate(w for *_, w in rows)) for il, rows in by_il.items()}
    years = list(REG_YEARS)
    out = []
    for _ in range(N_CUSTOMERS):
        y = rng.choices(years, list(REG_YEARS.values()))[0]
        kayit = rand_date(rng, max(date(y, 1, 1), SHOP_OPEN), date(y, 12, 31))
        cinsiyet = rng.choices(["K", "E", None], [52, 44, 4])[0]
        names = FEMALE_NAMES if cinsiyet == "K" else MALE_NAMES if cinsiyet == "E" else rng.choice([FEMALE_NAMES, MALE_NAMES])
        ad = rng.choices(list(names), list(names.values()))[0]
        soyad = rng.choices(list(SURNAMES), list(SURNAMES.values()))[0]
        dogum = None
        if rng.random() < 0.65:
            r = rng.random()
            age = rng.randint(19, 30) if r < 0.4 else rng.randint(31, 45) if r < 0.8 else rng.randint(46, 72)
            dogum = date(2024, 12, 31) - timedelta(days=age * 365 + rng.randint(0, 364))
            if (kayit - dogum).days < 18 * 365 + 5:
                dogum = kayit - timedelta(days=18 * 365 + 5 + rng.randint(0, 2000))
        dormant = rng.random() < (0.40 if kayit < MIGRATION else 0.25)
        weight = 0.0 if dormant else rng.lognormvariate(0, 0.9)
        c = Customer(kayit, ad, soyad, cinsiyet, dogum, int(rng.random() < 0.55), weight)
        il, ilce, bolge, _ = pick_place(rng, flat, cum)
        c.addresses.append(Address(kayit, il, ilce, bolge, rng.choices(["Ev", "İş", "Evim"], [85, 10, 5])[0],
                                   kayit < MIGRATION))
        for extra in (0.30, 0.08):
            if rng.random() < extra and (DATA_END - kayit).days > 60:
                created = rand_date(rng, kayit + timedelta(days=30), DATA_END)
                if rng.random() < 0.6:
                    rows = by_il[il]
                    il2, ilce2, bolge2, _ = rows[bisect.bisect_right(il_cum[il], rng.random() * il_cum[il][-1])]
                else:
                    il2, ilce2, bolge2, _ = pick_place(rng, flat, cum)
                baslik = rng.choices(["İş", "Annemler", "Yazlık", "Ev"], [50, 20, 10, 20])[0]
                c.addresses.append(Address(created, il2, ilce2, bolge2, baslik, created < MIGRATION))
        out.append(c)
    out.sort(key=lambda c: (c.kayit, c.soyad, c.ad))
    for i, c in enumerate(out, start=1):
        c.musteri_id = i
    return out


# ---------------------------------------------------------------- orders

@dataclass
class Line:
    product: Product
    adet: int
    birim: Decimal
    kdv: int
    kalem_id: int = 0


@dataclass
class Order:
    d: date
    customer: Customer
    address: Address
    lines: list[Line]
    kanal: str = ""
    odeme: str = ""
    taksit: int = 1
    kupon: str | None = None
    toplam: Decimal = Decimal(0)
    indirim: Decimal = Decimal(0)
    kargo_ucreti: Decimal = Decimal(0)
    durum: str = ""
    shipment: tuple | None = None
    siparis_id: int = 0


CHEAP = {"Çorap", "Makarna", "Çikolata", "Türk Kahvesi", "Çay", "Havlu", "Bardak", "Saklama Kabı", "Şarj Aleti"}


def month_pickers(products: list[Product]):
    """Per (year, month): products on sale for the whole month with cumulative weights."""
    out = {}
    for y, m in itertools.product((2023, 2024), range(1, 13)):
        first = date(y, m, 1)
        last = (date(y + (m == 12), m % 12 + 1, 1) - timedelta(days=1))
        cat_size = {}
        for p in products:
            cat_size[p.cat.name] = cat_size.get(p.cat.name, 0.0) + p.pop
        avail, weights = [], []
        for p in products:
            if p.eklenme <= first and (p.son_satis is None or p.son_satis >= last):
                w = p.cat.pop * p.pop / cat_size[p.cat.name]
                if p.cat.season:
                    w *= SEASON[p.cat.season][m - 1]
                if m == 11 and p.kampanya.get(y):
                    w *= 1.6
                avail.append(p)
                weights.append(w)
        out[(y, m)] = (avail, list(itertools.accumulate(weights)))
    return out


def make_lines(rng: random.Random, d: date, pickers) -> list[Line]:
    avail, cum = pickers[(d.year, d.month)]
    n = rng.choices([1, 2, 3, 4, 5], [55, 25, 12, 5, 3])[0]
    chosen: list[Product] = []
    for _ in range(n * 3):
        p = avail[bisect.bisect_right(cum, rng.random() * cum[-1])]
        if all(p is not q for q in chosen):
            chosen.append(p)
        if len(chosen) == n:
            break
    lines = []
    for p in chosen:
        birim = p.unit_price(d)
        if p.cat.name in CHEAP:
            adet = rng.choices([1, 2, 3, 4], [55, 25, 12, 8])[0]
        elif birim > 5000:
            adet = 1
        else:
            adet = rng.choices([1, 2], [92, 8])[0]
        lines.append(Line(p, adet, birim, p.kdv(d)))
    return lines


def price_order(rng: random.Random, o: Order, first_order: bool) -> None:
    o.toplam = sum((l.birim * l.adet for l in o.lines), Decimal(0))
    y = o.d.year
    if first_order and not o.customer.legacy:
        code, off, minimum = ("HOSGELDIN100", 100, 500) if y == 2023 else ("HOSGELDIN150", 150, 750)
        if o.toplam >= minimum and rng.random() < 0.45:
            o.kupon, o.indirim = code, Decimal(off)
    if o.kupon is None and o.d.month == 11 and o.toplam >= 1000 and rng.random() < 0.25:
        o.kupon, o.indirim = f"EFSANE{y % 100}", min(money(o.toplam * Decimal("0.10")), Decimal(750))
    if o.kupon is None and o.customer.n_orders >= 3 and rng.random() < 0.05:
        o.kupon, o.indirim = "SADAKAT5", money(o.toplam * Decimal("0.05"))
    fee, threshold = next((f, t) for start, f, t in SHIPPING if o.d >= start)
    o.kargo_ucreti = Decimal("0.00") if o.toplam - o.indirim >= threshold else fee
    paid = o.toplam - o.indirim + o.kargo_ucreti
    progress = (o.d - DATA_START).days / 730
    o.kanal = "MOBIL" if rng.random() < 0.48 + 0.12 * progress else "WEB"
    methods = ["KREDI_KARTI", "BANKA_KARTI", "HAVALE_EFT", "KAPIDA_ODEME"]
    weights = [63, 20, 5, 12 if paid < 5000 else 0]
    o.odeme = rng.choices(methods, weights)[0]
    if o.odeme == "KREDI_KARTI" and paid >= 1000:
        o.taksit = rng.choices([1, 2, 3, 6, 9, 12], [48, 8, 22, 12, 5, 5])[0]


def next_working_day(d: date) -> date:
    while d.weekday() == 6 or (d.month, d.day) == (1, 1):
        d += timedelta(days=1)
    return d


def ship(rng: random.Random, o: Order, used_tracking: set[str]) -> None:
    """Fill status and shipment: (carrier, takip, depot, shipped, delivered date, delivered time, desi, kg, cost)."""
    cancel = 0.06 if o.odeme == "KAPIDA_ODEME" else 0.03
    if rng.random() < cancel:
        o.durum = "IPTAL_EDILDI"
        return
    prep = rng.randint(4, 9) if rng.random() < 0.01 else rng.choices([0, 1, 2], [35, 50, 15])[0]
    shipped = next_working_day(o.d + timedelta(days=prep))
    if shipped >= SNAPSHOT:
        o.durum = "HAZIRLANIYOR"
        return
    region = o.address.bolge
    depot = DEPOT_OF_REGION[region]
    if rng.random() < 0.08:
        depot = "Ankara Deposu" if depot == "İstanbul Deposu" else "İstanbul Deposu"
    near_depot = DEPOT_OF_REGION[region]
    choices, weights = TRANSIT[(near_depot, region)]
    transit = rng.choices(choices, weights)[0] + (1 if depot != near_depot else 0)
    delivered = shipped + timedelta(days=transit)
    if delivered.weekday() == 6:
        delivered += timedelta(days=1)
    carrier = rng.choices(list(CARRIERS), [w for w, _ in CARRIERS.values()])[0]
    while True:
        takip = str(rng.randrange(10 ** 11, 10 ** 12))
        if takip not in used_tracking:
            used_tracking.add(takip)
            break
    single = len(o.lines) == 1 and o.lines[0].adet == 1
    volume = sum(l.adet * math.prod(l.product.dims) for l in o.lines) * (1.0 if single else 1.2)
    desi = max(0.5, math.ceil(volume / 3000 * 2) / 2)
    kg = sum((l.product.kg * l.adet for l in o.lines), Decimal(0)) + (Decimal(0) if single else Decimal("0.20"))
    cost = carrier_cost(max(desi, float(kg)), shipped, carrier)
    hour = rng.choices(range(9, 21), [3, 6, 9, 10, 10, 9, 9, 9, 8, 7, 5, 3])[0]
    when = (delivered, hour, rng.randint(0, 59)) if delivered < SNAPSHOT else None
    o.durum = "TESLIM_EDILDI" if when else "KARGODA"
    o.shipment = (carrier, takip, depot, shipped, when, Decimal(str(desi)), kg, cost)


def build_orders(rng: random.Random, customers: list[Customer], products: list[Product]) -> list[Order]:
    pickers = month_pickers(products)
    kayit = [c.kayit for c in customers]
    cum = list(itertools.accumulate(c.weight for c in customers))
    # forced first orders of customers who registered during the data period
    first_orders: dict[date, list[Customer]] = {}
    for c in customers:
        if c.kayit >= DATA_START and rng.random() < 0.8:
            d = c.kayit + timedelta(days=0 if rng.random() < 0.6 else rng.randint(1, 10))
            if d <= DATA_END:
                first_orders.setdefault(d, []).append(c)
    n_first = sum(len(v) for v in first_orders.values())
    target = 52000 - n_first
    all_days = list(days(DATA_START, DATA_END))
    intensity = [order_intensity(d) for d in all_days]
    scale = target / sum(intensity)
    orders: list[Order] = []
    used_tracking: set[str] = set()
    for d, f in zip(all_days, intensity):
        todays = [(c, True) for c in first_orders.get(d, [])]
        k = bisect.bisect_left(kayit, d)          # registered before d
        n = round(f * scale * rng.uniform(0.9, 1.1))
        if k and cum[k - 1] > 0:
            for _ in range(n):
                c = customers[bisect.bisect_right(cum, rng.random() * cum[k - 1], 0, k)]
                todays.append((c, False))
        rng.shuffle(todays)
        for c, first in todays:
            usable = [a for a in c.addresses if a.created <= d]
            addr = usable[0] if len(usable) == 1 or rng.random() < 0.75 else rng.choice(usable[1:])
            o = Order(d, c, addr, make_lines(rng, d, pickers))
            price_order(rng, o, first or c.n_orders == 0)
            c.n_orders += 1
            ship(rng, o, used_tracking)
            orders.append(o)
    return orders


# ---------------------------------------------------------------- returns

REASONS = {"Giyim": {"BEDEN_UYMADI": 55, "VAZGECTIM": 30, "HASARLI_GELDI": 5, "YANLIS_URUN": 5, "KUSURLU": 5},
           "Ayakkabı ve Çanta": {"BEDEN_UYMADI": 50, "VAZGECTIM": 30, "HASARLI_GELDI": 7, "YANLIS_URUN": 5, "KUSURLU": 8},
           "Elektronik": {"VAZGECTIM": 40, "KUSURLU": 35, "HASARLI_GELDI": 15, "YANLIS_URUN": 10},
           "Küçük Ev Aletleri": {"VAZGECTIM": 40, "KUSURLU": 35, "HASARLI_GELDI": 15, "YANLIS_URUN": 10},
           "Gurme Gıda": {"HASARLI_GELDI": 80, "YANLIS_URUN": 20}}
DEFAULT_REASONS = {"VAZGECTIM": 50, "HASARLI_GELDI": 30, "YANLIS_URUN": 10, "KUSURLU": 10}


def build_returns(rng: random.Random, orders: list[Order]) -> list[tuple]:
    """(talep, line, adet, neden, durum, tutar or None) for delivered orders; marks fully returned orders."""
    out = []
    for o in orders:
        if o.durum != "TESLIM_EDILDI":
            continue
        teslim = o.shipment[4][0]
        share = (o.indirim / o.toplam) if o.toplam else Decimal(0)
        full = 0
        for l in o.lines:
            if rng.random() >= l.product.ana.iade_orani:
                continue
            table = REASONS.get(l.product.ana.name, DEFAULT_REASONS)
            neden = rng.choices(list(table), list(table.values()))[0]
            if neden in ("VAZGECTIM", "BEDEN_UYMADI"):
                delay = (rng.choices(range(0, 15), [2, 8, 10, 10, 9, 8, 7, 6, 5, 5, 4, 4, 3, 3, 3])[0]
                         if rng.random() < 0.88 else rng.randint(15, 40))
            elif neden == "HASARLI_GELDI":
                delay = rng.randint(0, 3)
            elif neden == "YANLIS_URUN":
                delay = rng.randint(0, 6)
            else:
                delay = rng.randint(7, 150)
            talep = teslim + timedelta(days=delay)
            if talep >= SNAPSHOT:
                continue
            adet = l.adet if l.adet == 1 or rng.random() < 0.7 else rng.randint(1, l.adet - 1)
            if talep >= date(2024, 12, 30):
                durum = "BEKLIYOR"
            elif neden in ("VAZGECTIM", "BEDEN_UYMADI"):
                in_time = talep <= teslim + timedelta(days=CAYMA_GUN)
                durum = "ONAYLANDI" if in_time and rng.random() < 0.96 else "REDDEDILDI"
            else:
                durum = "ONAYLANDI" if rng.random() < 0.92 else "REDDEDILDI"
            tutar = money(l.birim * adet * (1 - share)) if durum == "ONAYLANDI" else None
            out.append((talep, l, adet, neden, durum, tutar))
            if durum == "ONAYLANDI" and adet == l.adet:
                full += 1
        if full == len(o.lines):
            o.durum = "IADE_EDILDI"
    return out


# ---------------------------------------------------------------- build

def build() -> dict[str, list[tuple]]:
    rng = random.Random(SEED)
    flat, by_il = load_places()
    kategori_rows, products = build_catalogue(rng)
    customers = build_customers(rng, flat, by_il)
    orders = build_orders(rng, customers, products)
    returns = build_returns(rng, orders)

    urun_rows = [(p.urun_id, p.stok_kodu, p.adi, p.marka, p.kategori_id, float(p.steps[-1][1]),
                  KDV[p.ana.group][1], float(p.kg), *p.dims, p.eklenme.isoformat(), int(p.son_satis is None))
                 for p in products]

    musteri_rows, addresses = [], []
    for c in customers:
        ad, soyad = (tr_upper(c.ad), tr_upper(c.soyad)) if c.legacy else (c.ad, c.soyad)
        musteri_rows.append((c.musteri_id, f"M{c.musteri_id:07d}", ad, soyad, c.cinsiyet,
                             c.dogum.isoformat() if c.dogum else None, c.kayit.isoformat(), c.izin,
                             "ESKI_AKTARIM" if c.legacy else "YENI"))
        addresses += [(a.created, c.musteri_id, a) for a in c.addresses]
    addresses.sort(key=lambda t: (t[0], t[1]))
    adres_rows = []
    for i, (_, mid, a) in enumerate(addresses, start=1):
        a.adres_id = i
        sehir, ilce = (tr_upper(a.sehir), tr_upper(a.ilce)) if a.legacy else (a.sehir, a.ilce)
        adres_rows.append((i, mid, a.baslik, sehir, ilce, "ESKI_AKTARIM" if a.legacy else "YENI"))

    siparis_rows, kalem_rows, kargo_rows = [], [], []
    seq: dict[int, int] = {}
    kalem_id = 0
    for i, o in enumerate(orders, start=1):
        o.siparis_id = i
        seq[o.d.year] = seq.get(o.d.year, 0) + 1
        siparis_rows.append((i, f"S{o.d.year}{seq[o.d.year]:06d}", o.customer.musteri_id, o.address.adres_id,
                             o.d.isoformat(), o.kanal, o.odeme, o.taksit, o.kupon, float(o.toplam),
                             float(o.indirim), float(o.kargo_ucreti),
                             float(o.toplam - o.indirim + o.kargo_ucreti), o.durum))
        for l in o.lines:
            kalem_id += 1
            l.kalem_id = kalem_id
            kalem_rows.append((kalem_id, i, l.product.urun_id, l.adet, float(l.birim), l.kdv))
        if o.shipment:
            carrier, takip, depot, shipped, when, desi, kg, cost = o.shipment
            teslim = f"{when[0]:%d.%m.%Y} {when[1]:02d}:{when[2]:02d}" if when else None
            kargo_rows.append((len(kargo_rows) + 1, i, carrier, takip, depot, shipped.isoformat(), teslim,
                               float(desi), float(kg), float(cost)))

    returns.sort(key=lambda r: (r[0], r[1].kalem_id))
    iade_rows = [(i, l.kalem_id, adet, talep.isoformat(), neden, durum, tr_money(tutar) if tutar is not None else None)
                 for i, (talep, l, adet, neden, durum, tutar) in enumerate(returns, start=1)]

    return {"kategori": kategori_rows, "urun": urun_rows, "musteri": musteri_rows, "adres": adres_rows,
            "siparis": siparis_rows, "siparis_kalemi": kalem_rows, "kargo": kargo_rows, "iade": iade_rows}


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
