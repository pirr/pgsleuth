"""Foreign key columns without a covering index.

A foreign key on `child(parent_id)` should usually be backed by an index whose
leftmost columns are exactly the FK column list. Otherwise:
  - cascading deletes/updates on the parent must seq-scan the child;
  - JOINs from parent to child miss the obvious index.
"""

from __future__ import annotations

from typing import ClassVar, Iterable

from pgsleuth.checkers.base import Checker, Issue, Severity, register
from pgsleuth.context import CheckerContext
from pgsleuth.db.catalog import excluded_schema_clause, fetch_all

_SQL = """
SELECT
    n.nspname                                       AS schema,
    t.relname                                       AS table,
    c.conname                                       AS constraint_name,
    (SELECT array_agg(a.attname ORDER BY i.ord)
       FROM unnest(c.conkey) WITH ORDINALITY AS i(attnum, ord)
       JOIN pg_attribute a
         ON a.attrelid = c.conrelid AND a.attnum = i.attnum) AS fk_columns
FROM pg_constraint c
JOIN pg_class      t ON t.oid = c.conrelid
JOIN pg_namespace  n ON n.oid = t.relnamespace
WHERE c.contype = 'f'
  {schema_filter}
  AND NOT EXISTS (
        SELECT 1
        FROM pg_index ix
        WHERE ix.indrelid = c.conrelid
          AND (ix.indkey::int2[])[0:array_length(c.conkey, 1) - 1] = c.conkey
      )
ORDER BY n.nspname, t.relname, c.conname;
"""


class MissingForeignKeyIndex(Checker):
    name: ClassVar[str] = "missing_fk_index"
    description: ClassVar[str] = (
        "Foreign key columns not covered by a leading index — slow cascades and joins."
    )
    default_severity: ClassVar[Severity] = Severity.WARNING

    def run(self, ctx: CheckerContext) -> Iterable[Issue]:
        sql = _SQL.format(
            schema_filter=excluded_schema_clause(ctx.config.excluded_schemas, "n"),
        )
        for row in fetch_all(ctx.conn, sql):
            if ctx.config.is_table_excluded(row["schema"], row["table"]):
                continue
            cols = ", ".join(row["fk_columns"])
            obj = f"{row['schema']}.{row['table']}({cols})"
            yield Issue(
                checker=self.name,
                severity=ctx.config.severity_for(self.name, self.default_severity),
                object_type="constraint",
                object_name=obj,
                message=(f"Foreign key {row['constraint_name']!r} on {obj} has no covering index."),
                suggestion=(f"CREATE INDEX ON {row['schema']}.{row['table']} ({cols});"),
                docs_url="https://www.postgresql.org/docs/15/indexes-multicolumn.html",
            )


register(MissingForeignKeyIndex)
