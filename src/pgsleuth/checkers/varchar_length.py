"""Columns typed as `varchar(N)` where `text` would be equivalent.

In Postgres, `varchar(N)` and `text` are stored identically — there is no
performance benefit to a length cap. The cap is enforced as a constraint
at write time, which is fine if you actually want that constraint, but
many `varchar(N)` columns exist only because the schema author was
following habits from MySQL or SQL Server where the length matters.

Bare `varchar` (no length) and `text` are not flagged.
"""

from __future__ import annotations

from typing import ClassVar

from pgsleuth.checkers.base import Issue, RowChecker, Severity, register
from pgsleuth.context import CheckerContext

# atttypmod for varchar(N) is `N + VARHDRSZ` (= N + 4); -1 means unbounded.
_SQL = """
SELECT
    n.nspname AS schema,
    c.relname AS table,
    a.attname AS column,
    a.atttypmod - 4 AS length
FROM pg_attribute  a
JOIN pg_class      c ON c.oid = a.attrelid
JOIN pg_namespace  n ON n.oid = c.relnamespace
WHERE c.relkind IN ('r', 'p')
  AND a.attnum > 0
  AND NOT a.attisdropped
  AND a.atttypid = 'varchar'::regtype
  AND a.atttypmod <> -1
  {schema_filter}
ORDER BY n.nspname, c.relname, a.attname;
"""


class VarcharLength(RowChecker):
    name: ClassVar[str] = "varchar_length"
    description: ClassVar[str] = (
        "`varchar(N)` columns where `text` would be equivalent and avoid the cap."
    )
    default_severity: ClassVar[Severity] = Severity.INFO
    sql: ClassVar[str] = _SQL

    def check_row(self, ctx: CheckerContext, row: dict) -> Issue | None:
        obj = f"{row['schema']}.{row['table']}.{row['column']}"
        return self.issue(
            ctx,
            object_type="column",
            object_name=obj,
            message=(
                f"Column {obj} is `varchar({row['length']})`; in Postgres, "
                f"`text` is stored identically and avoids the length-change migration."
            ),
            suggestion=(
                f"ALTER TABLE {row['schema']}.{row['table']} "
                f"ALTER COLUMN {row['column']} TYPE text; "
                f"-- or keep the cap as an explicit CHECK constraint if it's intended"
            ),
            extra={"length": str(row["length"])},
        )


register(VarcharLength)
