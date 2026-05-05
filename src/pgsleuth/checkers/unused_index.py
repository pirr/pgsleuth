"""Indexes that have never been scanned, per `pg_stat_user_indexes.idx_scan = 0`.

Pure write-cost: every INSERT / UPDATE on the table maintains the index,
autovacuum reads its pages, it eats disk and shared buffers — and queries
pay nothing back. Drop or justify.

The check is intentionally narrow. Indexes that enforce correctness, not just
provide an access path, are excluded:

- `indisprimary` — primary keys.
- `indisunique` — unique constraints.
- `indisexclusion` — EXCLUDE constraints.
- `indisvalid = false` — failed CONCURRENT builds; nothing scans them by design.

Stats are read live from the cluster, so a freshly-created index always looks
"unused" until something scans it. Treat the report as a starting list, not a
verdict — the rule doc explains how to vet a finding before dropping.
"""

from __future__ import annotations

from typing import ClassVar

from pgsleuth.checkers.base import Issue, RowChecker, Severity, register
from pgsleuth.context import CheckerContext

_SQL = """
SELECT
    n.nspname  AS schema,
    t.relname  AS table,
    ic.relname AS index_name,
    s.idx_scan AS scans
FROM pg_stat_user_indexes s
JOIN pg_index     i  ON i.indexrelid = s.indexrelid
JOIN pg_class     ic ON ic.oid       = i.indexrelid
JOIN pg_class     t  ON t.oid        = i.indrelid
JOIN pg_namespace n  ON n.oid        = t.relnamespace
WHERE s.idx_scan = 0
  AND i.indisvalid
  AND NOT i.indisprimary
  AND NOT i.indisunique
  AND NOT i.indisexclusion
  AND t.relkind IN ('r', 'p')
  {schema_filter}
ORDER BY n.nspname, t.relname, ic.relname;
"""


class UnusedIndex(RowChecker):
    name: ClassVar[str] = "unused_index"
    description: ClassVar[str] = (
        "Indexes never scanned per pg_stat_user_indexes — pure write-cost, no read benefit."
    )
    default_severity: ClassVar[Severity] = Severity.INFO
    sql: ClassVar[str] = _SQL

    def check_row(self, ctx: CheckerContext, row: dict) -> Issue | None:
        obj = f"{row['schema']}.{row['index_name']}"
        return self.issue(
            ctx,
            object_type="index",
            object_name=obj,
            message=(
                f"Index {row['index_name']!r} on {row['schema']}.{row['table']} "
                f"has never been scanned (pg_stat_user_indexes.idx_scan = 0)."
            ),
            suggestion=f"DROP INDEX CONCURRENTLY {row['schema']}.{row['index_name']};",
        )


register(UnusedIndex)
