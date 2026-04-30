"""Columns typed as `json` instead of `jsonb`.

`json` stores the literal text of the input — including whitespace, key
order, and duplicate keys — and reparses it on every read. `jsonb` parses
once at write time, normalizes the structure, and supports indexing
(GIN), containment operators (`@>`, `<@`), and is materially faster on
read. The only thing `json` preserves that `jsonb` doesn't is the exact
input bytes; almost no application cares about that.
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
  AND a.atttypid = 'json'::regtype
  {schema_filter}
ORDER BY n.nspname, c.relname, a.attname;
"""


class JsonOverJsonb(RowChecker):
    name: ClassVar[str] = "json_over_jsonb"
    description: ClassVar[str] = "Columns typed as `json`; `jsonb` is almost always preferable."
    default_severity: ClassVar[Severity] = Severity.WARNING
    sql: ClassVar[str] = _SQL

    def check_row(self, ctx: CheckerContext, row: dict) -> Issue | None:
        obj = f"{row['schema']}.{row['table']}.{row['column']}"
        return self.issue(
            ctx,
            object_type="column",
            object_name=obj,
            message=(
                f"Column {obj} is `json`; `jsonb` supports indexing, containment "
                f"operators, and is faster on read."
            ),
            suggestion=(
                f"ALTER TABLE {row['schema']}.{row['table']} "
                f"ALTER COLUMN {row['column']} TYPE jsonb USING {row['column']}::jsonb;"
            ),
        )


register(JsonOverJsonb)
