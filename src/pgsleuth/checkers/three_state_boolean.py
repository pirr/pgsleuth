"""Boolean columns without NOT NULL.

A nullable boolean is effectively a three-valued logic field (true/false/null).
That's almost always an oversight; either the column has a meaningful default
or NULL means something the type doesn't capture.
"""

from __future__ import annotations

from typing import ClassVar, Iterable

from pgsleuth.checkers.base import Checker, Issue, Severity, register
from pgsleuth.context import CheckerContext
from pgsleuth.db.catalog import iter_objects

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
        for row in iter_objects(ctx, _SQL):
            obj = f"{row['schema']}.{row['table']}.{row['column']}"
            yield self.issue(
                ctx,
                object_type="column",
                object_name=obj,
                message=f"Boolean column {obj} is nullable.",
                suggestion=(
                    f"ALTER TABLE {row['schema']}.{row['table']} "
                    f"ALTER COLUMN {row['column']} SET DEFAULT false, "
                    f"ALTER COLUMN {row['column']} SET NOT NULL;"
                ),
            )


register(ThreeStateBoolean)
