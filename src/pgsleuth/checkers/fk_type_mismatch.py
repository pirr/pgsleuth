"""Foreign key columns whose type differs from the referenced primary/unique column.

Mismatched types (e.g. `integer` referencing `bigint`) force casting at
join time and prevent index use in some plans. Almost always a bug.
"""

from __future__ import annotations

from typing import ClassVar, Iterable

from pgsleuth.checkers.base import Checker, Issue, Severity, register
from pgsleuth.context import CheckerContext
from pgsleuth.db.catalog import excluded_schema_clause, fetch_all
from pgsleuth.db.connection import rule_docs_url

_SQL = """
WITH fk AS (
    SELECT
        c.oid               AS conid,
        c.conname,
        c.conrelid,
        c.confrelid,
        c.conkey,
        c.confkey,
        n.nspname           AS schema,
        t.relname           AS table,
        nf.nspname          AS ref_schema,
        tf.relname          AS ref_table
    FROM pg_constraint c
    JOIN pg_class      t  ON t.oid  = c.conrelid
    JOIN pg_namespace  n  ON n.oid  = t.relnamespace
    JOIN pg_class      tf ON tf.oid = c.confrelid
    JOIN pg_namespace  nf ON nf.oid = tf.relnamespace
    WHERE c.contype = 'f'
      {schema_filter}
)
SELECT
    fk.schema, fk.table, fk.ref_schema, fk.ref_table, fk.conname,
    a.attname  AS column,
    af.attname AS ref_column,
    format_type(a.atttypid,  a.atttypmod)  AS column_type,
    format_type(af.atttypid, af.atttypmod) AS ref_column_type
FROM fk
JOIN unnest(fk.conkey)  WITH ORDINALITY AS k(attnum, ord)  ON TRUE
JOIN unnest(fk.confkey) WITH ORDINALITY AS kf(attnum, ord) ON kf.ord = k.ord
JOIN pg_attribute a  ON a.attrelid  = fk.conrelid  AND a.attnum  = k.attnum
JOIN pg_attribute af ON af.attrelid = fk.confrelid AND af.attnum = kf.attnum
WHERE format_type(a.atttypid, a.atttypmod) <> format_type(af.atttypid, af.atttypmod)
ORDER BY fk.schema, fk.table, fk.conname;
"""


class ForeignKeyTypeMismatch(Checker):
    name: ClassVar[str] = "fk_type_mismatch"
    description: ClassVar[str] = "Foreign key column type differs from the referenced column type."
    default_severity: ClassVar[Severity] = Severity.ERROR

    def run(self, ctx: CheckerContext) -> Iterable[Issue]:
        sql = _SQL.format(
            schema_filter=excluded_schema_clause(ctx.config.excluded_schemas, "n"),
        )
        for row in fetch_all(ctx.conn, sql):
            if ctx.config.is_table_excluded(row["schema"], row["table"]):
                continue
            child = f"{row['schema']}.{row['table']}.{row['column']}"
            parent = f"{row['ref_schema']}.{row['ref_table']}.{row['ref_column']}"
            yield Issue(
                checker=self.name,
                severity=ctx.config.severity_for(self.name, self.default_severity),
                object_type="column",
                object_name=child,
                message=(
                    f"{child} ({row['column_type']}) references "
                    f"{parent} ({row['ref_column_type']}) — types differ."
                ),
                suggestion=(
                    f"ALTER TABLE {row['schema']}.{row['table']} "
                    f"ALTER COLUMN {row['column']} TYPE {row['ref_column_type']};"
                ),
                docs_url=rule_docs_url(self.name),
            )


register(ForeignKeyTypeMismatch)
