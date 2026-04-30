"""Sequences whose next value would collide with rows already in the table.

After `pg_restore` (or a manual data load that bypassed the sequence), a
sequence can be left below the maximum value already present in its owning
column. The next `INSERT` then trips a unique-violation on the primary key.

We use `pg_depend` to find sequences owned by a column, then compare the
sequence's own `last_value`/`is_called`/`increment_by` triple against
`MAX(owner_column)`. We read directly from the sequence relation rather than
`pg_sequences` because the latter exposes `last_value` as NULL until the
sequence has been called for the first time.
"""

from __future__ import annotations

from typing import ClassVar, Iterable

import psycopg
from psycopg import sql

from pgsleuth.checkers.base import Checker, Issue, Severity, register
from pgsleuth.context import CheckerContext
from pgsleuth.db.catalog import iter_objects

_OWNED_SEQUENCES_SQL = """
SELECT
    sn.nspname       AS seq_schema,
    s.relname        AS seq_name,
    tn.nspname       AS table_schema,
    t.relname        AS table_name,
    a.attname        AS column_name,
    seq.seqincrement AS increment_by
FROM pg_class       s
JOIN pg_namespace   sn  ON sn.oid = s.relnamespace
JOIN pg_sequence    seq ON seq.seqrelid = s.oid
JOIN pg_depend      d   ON d.objid = s.oid AND d.classid = 'pg_class'::regclass
JOIN pg_class       t   ON t.oid = d.refobjid
JOIN pg_namespace   tn  ON tn.oid = t.relnamespace
JOIN pg_attribute   a   ON a.attrelid = t.oid AND a.attnum = d.refobjsubid
WHERE s.relkind = 'S'
  AND d.deptype = 'a'
  AND t.relkind IN ('r', 'p')
  {schema_filter}
ORDER BY sn.nspname, s.relname;
"""


class SequenceDrift(Checker):
    name: ClassVar[str] = "sequence_drift"
    description: ClassVar[str] = (
        "Sequences whose last_value is below MAX of the column they back "
        "(typically a leftover from pg_restore)."
    )
    default_severity: ClassVar[Severity] = Severity.ERROR
    min_version: ClassVar[int] = 100000  # pg_sequence relation lands in PG10

    def run(self, ctx: CheckerContext) -> Iterable[Issue]:
        rows = iter_objects(
            ctx,
            _OWNED_SEQUENCES_SQL,
            schema_alias="tn",
            schema_key="table_schema",
            table_key="table_name",
        )
        for row in rows:
            seq_qualified = f"{row['seq_schema']}.{row['seq_name']}"
            next_value = self._next_value(
                ctx.conn,
                row["seq_schema"],
                row["seq_name"],
                row["increment_by"],
            )
            max_value = self._max_value(
                ctx.conn,
                row["table_schema"],
                row["table_name"],
                row["column_name"],
            )

            if next_value is None or max_value is None:
                continue
            if next_value > max_value:
                continue

            obj = seq_qualified
            yield self.issue(
                ctx,
                object_type="sequence",
                object_name=obj,
                message=(
                    f"Sequence {obj} would issue {next_value} next, but "
                    f"MAX({row['table_schema']}.{row['table_name']}."
                    f"{row['column_name']})={max_value} — next insert will collide."
                ),
                suggestion=(
                    f"SELECT setval('{obj}', "
                    f"(SELECT MAX({row['column_name']}) FROM "
                    f"{row['table_schema']}.{row['table_name']}));"
                ),
                extra={
                    "next_value": str(next_value),
                    "max_value": str(max_value),
                },
            )

    @staticmethod
    def _next_value(
        conn: psycopg.Connection,
        schema: str,
        name: str,
        increment_by: int,
    ) -> int | None:
        # Read directly from the sequence relation. pg_sequences.last_value is
        # NULL while is_called=false, which would mask exactly the drift state
        # we care about (sequence reset but never advanced).
        query = sql.SQL("SELECT last_value, is_called FROM {seq}").format(
            seq=sql.Identifier(schema, name),
        )
        with conn.cursor() as cur:
            cur.execute(query)
            row = cur.fetchone()
        if not row:
            return None
        last_value, is_called = row
        return last_value + increment_by if is_called else last_value

    @staticmethod
    def _max_value(
        conn: psycopg.Connection,
        schema: str,
        table: str,
        column: str,
    ) -> int | None:
        query = sql.SQL("SELECT MAX({col}) FROM {tbl}").format(
            col=sql.Identifier(column),
            tbl=sql.Identifier(schema, table),
        )
        with conn.cursor() as cur:
            cur.execute(query)
            row = cur.fetchone()
        return row[0] if row and row[0] is not None else None


register(SequenceDrift)
