"""Reusable pg_catalog query helpers.

Catalog queries that more than one checker uses live here. Checker-specific
queries live in the checker module itself, to keep each checker readable as a
single unit.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, Iterable, Iterator

import psycopg

if TYPE_CHECKING:
    from pgsleuth.context import CheckerContext


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


def iter_objects(
    ctx: "CheckerContext",
    sql_template: str,
    *,
    schema_alias: str = "n",
    schema_key: str = "schema",
    table_key: str = "table",
) -> Iterator[dict[str, Any]]:
    """Run a checker query and yield rows for objects not excluded by config.

    Centralizes the two filters every checker has to apply:
      1. The schema-exclude clause is interpolated into ``{schema_filter}`` in
         the SQL template (using ``schema_alias`` as the pg_namespace alias).
      2. Each row is post-filtered by ``Config.excluded_table_patterns``,
         keyed by ``schema_key``/``table_key`` in the row dict.

    Forgetting either filter in a checker silently broke ``--exclude-schema``
    or ``--exclude-table`` for that rule. Funneling every checker through
    this helper makes "applies the filters" the only way to write a checker.

    For checkers whose SQL aliases the schema/table differently (e.g.
    ``sequence_drift`` joins twice and exposes ``table_schema``/``table_name``),
    pass the keyword overrides.
    """
    sql = sql_template.format(
        schema_filter=excluded_schema_clause(ctx.config.excluded_schemas, schema_alias),
    )
    for row in fetch_all(ctx.conn, sql):
        if ctx.config.is_table_excluded(row[schema_key], row[table_key]):
            continue
        yield row
