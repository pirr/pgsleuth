"""Ordinary tables without a primary key.

Tables without a primary key break logical replication (the default
REPLICA IDENTITY needs one), prevent some tools from identifying rows
unambiguously, and are nearly always an oversight.
"""

from __future__ import annotations

from typing import ClassVar, Iterable

from pgsleuth.checkers.base import Checker, Issue, Severity, register
from pgsleuth.context import CheckerContext
from pgsleuth.db.catalog import iter_objects

_SQL = """
SELECT
    n.nspname AS schema,
    c.relname AS table
FROM pg_class      c
JOIN pg_namespace  n ON n.oid = c.relnamespace
WHERE c.relkind = 'r'
  AND NOT c.relispartition
  AND NOT EXISTS (
        SELECT 1 FROM pg_index i
        WHERE i.indrelid = c.oid AND i.indisprimary
      )
  {schema_filter}
ORDER BY n.nspname, c.relname;
"""


class MissingPrimaryKey(Checker):
    name: ClassVar[str] = "missing_primary_key"
    description: ClassVar[str] = "Ordinary tables without a primary key."
    default_severity: ClassVar[Severity] = Severity.WARNING
    min_version: ClassVar[int] = 100000  # pg_class.relispartition lands in PG10

    def run(self, ctx: CheckerContext) -> Iterable[Issue]:
        for row in iter_objects(ctx, _SQL):
            obj = f"{row['schema']}.{row['table']}"
            yield self.issue(
                ctx,
                object_type="table",
                object_name=obj,
                message=f"Table {obj} has no primary key.",
                suggestion=(
                    f"ALTER TABLE {obj} ADD COLUMN id bigserial PRIMARY KEY; "
                    f"-- or pick an existing unique column"
                ),
            )


register(MissingPrimaryKey)
