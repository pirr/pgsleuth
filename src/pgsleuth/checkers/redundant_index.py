"""Indexes whose column list is a strict prefix of another index on the same table.

If `idx_a (x)` exists and `idx_b (x, y)` exists, `idx_a` is almost always
redundant: any query using `idx_a` can use `idx_b` instead. Same access method,
no partial-predicate, non-unique on the prefix side.
"""

from __future__ import annotations

from typing import ClassVar, Iterable

from pgsleuth.checkers.base import Checker, Issue, Severity, register
from pgsleuth.context import CheckerContext
from pgsleuth.db.catalog import excluded_schema_clause, fetch_all

_SQL = """
WITH idx AS (
    SELECT
        n.nspname            AS schema,
        t.relname            AS table,
        ic.relname           AS index_name,
        -- pg_index.indkey is a 0-indexed int2vector; re-pack via unnest to
        -- normalize to a 1-indexed int[] so slicing behaves predictably.
        (SELECT array_agg(k::int) FROM unnest(i.indkey::int2[]) AS k) AS keys,
        i.indisunique        AS is_unique,
        i.indpred IS NOT NULL AS is_partial,
        am.amname            AS method
    FROM pg_index i
    JOIN pg_class      ic ON ic.oid = i.indexrelid
    JOIN pg_class      t  ON t.oid  = i.indrelid
    JOIN pg_namespace  n  ON n.oid  = t.relnamespace
    JOIN pg_am         am ON am.oid = ic.relam
    WHERE t.relkind IN ('r', 'p')
      {schema_filter}
)
SELECT
    a.schema,
    a.table,
    a.index_name        AS redundant_index,
    b.index_name        AS covering_index,
    a.keys              AS redundant_keys,
    b.keys              AS covering_keys
FROM idx a
JOIN idx b
  ON a.schema = b.schema
 AND a.table  = b.table
 AND a.index_name <> b.index_name
 AND a.method = b.method
 AND NOT a.is_partial
 AND NOT b.is_partial
 AND array_length(a.keys, 1) < array_length(b.keys, 1)
 AND b.keys[1:array_length(a.keys, 1)] = a.keys
 AND (NOT a.is_unique OR b.is_unique)
ORDER BY a.schema, a.table, a.index_name;
"""


class RedundantIndex(Checker):
    name: ClassVar[str] = "redundant_index"
    description: ClassVar[str] = (
        "Indexes that are a strict prefix of another index on the same table."
    )
    default_severity: ClassVar[Severity] = Severity.INFO

    def run(self, ctx: CheckerContext) -> Iterable[Issue]:
        sql = _SQL.format(
            schema_filter=excluded_schema_clause(ctx.config.excluded_schemas, "n"),
        )
        for row in fetch_all(ctx.conn, sql):
            if ctx.config.is_table_excluded(row["schema"], row["table"]):
                continue
            obj = f"{row['schema']}.{row['redundant_index']}"
            yield self.issue(
                ctx,
                object_type="index",
                object_name=obj,
                message=(
                    f"Index {row['redundant_index']!r} on {row['schema']}.{row['table']} "
                    f"is a prefix of {row['covering_index']!r}."
                ),
                suggestion=f"DROP INDEX {row['schema']}.{row['redundant_index']};",
            )


register(RedundantIndex)
