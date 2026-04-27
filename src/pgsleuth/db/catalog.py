"""Reusable pg_catalog query helpers.

Catalog queries that more than one checker uses live here. Checker-specific
queries live in the checker module itself, to keep each checker readable as a
single unit.
"""

from __future__ import annotations

from typing import Any, Iterable

import psycopg


def fetch_all(
    conn: psycopg.Connection,
    sql: str,
    params: tuple[Any, ...] | dict[str, Any] | None = None,
) -> list[dict[str, Any]]:
    with conn.cursor() as cur:
        cur.execute(sql, params)
        cols = [d.name for d in cur.description] if cur.description else []
        return [dict(zip(cols, row)) for row in cur.fetchall()]


def excluded_schema_clause(excluded: Iterable[str], alias: str = "n") -> str:
    """Return a SQL fragment like `AND n.nspname NOT IN ('pg_catalog', ...)`.

    The list is materialized as literals (catalog schema names are trusted
    inputs from config, not user request data); we still validate they look
    like identifiers so we never paste arbitrary strings.
    """
    safe = []
    for s in excluded:
        if not s.replace("_", "").isalnum():
            raise ValueError(f"unsafe schema name: {s!r}")
        safe.append(f"'{s}'")
    if not safe:
        return ""
    return f"AND {alias}.nspname NOT IN ({', '.join(safe)})"
