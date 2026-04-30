"""Sequence-backed columns whose sequence is approaching its type's max value.

Complements `primary_key_type`, which flags `int4`/`smallint` PKs by type
alone (preventive). This checker is reactive: it fires when the actual
sequence usage is past a threshold (default 70%) of its `max_value`, so a
team gets a warning *before* the next `INSERT` raises
`nextval: reached maximum value of sequence`.

Scope is broader than just PKs — any column owned by a sequence (`SERIAL`,
`bigserial`, `GENERATED ... AS IDENTITY`, or a manually-OWNED-BY sequence)
qualifies. Non-PK serial-backed columns can overflow the same way; the type
of column doesn't matter to the underlying failure mode.
"""

from __future__ import annotations

from typing import ClassVar, Iterable

from pgsleuth.checkers.base import Checker, Issue, Severity, register
from pgsleuth.context import CheckerContext
from pgsleuth.db.catalog import iter_objects

# Fraction of `max_value` at which we flag a sequence-backed column.
# 0.70 leaves headroom for the team to plan a migration before the
# overflow becomes urgent.
_DEFAULT_THRESHOLD = 0.70

_SQL = """
SELECT
    ns_tbl.nspname        AS schema,
    tbl.relname           AS table,
    col.attname           AS column,
    format_type(col.atttypid, col.atttypmod) AS column_type,
    ns_seq.nspname        AS seq_schema,
    seq.relname           AS seq_name,
    ps.last_value::numeric AS last_value,
    ps.max_value::numeric  AS max_value
FROM pg_class       seq
JOIN pg_namespace   ns_seq ON ns_seq.oid = seq.relnamespace
JOIN pg_depend      dep    ON dep.objid = seq.oid
                          AND dep.classid = 'pg_class'::regclass
                          AND dep.deptype = 'a'
JOIN pg_class       tbl    ON tbl.oid = dep.refobjid
JOIN pg_namespace   ns_tbl ON ns_tbl.oid = tbl.relnamespace
JOIN pg_attribute   col    ON col.attrelid = tbl.oid
                          AND col.attnum = dep.refobjsubid
JOIN pg_sequences   ps     ON ps.schemaname = ns_seq.nspname
                          AND ps.sequencename = seq.relname
WHERE seq.relkind = 'S'
  AND tbl.relkind IN ('r', 'p')
  AND NOT ps.cycle
  AND ps.last_value IS NOT NULL
  {schema_filter}
ORDER BY ns_tbl.nspname, tbl.relname, col.attname;
"""


class ColumnValueAtRisk(Checker):
    name: ClassVar[str] = "column_value_at_risk"
    description: ClassVar[str] = (
        "Sequence-backed columns whose sequence is past 70% of its type's max value."
    )
    default_severity: ClassVar[Severity] = Severity.WARNING
    # pg_sequences view (used for last_value/max_value/cycle) is PG10+.
    min_version: ClassVar[int] = 100000

    def run(self, ctx: CheckerContext) -> Iterable[Issue]:
        for row in iter_objects(ctx, _SQL, schema_alias="ns_tbl"):
            last_value = row["last_value"]
            max_value = row["max_value"]
            if max_value <= 0:
                continue  # malformed sequence; ignore rather than div-by-zero
            ratio = float(last_value) / float(max_value)
            if ratio < _DEFAULT_THRESHOLD:
                continue

            obj = f"{row['schema']}.{row['table']}.{row['column']}"
            seq = f"{row['seq_schema']}.{row['seq_name']}"
            pct = round(ratio * 100, 1)
            yield self.issue(
                ctx,
                object_type="column",
                object_name=obj,
                message=(
                    f"Column {obj} ({row['column_type']}) is at {pct}% of "
                    f"sequence {seq}'s max ({last_value} / {max_value})."
                ),
                suggestion=(
                    f"ALTER TABLE {row['schema']}.{row['table']} "
                    f"ALTER COLUMN {row['column']} TYPE bigint;"
                ),
                extra={
                    "last_value": str(last_value),
                    "max_value": str(max_value),
                    "ratio": f"{ratio:.6f}",
                    "sequence": seq,
                },
            )


register(ColumnValueAtRisk)
