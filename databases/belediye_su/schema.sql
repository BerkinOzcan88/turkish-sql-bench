-- belediye_su: consolidated municipal water billing (several municipalities' water utilities)
-- Portable DDL: runs unchanged on SQLite 3 and PostgreSQL 16.
-- ISO date columns are DATE in Postgres and ISO-8601 TEXT in SQLite.
-- NUMERIC amounts are REAL in SQLite (generate.py swaps the type), so whole amounts never become integers.
-- Legacy quirks are intentional; see README.md ("Where the traps live").

CREATE TABLE adres (
    adres_id       INTEGER PRIMARY KEY,
    sehir          TEXT    NOT NULL,  -- il; ~15% legacy rows are ALL CAPS ('IĞDIR', 'İSTANBUL')
    ilce           TEXT    NOT NULL,  -- same casing as sehir in the row
    mahalle        TEXT    NOT NULL,
    sokak          TEXT    NOT NULL,
    bina_no        TEXT    NOT NULL,
    kaynak_sistem  TEXT    NOT NULL   -- 'YENI' | 'ESKI_AKTARIM'
);

CREATE TABLE abone (
    abone_id         INTEGER PRIMARY KEY,
    abone_no         TEXT    NOT NULL UNIQUE,
    abone_adi        TEXT    NOT NULL,  -- first name, or company/institution title
    abone_soyadi     TEXT,              -- NULL for ISYERI / RESMI
    tc_no            TEXT,              -- 11-digit T.C. kimlik no (KONUT only)
    vkn              TEXT,              -- 10-digit vergi kimlik no (ISYERI / RESMI only)
    abone_tipi       TEXT    NOT NULL,  -- 'KONUT' | 'ISYERI' | 'RESMI'
    adres_id         INTEGER NOT NULL REFERENCES adres (adres_id),
    abonelik_tarihi  DATE    NOT NULL,
    durum            TEXT    NOT NULL   -- 'AKTIF' | 'KAPALI'
);

CREATE TABLE sayac (
    sayac_id        INTEGER PRIMARY KEY,
    sayac_no        TEXT    NOT NULL UNIQUE,
    abone_id        INTEGER NOT NULL REFERENCES abone (abone_id),
    marka           TEXT    NOT NULL,
    cap_mm          INTEGER NOT NULL,   -- meter diameter in mm
    takilma_tarihi  DATE    NOT NULL,
    sokulme_tarihi  DATE                -- NULL while the meter is in service
);

CREATE TABLE okuma (
    okuma_id     INTEGER PRIMARY KEY,
    sayac_id     INTEGER NOT NULL REFERENCES sayac (sayac_id),
    okuma_tarihi TEXT    NOT NULL,      -- legacy text date 'DD.MM.YYYY'
    ilk_endeks   INTEGER NOT NULL,
    son_endeks   INTEGER NOT NULL,
    tuketim_m3   INTEGER NOT NULL,
    okuma_tipi   TEXT    NOT NULL       -- 'GERCEK' | 'TAHMINI'
);

CREATE TABLE fatura (
    fatura_id         INTEGER PRIMARY KEY,
    fatura_no         TEXT    NOT NULL UNIQUE,
    abone_id          INTEGER NOT NULL REFERENCES abone (abone_id),
    okuma_id          INTEGER NOT NULL UNIQUE REFERENCES okuma (okuma_id),
    fatura_tarihi     DATE    NOT NULL,
    son_odeme_tarihi  DATE    NOT NULL,
    su_bedeli         NUMERIC(12,2) NOT NULL,  -- water charge, KDV excluded
    su_kdv_orani      INTEGER NOT NULL,        -- percent: 1 (içme suyu, list I)
    atiksu_bedeli     NUMERIC(12,2) NOT NULL,  -- wastewater charge, KDV excluded
    atiksu_kdv_orani  INTEGER NOT NULL,        -- percent: 8 before 10.07.2023, 10 after
    ctv_tutari        NUMERIC(12,2) NOT NULL   -- çevre temizlik vergisi (konut only), not subject to KDV
);

CREATE TABLE odeme (
    odeme_id       INTEGER PRIMARY KEY,
    fatura_id      INTEGER NOT NULL REFERENCES fatura (fatura_id),
    odeme_tarihi   DATE    NOT NULL,
    odenen_tutar   TEXT    NOT NULL,           -- legacy Turkish-format text '1.250,50'
    gecikme_zammi  NUMERIC(10,2) NOT NULL,     -- late fee, included in odenen_tutar
    odeme_kanali   TEXT    NOT NULL            -- 'VEZNE' | 'BANKA' | 'INTERNET' | 'OTOMATIK'
);

CREATE INDEX idx_abone_adres  ON abone (adres_id);
CREATE INDEX idx_sayac_abone  ON sayac (abone_id);
CREATE INDEX idx_okuma_sayac  ON okuma (sayac_id);
CREATE INDEX idx_fatura_abone ON fatura (abone_id);
CREATE INDEX idx_odeme_fatura ON odeme (fatura_id);
