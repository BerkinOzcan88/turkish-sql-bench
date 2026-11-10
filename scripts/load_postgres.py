"""Load the built SQLite databases into PostgreSQL with identical data.

Drops and recreates each target database, using the server's default
collation (no explicit LOCALE/COLLATE), so re-running is safe. After loading,
every table is compared with its SQLite source by a hash of all rows (numbers
and dates normalized), and a read-only role is (re)granted SELECT on it:
evaluate.py runs model SQL as that role, never as the superuser.

    docker compose up -d
    python scripts/load_postgres.py               # every databases/<db_id>/<db_id>.sqlite
    python scripts/load_postgres.py belediye_su

Connection settings come from PGHOST/PGPORT/PGUSER/PGPASSWORD (defaults:
localhost:5433, postgres/postgres, matching docker-compose.yml). The read-only
role is PGREADER_USER/PGREADER_PASSWORD (default bench_reader/bench_reader).
"""
from __future__ import annotations

import datetime as dt
import hashlib
import os
import sqlite3
import sys
from decimal import Decimal
from pathlib import Path

import psycopg
from psycopg import sql

ROOT = Path(__file__).resolve().parent.parent


def reader_credentials() -> tuple[str, str]:
    return os.environ.get("PGREADER_USER", "bench_reader"), os.environ.get("PGREADER_PASSWORD", "bench_reader")


def conninfo(dbname: str, reader: bool = False) -> str:
    user, password = (reader_credentials() if reader else
                      (os.environ.get("PGUSER", "postgres"), os.environ.get("PGPASSWORD", "postgres")))
    return psycopg.conninfo.make_conninfo(
        host=os.environ.get("PGHOST", "localhost"),
        port=os.environ.get("PGPORT", "5433"),
        user=user,
        password=password,
        dbname=dbname,
    )


def table_order(schema_sql: str) -> list[str]:
    """Tables in CREATE order (parents before children)."""
    names = []
    for line in schema_sql.splitlines():
        if line.upper().startswith("CREATE TABLE"):
            names.append(line.split()[2])
    return names


def _canon(v) -> str:
    if v is None:
        return "\\N"
    if isinstance(v, (int, float, Decimal)) and not isinstance(v, bool):
        return f"{Decimal(str(v)):.6f}"
    if isinstance(v, (dt.date, dt.datetime)):
        return v.isoformat()
    return str(v)


def rows_digest(rows) -> str:
    """Hash of rows in the given order; SQLite and Postgres forms of the same value hash alike."""
    h = hashlib.sha256()
    for r in rows:
        h.update("\t".join(_canon(v) for v in r).encode("utf-8") + b"\n")
    return h.hexdigest()


def grant_reader(pg: psycopg.Connection, db_id: str) -> None:
    user, password = reader_credentials()
    exists = pg.execute("SELECT 1 FROM pg_roles WHERE rolname = %s", (user,)).fetchone()
    pg.execute(sql.SQL("{} ROLE {} LOGIN PASSWORD {}").format(
        sql.SQL("ALTER" if exists else "CREATE"), sql.Identifier(user), sql.Literal(password)))
    pg.execute(sql.SQL("ALTER ROLE {} SET default_transaction_read_only = on").format(sql.Identifier(user)))
    pg.execute(sql.SQL("GRANT CONNECT ON DATABASE {} TO {}").format(sql.Identifier(db_id), sql.Identifier(user)))
    pg.execute(sql.SQL("GRANT USAGE ON SCHEMA public TO {}").format(sql.Identifier(user)))
    pg.execute(sql.SQL("GRANT SELECT ON ALL TABLES IN SCHEMA public TO {}").format(sql.Identifier(user)))


def load(db_id: str) -> None:
    db_dir = ROOT / "databases" / db_id
    sqlite_path = db_dir / f"{db_id}.sqlite"
    schema_sql = (db_dir / "schema.sql").read_text(encoding="utf-8")
    if not sqlite_path.exists():
        raise SystemExit(f"{sqlite_path} not found; run databases/{db_id}/generate.py first")

    with psycopg.connect(conninfo("postgres"), autocommit=True) as admin:
        admin.execute(sql.SQL("DROP DATABASE IF EXISTS {} WITH (FORCE)").format(sql.Identifier(db_id)))
        admin.execute(sql.SQL("CREATE DATABASE {} ENCODING 'UTF8'").format(sql.Identifier(db_id)))

    src = sqlite3.connect(sqlite_path)
    with psycopg.connect(conninfo(db_id)) as pg:
        pg.execute(schema_sql)
        for table in table_order(schema_sql):
            cols = [r[1] for r in src.execute(f"PRAGMA table_info({table})")]
            select = f"SELECT {', '.join(cols)} FROM {table} ORDER BY 1"
            stmt = sql.SQL("COPY {} ({}) FROM STDIN").format(
                sql.Identifier(table), sql.SQL(", ").join(map(sql.Identifier, cols)))
            with pg.cursor().copy(stmt) as copy:
                for row in src.execute(select):
                    copy.write_row(row)
            n_pg = pg.execute(sql.SQL("SELECT COUNT(*) FROM {}").format(sql.Identifier(table))).fetchone()[0]
            if rows_digest(src.execute(select)) != rows_digest(pg.execute(select)):
                raise SystemExit(f"{table}: Postgres rows differ from SQLite")
            print(f"  {table:12s} {n_pg:>7d} rows, contents identical")
        grant_reader(pg, db_id)
        pg.execute("ANALYZE")
        provider, collate, ctype = pg.execute(
            "SELECT datlocprovider, datcollate, datctype FROM pg_database WHERE datname = current_database()"
        ).fetchone()
        version = pg.execute("SHOW server_version").fetchone()[0]
    src.close()
    print(f"loaded {db_id} into PostgreSQL {version} "
          f"(locale provider={provider}, collate={collate}, ctype={ctype}); read-only role {reader_credentials()[0]}")


def all_databases() -> list[str]:
    return sorted(p.parent.name for p in (ROOT / "databases").glob("*/*.sqlite") if p.stem == p.parent.name)


if __name__ == "__main__":
    for db in sys.argv[1:] or all_databases():
        load(db)
