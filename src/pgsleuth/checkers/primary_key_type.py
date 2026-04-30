"""Primary keys whose type can run out of values.

A 32-bit `integer`/`serial` PK has ~2.1 billion positive values. For any
table that grows steadily that ceiling is reachable; the migration to
`bigint` is painful when it's hot. Flag so it can be fixed early.
"""

from __future__ import annotations

from typing import ClassVar, Iterable

from pgsleuth.checkers.base import Checker, Issue, Severity, register
from pgsleuth.context import CheckerContext
from pgsleuth.db.catalog import excluded_schema_clause, fetch_all

_SQL = """
SELECT
    n.nspname                              AS schema,
    c.relname                              AS table,
    a.attname                              AS column,
    format_type(a.atttypid, a.atttypmod)   AS type
FROM pg_index i
JOIN pg_class       c ON c.oid = i.indrelid
JOIN pg_namespace   n ON n.oid = c.relnamespace
JOIN pg_attribute   a ON a.attrelid = c.oid AND a.attnum = ANY(i.indkey)
WHERE i.indisprimary
  AND c.relkind IN ('r', 'p')
  AND a.atttypid IN ('integer'::regtype, 'smallint'::regtype)
  {schema_filter}
ORDER BY n.nspname, c.relname, a.attname;
"""


class PrimaryKeyType(Checker):
    name: ClassVar[str] = "primary_key_type"
    description: ClassVar[str] = "Primary keys typed as integer/smallint will eventually overflow."
    default_severity: ClassVar[Severity] = Severity.WARNING

    def run(self, ctx: CheckerContext) -> Iterable[Issue]:
        sql = _SQL.format(
            schema_filter=excluded_schema_clause(ctx.config.excluded_schemas, "n"),
        )
        for row in fetch_all(ctx.conn, sql):
            if ctx.config.is_table_excluded(row["schema"], row["table"]):
                continue
            obj = f"{row['schema']}.{row['table']}.{row['column']}"
            yield self.issue(
                ctx,
                object_type="column",
                object_name=obj,
                message=f"Primary key {obj} is {row['type']}; consider bigint or uuid.",
                suggestion=(
                    f"ALTER TABLE {row['schema']}.{row['table']} "
                    f"ALTER COLUMN {row['column']} TYPE bigint;"
                ),
            )


register(PrimaryKeyType)
