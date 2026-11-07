"""Load a built SQLite database into PostgreSQL with identical data.

Drops and recreates the target database each run, using the server's default
collation (no explicit LOCALE/COLLATE), so re-running is safe.

    docker compose up -d
    python scripts/load_postgres.py               # loads belediye_su
    python scripts/load_postgres.py belediye_su

Connection settings come from PGHOST/PGPORT/PGUSER/PGPASSWORD
(defaults: localhost:5433, postgres/postgres, matching docker-compose.yml).
"""
from __future__ import annotations

import os
import sqlite3
import sys
from pathlib import Path

import psycopg
from psycopg import sql

ROOT = Path(__file__).resolve().parent.parent


def conninfo(dbname: str) -> str:
    return psycopg.conninfo.make_conninfo(
        host=os.environ.get("PGHOST", "localhost"),
        port=os.environ.get("PGPORT", "5433"),
        user=os.environ.get("PGUSER", "postgres"),
        password=os.environ.get("PGPASSWORD", "postgres"),
        dbname=dbname,
    )


def table_order(schema_sql: str) -> list[str]:
    """Tables in CREATE order (parents before children)."""
    names = []
    for line in schema_sql.splitlines():
        if line.upper().startswith("CREATE TABLE"):
            names.append(line.split()[2])
    return names


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
            rows = src.execute(f"SELECT {', '.join(cols)} FROM {table} ORDER BY 1")
            stmt = sql.SQL("COPY {} ({}) FROM STDIN").format(
                sql.Identifier(table), sql.SQL(", ").join(map(sql.Identifier, cols)))
            with pg.cursor().copy(stmt) as copy:
                for row in rows:
                    copy.write_row(row)
            n_src = src.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
            n_pg = pg.execute(sql.SQL("SELECT COUNT(*) FROM {}").format(sql.Identifier(table))).fetchone()[0]
            if n_src != n_pg:
                raise SystemExit(f"{table}: sqlite={n_src} postgres={n_pg}")
            print(f"  {table:8s} {n_pg:>7d} rows")
        pg.execute("ANALYZE")
        provider, collate, ctype = pg.execute(
            "SELECT datlocprovider, datcollate, datctype FROM pg_database WHERE datname = current_database()"
        ).fetchone()
        version = pg.execute("SHOW server_version").fetchone()[0]
    src.close()
    print(f"loaded {db_id} into PostgreSQL {version} "
          f"(locale provider={provider}, collate={collate}, ctype={ctype})")


if __name__ == "__main__":
    load(sys.argv[1] if len(sys.argv) > 1 else "belediye_su")
