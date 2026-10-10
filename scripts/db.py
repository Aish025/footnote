"""Postgres connection + schema helpers (pg8000: pure-Python driver, see D-011)."""

from __future__ import annotations

import pg8000.native as pg

from config import Settings
from manifest import REPO_ROOT

SCHEMA_SQL = REPO_ROOT / "db" / "schema.sql"


def connect(settings: Settings) -> pg.Connection:
    return pg.Connection(
        user=settings.postgres_user,
        password=settings.postgres_password.get_secret_value(),
        database=settings.postgres_db,
        host=settings.postgres_host,
        port=settings.postgres_port,
    )


def apply_schema(conn: pg.Connection, settings: Settings) -> None:
    """Create tables/indexes if missing. Idempotent."""
    sql = SCHEMA_SQL.read_text(encoding="utf-8").replace(
        "{embedding_dim}", str(settings.embedding_dim)
    )
    for statement in split_statements(sql):
        conn.run(statement)


def split_statements(sql: str) -> list[str]:
    """Split a schema file on ';' at line ends, dropping '--' comment lines."""
    lines = [ln for ln in sql.splitlines() if not ln.strip().startswith("--")]
    parts = (s.strip().rstrip(";").strip() for s in "\n".join(lines).split(";\n"))
    return [p for p in parts if p]
