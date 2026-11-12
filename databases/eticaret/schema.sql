-- eticaret: orders of a fictional single online home and lifestyle shop (one seller, two warehouses)
-- Portable DDL: runs unchanged on SQLite 3 and PostgreSQL 16.
-- ISO date columns are DATE in Postgres and ISO-8601 TEXT in SQLite.
-- NUMERIC amounts are REAL in SQLite (generate.py swaps the type), so whole amounts never become integers.
-- Legacy quirks are intentional; see README.md ("Where the traps live").

CREATE TABLE kategori (
    kategori_id    INTEGER PRIMARY KEY,
    kategori_adi   TEXT    NOT NULL UNIQUE,  -- singular: 'Kulaklık', 'Çorap', 'Saklama Kabı'
    ana_kategori   TEXT    NOT NULL          -- 'Elektronik', 'Giyim', 'Gurme Gıda', ...
);

CREATE TABLE urun (
    urun_id         INTEGER PRIMARY KEY,
    stok_kodu       TEXT    NOT NULL UNIQUE,  -- 'ELK-0012'
    urun_adi        TEXT    NOT NULL,
    marka           TEXT    NOT NULL,         -- fictional brands and publishers
    kategori_id     INTEGER NOT NULL REFERENCES kategori (kategori_id),
    liste_fiyati    NUMERIC(10,2) NOT NULL,   -- current shelf price, KDV included
    kdv_orani       INTEGER NOT NULL,         -- current rate, percent: 1 | 10 | 20
    agirlik_kg      NUMERIC(6,2) NOT NULL,    -- packed weight
    en_cm           INTEGER NOT NULL,         -- package dimensions
    boy_cm          INTEGER NOT NULL,
    yukseklik_cm    INTEGER NOT NULL,
    eklenme_tarihi  DATE    NOT NULL,
    satista         INTEGER NOT NULL          -- 1 on sale, 0 discontinued
);

CREATE TABLE musteri (
    musteri_id     INTEGER PRIMARY KEY,
    musteri_no     TEXT    NOT NULL UNIQUE,  -- 'M0012345', in registration order
    ad             TEXT    NOT NULL,         -- legacy rows ALL CAPS ('İBRAHİM')
    soyad          TEXT    NOT NULL,         -- legacy rows ALL CAPS
    cinsiyet       TEXT,                     -- 'E' | 'K'; NULL if not given
    dogum_tarihi   DATE,                     -- NULL if not given
    kayit_tarihi   DATE    NOT NULL,
    eposta_izni    INTEGER NOT NULL,         -- ticari elektronik ileti onayı: 1 | 0
    kaynak_sistem  TEXT    NOT NULL          -- 'YENI' | 'ESKI_AKTARIM' (registered before the 01.09.2022 platform move)
);

CREATE TABLE adres (
    adres_id       INTEGER PRIMARY KEY,
    musteri_id     INTEGER NOT NULL REFERENCES musteri (musteri_id),
    adres_basligi  TEXT    NOT NULL,         -- customer's own label: 'Ev', 'İş', 'Annemler', 'Yazlık'
    sehir          TEXT    NOT NULL,         -- il; legacy rows ALL CAPS ('IĞDIR', 'İZMİR')
    ilce           TEXT    NOT NULL,         -- same casing as sehir in the row
    kaynak_sistem  TEXT    NOT NULL          -- 'YENI' | 'ESKI_AKTARIM'
);

CREATE TABLE siparis (
    siparis_id         INTEGER PRIMARY KEY,
    siparis_no         TEXT    NOT NULL UNIQUE,   -- 'S2024012345'
    musteri_id         INTEGER NOT NULL REFERENCES musteri (musteri_id),
    teslimat_adres_id  INTEGER NOT NULL REFERENCES adres (adres_id),
    siparis_tarihi     DATE    NOT NULL,
    kanal              TEXT    NOT NULL,          -- 'WEB' | 'MOBIL'
    odeme_yontemi      TEXT    NOT NULL,          -- 'KREDI_KARTI' | 'BANKA_KARTI' | 'HAVALE_EFT' | 'KAPIDA_ODEME'
    taksit_sayisi      INTEGER NOT NULL,          -- 1 = tek çekim
    kupon_kodu         TEXT,
    urun_toplami       NUMERIC(12,2) NOT NULL,    -- sum of adet × birim_fiyat, KDV included
    indirim_tutari     NUMERIC(10,2) NOT NULL,    -- coupon discount
    kargo_ucreti       NUMERIC(8,2)  NOT NULL,    -- shipping fee charged to the customer; 0 above the free-shipping threshold
    odenen_tutar       NUMERIC(12,2) NOT NULL,    -- urun_toplami - indirim_tutari + kargo_ucreti
    durum              TEXT    NOT NULL           -- 'HAZIRLANIYOR' | 'KARGODA' | 'TESLIM_EDILDI' | 'IPTAL_EDILDI' | 'IADE_EDILDI' (every item returned)
);

CREATE TABLE siparis_kalemi (
    kalem_id     INTEGER PRIMARY KEY,
    siparis_id   INTEGER NOT NULL REFERENCES siparis (siparis_id),
    urun_id      INTEGER NOT NULL REFERENCES urun (urun_id),
    adet         INTEGER NOT NULL,
    birim_fiyat  NUMERIC(10,2) NOT NULL,   -- price paid per unit on the order date, KDV included
    kdv_orani    INTEGER NOT NULL          -- rate applied on the order date, percent
);

CREATE TABLE kargo (
    kargo_id                INTEGER PRIMARY KEY,
    siparis_id              INTEGER NOT NULL UNIQUE REFERENCES siparis (siparis_id),
    kargo_firmasi           TEXT    NOT NULL,       -- fictional carriers
    takip_no                TEXT    NOT NULL UNIQUE,
    cikis_deposu            TEXT    NOT NULL,       -- 'İstanbul Deposu' | 'Ankara Deposu'
    kargoya_verilis_tarihi  DATE    NOT NULL,
    teslim_zamani           TEXT,                   -- carrier feed text 'DD.MM.YYYY HH:MM'; NULL until delivered
    desi                    NUMERIC(6,1) NOT NULL,  -- package volume desi measured by the carrier
    agirlik_kg              NUMERIC(6,2) NOT NULL,  -- package weight
    kargo_maliyeti          NUMERIC(8,2) NOT NULL   -- carrier's charge to the shop, KDV excluded
);

CREATE TABLE iade (
    iade_id       INTEGER PRIMARY KEY,
    kalem_id      INTEGER NOT NULL REFERENCES siparis_kalemi (kalem_id),
    adet          INTEGER NOT NULL,
    talep_tarihi  DATE    NOT NULL,
    iade_nedeni   TEXT    NOT NULL,   -- 'VAZGECTIM' | 'BEDEN_UYMADI' | 'HASARLI_GELDI' | 'YANLIS_URUN' | 'KUSURLU'
    durum         TEXT    NOT NULL,   -- 'ONAYLANDI' | 'REDDEDILDI' | 'BEKLIYOR'
    iade_tutari   TEXT                -- payment provider's refund report, Turkish number format '1.299,90'; NULL unless ONAYLANDI
);

CREATE INDEX idx_urun_kategori    ON urun (kategori_id);
CREATE INDEX idx_adres_musteri    ON adres (musteri_id);
CREATE INDEX idx_siparis_musteri  ON siparis (musteri_id);
CREATE INDEX idx_siparis_adres    ON siparis (teslimat_adres_id);
CREATE INDEX idx_kalem_siparis    ON siparis_kalemi (siparis_id);
CREATE INDEX idx_kalem_urun       ON siparis_kalemi (urun_id);
CREATE INDEX idx_iade_kalem       ON iade (kalem_id);
