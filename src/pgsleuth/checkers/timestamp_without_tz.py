"""Columns typed as `timestamp` (without time zone) — almost always a bug.

`timestamp without time zone` stores a wall-clock value with no awareness of
which timezone produced it. Two services writing to the same column from
different timezones, or a single service migrating between regions, will
silently produce values whose meaning depends on context the database
doesn't capture. `timestamptz` ("timestamp with time zone") stores the
moment in UTC and converts on display — almost always what teams actually
want.
"""

from __future__ import annotations

from typing import ClassVar

from pgsleuth.checkers.base import Issue, RowChecker, Severity, register
from pgsleuth.context import CheckerContext

_SQL = """
SELECT
    n.nspname AS schema,
    c.relname AS table,
    a.attname AS column
FROM pg_attribute  a
JOIN pg_class      c ON c.oid = a.attrelid
JOIN pg_namespace  n ON n.oid = c.relnamespace
WHERE c.relkind IN ('r', 'p')
  AND a.attnum > 0
  AND NOT a.attisdropped
  AND a.atttypid = 'timestamp without time zone'::regtype
  {schema_filter}
ORDER BY n.nspname, c.relname, a.attname;
"""


class TimestampWithoutTz(RowChecker):
    name: ClassVar[str] = "timestamp_without_tz"
    description: ClassVar[str] = (
        "Columns typed as `timestamp` (without time zone) drop timezone information."
    )
    default_severity: ClassVar[Severity] = Severity.WARNING
    sql: ClassVar[str] = _SQL

    def check_row(self, ctx: CheckerContext, row: dict) -> Issue | None:
        obj = f"{row['schema']}.{row['table']}.{row['column']}"
        return self.issue(
            ctx,
            object_type="column",
            object_name=obj,
            message=(
                f"Column {obj} is `timestamp without time zone`; "
                f"use `timestamptz` to avoid silent timezone bugs."
            ),
            suggestion=(
                f"ALTER TABLE {row['schema']}.{row['table']} "
                f"ALTER COLUMN {row['column']} TYPE timestamptz "
                f"USING {row['column']} AT TIME ZONE 'UTC'; "
                f"-- adjust the source timezone if existing values are not UTC"
            ),
        )


register(TimestampWithoutTz)
