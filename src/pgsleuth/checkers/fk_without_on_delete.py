"""Foreign keys with no explicit ON DELETE policy.

Postgres' default `ON DELETE` action is `NO ACTION`, applied silently when
the FK is declared without an `ON DELETE` clause. NO ACTION is rarely the
deliberate choice: teams usually want `RESTRICT` (be strict and explicit
about it), `CASCADE`, or `SET NULL`. Flagging FKs that resolved to NO
ACTION nudges the team to make the choice intentionally — and to revisit
it when business rules change.

The catalog stores only the resulting action, not whether the user wrote
`ON DELETE NO ACTION` explicitly. This rule treats both the implicit and
explicit cases the same: if the team genuinely wants NO ACTION, they can
suppress the rule on the constraint.
"""

from __future__ import annotations

from typing import ClassVar

from pgsleuth.checkers.base import Issue, RowChecker, Severity, register
from pgsleuth.context import CheckerContext

_SQL = """
SELECT
    n.nspname  AS schema,
    t.relname  AS table,
    c.conname  AS constraint_name,
    (SELECT array_agg(a.attname ORDER BY i.ord)
       FROM unnest(c.conkey) WITH ORDINALITY AS i(attnum, ord)
       JOIN pg_attribute a
         ON a.attrelid = c.conrelid AND a.attnum = i.attnum) AS fk_columns,
    rn.nspname AS referenced_schema,
    rt.relname AS referenced_table,
    (SELECT array_agg(a.attname ORDER BY i.ord)
       FROM unnest(c.confkey) WITH ORDINALITY AS i(attnum, ord)
       JOIN pg_attribute a
         ON a.attrelid = c.confrelid AND a.attnum = i.attnum) AS referenced_columns
FROM pg_constraint c
JOIN pg_class      t  ON t.oid  = c.conrelid
JOIN pg_namespace  n  ON n.oid  = t.relnamespace
JOIN pg_class      rt ON rt.oid = c.confrelid
JOIN pg_namespace  rn ON rn.oid = rt.relnamespace
WHERE c.contype = 'f'
  AND c.confdeltype = 'a'
  {schema_filter}
ORDER BY n.nspname, t.relname, c.conname;
"""


class ForeignKeyWithoutOnDelete(RowChecker):
    name: ClassVar[str] = "fk_without_on_delete"
    description: ClassVar[str] = (
        "Foreign keys with no explicit ON DELETE policy (default NO ACTION)."
    )
    default_severity: ClassVar[Severity] = Severity.INFO
    sql: ClassVar[str] = _SQL

    def check_row(self, ctx: CheckerContext, row: dict) -> Issue | None:
        fk_cols = ", ".join(row["fk_columns"])
        ref_cols = ", ".join(row["referenced_columns"])
        child = f"{row['schema']}.{row['table']}"
        parent = f"{row['referenced_schema']}.{row['referenced_table']}"
        obj = f"{child}({fk_cols})"
        return self.issue(
            ctx,
            object_type="constraint",
            object_name=obj,
            message=(
                f"Foreign key {row['constraint_name']!r} on {obj} has no explicit "
                f"ON DELETE policy (defaults to NO ACTION)."
            ),
            suggestion=(
                f"ALTER TABLE {child} "
                f"DROP CONSTRAINT {row['constraint_name']}, "
                f"ADD CONSTRAINT {row['constraint_name']} "
                f"FOREIGN KEY ({fk_cols}) REFERENCES {parent}({ref_cols}) "
                f"ON DELETE RESTRICT;  -- or CASCADE / SET NULL, per intent"
            ),
        )


register(ForeignKeyWithoutOnDelete)
