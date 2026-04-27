# missing_primary_key

> **Severity:** warning
> Ordinary tables without a primary key.

## What it catches

Any ordinary (`relkind = 'r'`) non-partition table that has no primary-key index.

```sql
CREATE TABLE events (
    user_id  bigint,
    payload  jsonb,
    occurred timestamptz
);
-- ⚠️  flagged: no PK
```

Partitions of a partitioned table are excluded — the parent's PK propagates and pgsleuth doesn't double-flag.

## Why it matters

Three concrete consequences, in roughly increasing severity:

### 1. Logical replication / CDC silently breaks

Postgres' default `REPLICA IDENTITY` is `DEFAULT`, which means "use the primary key." If there's no PK, an `UPDATE` or `DELETE` on the table emits a WAL record with **no row identity** — there's nothing for the downstream replica or CDC consumer to match against.

What you actually see, depending on your stack:

```text
-- on the publisher, when a logical replication slot tries to decode an UPDATE
ERROR:  cannot update table "events" because it does not have a replica identity
        and publishes updates
HINT:   To enable updating the table, set REPLICA IDENTITY using ALTER TABLE.
```

Or, more insidiously, on the consumer (Debezium, pglogical, etc.): the row event arrives with `before = null`, the consumer can't determine which downstream row to update, and either errors or — worst case — silently drops the event. You discover this when staging and prod diverge.

The escape hatch is `ALTER TABLE events REPLICA IDENTITY FULL`, which writes the full old-row image into every WAL record. It works, but it bloats WAL by 2–10× depending on row width. A PK is almost always cheaper.

### 2. No unambiguous row identity

`ORM.update(event)`, "click to edit" UIs, audit trails, dedup jobs — they all assume a stable per-row identifier. Without a PK, the application falls back to "find the row by all its columns," which is:

- **Wrong as soon as two rows are byte-identical.** `UPDATE events SET ... WHERE col1 = ? AND col2 = ? AND ...` will silently update *both* rows (or fail-on-multiple, depending on ORM).
- **Slow.** Equality checks on every column, no index to help.
- **Brittle.** The day someone adds a new column, every "row identity" code path needs rewriting.

### 3. Most tooling refuses to work

`pg_repack`, `pgloader`, online migration tools (`pg-osc`, `pt-online-schema-change` for the MySQL world), most ORMs' "upsert" helpers — many simply refuse to operate on PK-less tables, or operate incorrectly. You discover this the day you finally need to run one.

The common case where this slips in: someone wrote `CREATE TABLE` by hand for a "temporary" log/staging table, the table outlived the temporary, and it never got a PK.

## How to fix

Add a synthetic identity column:

```sql
ALTER TABLE events ADD COLUMN id bigserial PRIMARY KEY;
```

Or — better, when one obvious unique column already exists — promote it:

```sql
ALTER TABLE events ADD PRIMARY KEY (event_id);  -- if event_id is already unique + non-null
```

For very large tables, do this in two steps to avoid a long lock:

```sql
CREATE UNIQUE INDEX CONCURRENTLY events_pkey ON events (event_id);
ALTER TABLE events ADD CONSTRAINT events_pkey PRIMARY KEY USING INDEX events_pkey;
```

## When to ignore

Genuinely PK-less tables exist:

- **Append-only log tables that no system ever updates or deletes from**, and that no replication target consumes. Rare but real.
- **Materialized views** masquerading as tables — they're refreshed wholesale, identity per row doesn't matter.

If yours is one of these, suppress for that table:

```toml
[pgsleuth]
exclude_tables = ["^events_archive_"]
```

## See also

- [PostgreSQL documentation — Constraints](https://www.postgresql.org/docs/current/ddl-constraints.html)
