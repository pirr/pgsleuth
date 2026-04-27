"""Foreign-key and CHECK constraints with convalidated = false.

Postgres allows `ADD CONSTRAINT ... NOT VALID` to skip the existence-time
scan of pre-existing rows. The intent is to follow up with `VALIDATE
CONSTRAINT`. When that follow-up never happens, the constraint is enforced
for new rows but pre-existing violators are silently tolerated. This is
specific to Postgres and the reference Ruby gem can't see it.
"""

from __future__ import annotations

from typing import ClassVar, Iterable

from pgsleuth.checkers.base import Checker, Issue, Severity, register
from pgsleuth.context import CheckerContext
from pgsleuth.db.catalog import excluded_schema_clause, fetch_all

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


class NotValidConstraints(Checker):
    name: ClassVar[str] = "not_valid_constraints"
    description: ClassVar[str] = (
        "Foreign keys or CHECK constraints that were added NOT VALID and never validated."
    )
    default_severity: ClassVar[Severity] = Severity.ERROR

    def run(self, ctx: CheckerContext) -> Iterable[Issue]:
        sql = _SQL.format(
            schema_filter=excluded_schema_clause(ctx.config.excluded_schemas, "n"),
        )
        for row in fetch_all(ctx.conn, sql):
            if ctx.config.is_table_excluded(row["schema"], row["table"]):
                continue
            kind = "foreign key" if row["contype"] == "f" else "check constraint"
            obj = f"{row['schema']}.{row['table']}.{row['constraint_name']}"
            yield Issue(
                checker=self.name,
                severity=ctx.config.severity_for(self.name, self.default_severity),
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
                docs_url="https://www.postgresql.org/docs/15/sql-altertable.html",
            )


register(NotValidConstraints)
