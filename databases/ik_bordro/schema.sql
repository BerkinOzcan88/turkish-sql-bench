-- ik_bordro: payroll and HR records of a fictional company group with branches across Türkiye
-- Portable DDL: runs unchanged on SQLite 3 and PostgreSQL 16.
-- ISO date columns are DATE in Postgres and ISO-8601 TEXT in SQLite.
-- NUMERIC amounts are REAL in SQLite (generate.py swaps the type), so whole amounts never become integers.
-- Legacy quirks are intentional; see README.md ("Where the traps live").

CREATE TABLE sube (
    sube_id        INTEGER PRIMARY KEY,
    sube_kodu      TEXT    NOT NULL UNIQUE,  -- 'IZM01'
    sube_adi       TEXT    NOT NULL,         -- 'Bornova Şubesi', 'Genel Müdürlük'
    sehir          TEXT    NOT NULL,
    ilce           TEXT    NOT NULL,
    acilis_tarihi  DATE    NOT NULL
);

CREATE TABLE departman (
    departman_id   INTEGER PRIMARY KEY,
    departman_adi  TEXT    NOT NULL UNIQUE
);

CREATE TABLE personel (
    personel_id         INTEGER PRIMARY KEY,
    sicil_no            TEXT    NOT NULL UNIQUE,  -- hire year + sequence, '20190042'
    ad                  TEXT    NOT NULL,         -- legacy rows ALL CAPS ('IŞIL')
    soyad               TEXT    NOT NULL,         -- legacy rows ALL CAPS
    tc_no               TEXT    NOT NULL UNIQUE,
    cinsiyet            TEXT    NOT NULL,         -- 'E' | 'K'
    dogum_tarihi        DATE    NOT NULL,
    ise_giris_tarihi    DATE    NOT NULL,
    isten_cikis_tarihi  DATE,                     -- NULL while still employed
    sube_id             INTEGER NOT NULL REFERENCES sube (sube_id),
    departman_id        INTEGER NOT NULL REFERENCES departman (departman_id),
    unvan               TEXT    NOT NULL,         -- legacy rows ALL CAPS ('MÜDÜR YARDIMCISI')
    kaynak_sistem       TEXT    NOT NULL          -- 'YENI' | 'ESKI_AKTARIM'
);

CREATE TABLE bordro (
    bordro_id           INTEGER PRIMARY KEY,
    personel_id         INTEGER NOT NULL REFERENCES personel (personel_id),
    donem               TEXT    NOT NULL,          -- legacy text period: Turkish month name + year, 'Şubat 2024'
    calisma_gunu        INTEGER NOT NULL,          -- SGK days; 30 for a full month
    brut_ucret          NUMERIC(12,2) NOT NULL,    -- salary for the days worked
    fazla_mesai_ucreti  NUMERIC(12,2) NOT NULL,    -- overtime pay of the month (sum of fazla_mesai.ucret)
    sgk_isci_payi       NUMERIC(12,2) NOT NULL,    -- 14% of gross, capped at the SGK ceiling
    issizlik_isci_payi  NUMERIC(12,2) NOT NULL,    -- 1% of gross, same cap
    gelir_vergisi       NUMERIC(12,2) NOT NULL,    -- after the minimum-wage exemption
    damga_vergisi       NUMERIC(12,2) NOT NULL,    -- after the minimum-wage exemption
    net_ucret           NUMERIC(12,2) NOT NULL,    -- gross + overtime - all four deductions
    UNIQUE (personel_id, donem)
);

CREATE TABLE izin (
    izin_id           INTEGER PRIMARY KEY,
    personel_id       INTEGER NOT NULL REFERENCES personel (personel_id),
    izin_turu         TEXT    NOT NULL,   -- 'YILLIK' | 'RAPOR' | 'MAZERET' | 'UCRETSIZ'
    baslangic_tarihi  DATE    NOT NULL,
    bitis_tarihi      DATE    NOT NULL,
    gun_sayisi        INTEGER NOT NULL    -- working days (Monday-Friday) in the range
);

CREATE TABLE fazla_mesai (
    mesai_id     INTEGER PRIMARY KEY,
    personel_id  INTEGER NOT NULL REFERENCES personel (personel_id),
    tarih        DATE    NOT NULL,
    saat         TEXT    NOT NULL,          -- legacy text hours with decimal comma: '2,5', '8'
    ucret        NUMERIC(10,2) NOT NULL     -- saat × (monthly gross / 225) × 1.5
);

CREATE INDEX idx_personel_sube      ON personel (sube_id);
CREATE INDEX idx_personel_departman ON personel (departman_id);
CREATE INDEX idx_bordro_personel    ON bordro (personel_id);
CREATE INDEX idx_izin_personel      ON izin (personel_id);
CREATE INDEX idx_mesai_personel     ON fazla_mesai (personel_id);
