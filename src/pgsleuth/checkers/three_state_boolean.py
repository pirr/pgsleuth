"""Boolean columns without NOT NULL.

A nullable boolean is effectively a three-valued logic field (true/false/null).
That's almost always an oversight; either the column has a meaningful default
or NULL means something the type doesn't capture.
"""

from __future__ import annotations

from typing import ClassVar, Iterable

from pgsleuth.checkers.base import Checker, Issue, Severity, register
from pgsleuth.context import CheckerContext
from pgsleuth.db.catalog import excluded_schema_clause, fetch_all
from pgsleuth.db.connection import pg_docs_url

_SQL = """
SELECT
    n.nspname AS schema,
    c.relname AS table,
    a.attname AS column
FROM pg_attribute a
JOIN pg_class      c ON c.oid = a.attrelid
JOIN pg_namespace  n ON n.oid = c.relnamespace
WHERE c.relkind IN ('r', 'p')
  AND a.attnum > 0
  AND NOT a.attisdropped
  AND a.atttypid = 'boolean'::regtype
  AND NOT a.attnotnull
  {schema_filter}
ORDER BY n.nspname, c.relname, a.attname;
"""


class ThreeStateBoolean(Checker):
    name: ClassVar[str] = "three_state_boolean"
    description: ClassVar[str] = (
        "Boolean columns without NOT NULL turn boolean into a three-valued field."
    )
    default_severity: ClassVar[Severity] = Severity.WARNING

    def run(self, ctx: CheckerContext) -> Iterable[Issue]:
        sql = _SQL.format(
            schema_filter=excluded_schema_clause(ctx.config.excluded_schemas, "n"),
        )
        for row in fetch_all(ctx.conn, sql):
            if ctx.config.is_table_excluded(row["schema"], row["table"]):
                continue
            obj = f"{row['schema']}.{row['table']}.{row['column']}"
            yield Issue(
                checker=self.name,
                severity=ctx.config.severity_for(self.name, self.default_severity),
                object_type="column",
                object_name=obj,
                message=f"Boolean column {obj} is nullable.",
                suggestion=(
                    f"ALTER TABLE {row['schema']}.{row['table']} "
                    f"ALTER COLUMN {row['column']} SET DEFAULT false, "
                    f"ALTER COLUMN {row['column']} SET NOT NULL;"
                ),
                docs_url=pg_docs_url(ctx.server_version, "datatype-boolean.html"),
            )


register(ThreeStateBoolean)
