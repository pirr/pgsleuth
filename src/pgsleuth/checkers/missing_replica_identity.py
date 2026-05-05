"""Tables whose REPLICA IDENTITY would silently break logical replication.

Postgres needs a way to identify a row in WAL when an UPDATE or DELETE is
replicated logically. The four `pg_class.relreplident` values:

    'd' default → uses the table's PRIMARY KEY
    'i' index   → uses a specific UNIQUE NOT-NULL non-deferrable index
    'f' full    → the entire pre-image is logged (works, but expensive)
    'n' nothing → no identity recorded (UPDATE/DELETE cannot replicate)

A table fails the check when its effective replica identity is empty:

    - relreplident = 'd' AND no PRIMARY KEY exists, or
    - relreplident = 'n'  (explicitly disabled)

Compared with `missing_primary_key` this rule is strictly narrower: a table
with `REPLICA IDENTITY USING INDEX` or `REPLICA IDENTITY FULL` set explicitly
has a working replica identity even without a PK and is *not* flagged here.
"""

from __future__ import annotations

from typing import ClassVar

from pgsleuth.checkers.base import Issue, RowChecker, Severity, register
from pgsleuth.context import CheckerContext

_SQL = """
SELECT
    n.nspname       AS schema,
    t.relname       AS table,
    t.relreplident  AS replident
FROM pg_class      t
JOIN pg_namespace  n ON n.oid = t.relnamespace
WHERE t.relkind IN ('r', 'p')
  AND t.relpersistence = 'p'   -- skip temp + unlogged: not replicated
  AND NOT t.relispartition     -- partitions inherit identity from the parent
  AND (
        (t.relreplident = 'd' AND NOT EXISTS (
            SELECT 1 FROM pg_index i
            WHERE i.indrelid = t.oid AND i.indisprimary
        ))
        OR t.relreplident = 'n'
      )
  {schema_filter}
ORDER BY n.nspname, t.relname;
"""


class MissingReplicaIdentity(RowChecker):
    name: ClassVar[str] = "missing_replica_identity"
    description: ClassVar[str] = (
        "Tables whose REPLICA IDENTITY is empty — UPDATE/DELETE will not replicate logically."
    )
    default_severity: ClassVar[Severity] = Severity.WARNING
    # PG 10's pg_class.relispartition is the earliest catalog column we touch.
    min_version: ClassVar[int] = 100000
    sql: ClassVar[str] = _SQL

    def check_row(self, ctx: CheckerContext, row: dict) -> Issue | None:
        obj = f"{row['schema']}.{row['table']}"
        if row["replident"] == "n":
            message = (
                f"Table {obj} has REPLICA IDENTITY NOTHING — "
                f"UPDATE/DELETE will not replicate via logical decoding."
            )
            suggestion = (
                f"ALTER TABLE {obj} REPLICA IDENTITY DEFAULT;  "
                f"-- or USING INDEX <unique_idx>, or FULL"
            )
        else:
            message = (
                f"Table {obj} has REPLICA IDENTITY DEFAULT but no primary key — "
                f"UPDATE/DELETE will not replicate via logical decoding."
            )
            suggestion = (
                f"ALTER TABLE {obj} ADD PRIMARY KEY (...);  "
                f"-- or REPLICA IDENTITY USING INDEX <unique_idx>, or FULL"
            )
        return self.issue(
            ctx,
            object_type="table",
            object_name=obj,
            message=message,
            suggestion=suggestion,
        )


register(MissingReplicaIdentity)
