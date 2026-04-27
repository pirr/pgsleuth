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

1. **Logical replication breaks.** Postgres' default `REPLICA IDENTITY` requires a primary key (or an explicit `REPLICA IDENTITY FULL`, which is expensive). Without one, `UPDATE` and `DELETE` rows can't be replicated downstream — your CDC pipeline silently drops events.
2. **No unambiguous row identity.** `ORM.update(event)`, "click to edit" UIs, audit trails, dedup jobs — they all assume a stable per-row identifier. Without a PK you end up with full-row equality checks, which become wrong the moment two rows happen to collide on every column.
3. **Most tools assume PKs exist.** pg_repack, pgloader, table-sync utilities, ORMs, schema migration tools — many simply refuse to operate on PK-less tables, or operate incorrectly.

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
