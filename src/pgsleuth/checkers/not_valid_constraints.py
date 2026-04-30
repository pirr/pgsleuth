"""Foreign-key and CHECK constraints with convalidated = false.

Postgres allows `ADD CONSTRAINT ... NOT VALID` to skip the existence-time
scan of pre-existing rows. The intent is to follow up with `VALIDATE
CONSTRAINT`. When that follow-up never happens, the constraint is enforced
for new rows but pre-existing violators are silently tolerated. This is
specific to Postgres and the reference Ruby gem can't see it.
"""

from __future__ import annotations

from typing import ClassVar

from pgsleuth.checkers.base import Issue, RowChecker, Severity, register
from pgsleuth.context import CheckerContext

_SQL = """
SELECT
    n.nspname AS schema,
    t.relname AS table,
    c.conname AS constraint_name,
    c.contype AS contype
FROM pg_constraint c
JOIN pg_class      t ON t.oid = c.conrelid
JOIN pg_namespace  n ON n.oid = t.relnamespace
WHERE c.contype IN ('f', 'c')
  AND NOT c.convalidated
  {schema_filter}
ORDER BY n.nspname, t.relname, c.conname;
"""


class NotValidConstraints(RowChecker):
    name: ClassVar[str] = "not_valid_constraints"
    description: ClassVar[str] = (
        "Foreign keys or CHECK constraints that were added NOT VALID and never validated."
    )
    default_severity: ClassVar[Severity] = Severity.ERROR
    sql: ClassVar[str] = _SQL

    def check_row(self, ctx: CheckerContext, row: dict) -> Issue | None:
        kind = "foreign key" if row["contype"] == "f" else "check constraint"
        obj = f"{row['schema']}.{row['table']}.{row['constraint_name']}"
        return self.issue(
            ctx,
            object_type="constraint",
            object_name=obj,
            message=(
                f"{kind.capitalize()} {row['constraint_name']!r} on "
                f"{row['schema']}.{row['table']} is not validated; "
                f"pre-existing rows may violate it."
            ),
            suggestion=(
                f"ALTER TABLE {row['schema']}.{row['table']} "
                f"VALIDATE CONSTRAINT {row['constraint_name']};"
            ),
        )


register(NotValidConstraints)
