# missing_replica_identity

> **Severity:** warning
> Tables whose `REPLICA IDENTITY` is effectively empty — `UPDATE` and `DELETE` will not replicate via logical decoding.

## What it catches

Postgres needs a way to identify a row when an `UPDATE` or `DELETE` is replicated logically. `pg_class.relreplident` carries one of four values per table:

| Value | Meaning | Replicates UPDATE/DELETE? |
| --- | --- | --- |
| `'d'` (default) | Uses the table's `PRIMARY KEY`. | Only if a PK exists. |
| `'i'` (index) | Uses a specific `UNIQUE NOT NULL` non-deferrable index. | Yes. |
| `'f'` (full) | Logs the entire pre-image. | Yes (expensive). |
| `'n'` (nothing) | No identity recorded. | **No.** |

A table fails this check when its **effective** replica identity is empty:

```sql
-- ❌ flagged: default identity, but no primary key
CREATE TABLE events (user_id bigint, payload jsonb);

-- ❌ flagged: explicit "nothing"
CREATE TABLE audit_log (...);
ALTER TABLE audit_log REPLICA IDENTITY NOTHING;

-- ✅ not flagged: index-based identity covers it
CREATE TABLE events (event_uuid uuid NOT NULL, payload jsonb);
CREATE UNIQUE INDEX events_uuid_idx ON events (event_uuid);
ALTER TABLE events REPLICA IDENTITY USING INDEX events_uuid_idx;

-- ✅ not flagged: FULL works (pricey, but works)
ALTER TABLE events REPLICA IDENTITY FULL;
```

Excluded by design:

- **Partitions** (`relispartition`) — replica identity is set on the partitioned root and inherited.
- **Temp and unlogged tables** (`relpersistence != 'p'`) — never replicated, so the column doesn't matter.

## Why it matters

The first time a team adopts logical replication — a CDC pipeline (Debezium, pglogical), a logical replication slot for blue/green migration, a downstream warehouse using `pg_logical_slot_get_changes` — every table flagged by this rule becomes a silent landmine.

Two failure shapes you actually see:

```text
-- on the publisher
ERROR:  cannot update table "events" because it does not have a replica identity
        and publishes updates
HINT:   To enable updating the table, set REPLICA IDENTITY using ALTER TABLE.
```

Or, more insidiously, on the consumer side: the row event arrives with `before = null`, the consumer can't identify which downstream row to update, and either errors out or silently drops the event. You discover the divergence weeks later.

This is **warning severity** because the cost only materializes when logical replication enters the picture. Teams on physical replication only can downgrade — see *When to ignore*.

## How to fix

The right answer depends on why the table has no identity:

### Add a primary key (preferred)

```sql
ALTER TABLE events ADD COLUMN id bigserial PRIMARY KEY;
-- or, when an existing column is already unique + non-null
ALTER TABLE events ADD PRIMARY KEY (event_uuid);
```

A PK fixes this rule *and* `missing_primary_key`, *and* unlocks `pg_repack` / online schema-change tooling. Almost always the right move.

### Or point at an existing unique index

```sql
ALTER TABLE events REPLICA IDENTITY USING INDEX events_unique_idx;
```

Requires a `UNIQUE` index on `NOT NULL` columns, non-deferrable, non-partial. Useful when adding a PK is genuinely not desirable (e.g., the existing unique key is composite and already serves the role).

### Or use FULL when no useful key exists

```sql
ALTER TABLE events REPLICA IDENTITY FULL;
```

Logs the full row image with every change. Works on any table, but bloats WAL by 2–10× depending on row width — only choose this when nothing else fits.

## When to ignore

- **Append-only log tables** never updated or deleted, and never consumed by logical replication. The replica-identity gap is theoretical.
- **No logical replication anywhere on the roadmap.** Some teams run on physical replication only and never plan to add CDC. Reasonable to disable or downgrade:

  ```toml
  [pgsleuth.checkers.missing_replica_identity]
  enabled = false
  # or:
  severity = "info"
  ```

When you *do* eventually adopt logical rep, re-enable and run prune to catch what slipped in.

## See also

- [PostgreSQL — `ALTER TABLE … REPLICA IDENTITY`](https://www.postgresql.org/docs/current/sql-altertable.html#SQL-ALTERTABLE-REPLICA-IDENTITY).
- Related rule: [`missing_primary_key`](missing_primary_key.md) — broader scope; this rule is the strictly-correct subset for the replica-identity concern.
