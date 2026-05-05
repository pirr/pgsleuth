# unused_index

> **Severity:** info
> Indexes that have never been scanned, per `pg_stat_user_indexes.idx_scan = 0`.

## What it catches

A non-correctness index whose `idx_scan` counter is zero in `pg_stat_user_indexes` — i.e. nothing has used it for a lookup since stats were last reset.

```sql
CREATE INDEX cold_lookup ON orders (legacy_external_id);
-- months pass, no query references legacy_external_id
-- ⚠️  cold_lookup is flagged: idx_scan = 0
```

Excluded by design — these indexes provide correctness, not just an access path:

- `indisprimary` — primary keys.
- `indisunique` — unique constraints / unique indexes.
- `indisexclusion` — EXCLUDE constraints (range overlap, etc.).
- `indisvalid = false` — failed `CREATE INDEX CONCURRENTLY`; never scanned by design.

## Why it matters

An unused index is pure write-cost with no read benefit:

### 1. Every write pays for the index

Postgres updates **every** index whose key columns are touched by an `INSERT` / `UPDATE`. A 50M-row table with three unused indexes pays three times the WAL volume, three times the autovacuum work, and three times the index-leaf-page cache pressure on each write — for queries that never use them.

### 2. Disk and shared-buffer space

Cold indexes still occupy disk pages and compete for shared-buffer cache against indexes that actually pull weight. On a hot table, evicting useful pages to keep cold ones warm is a quiet performance regression.

### 3. Maintenance amplification

`VACUUM`, `REINDEX`, and `ANALYZE` all walk every index. Cold indexes lengthen routine maintenance and bulk migrations (e.g. `pg_repack`) for no payoff.

This is **info severity** because the cost is real but rarely incident-causing, and false positives are easy to trip into (see below).

## How to fix

Drop it. Use `CONCURRENTLY` on a live system:

```sql
DROP INDEX CONCURRENTLY public.cold_lookup;
```

If the index has a clear owner (a feature flag, a batch job, a DR fallback), record the rationale in a baseline entry instead of dropping:

```bash
pgsleuth baseline write --dsn $DSN
```

## When to ignore

A finding is not always a defect. Vet a flagged index before dropping:

- **Recently created index.** `idx_scan = 0` is the same value a brand-new index reports until its first scan. Wait at least one statistics window (a day, a week — whatever your traffic shape demands) before acting.
- **Stats reset recently.** `pg_stat_reset()` or a `pg_stat_statements_reset()` zeros the counters. Cross-check with `pg_stat_get_db_stat_reset_time(d.oid)` from `pg_database`.
- **Index used only by infrequent jobs.** Monthly reports, end-of-quarter audits, DR-failover queries. Real workloads, just not in the recent stats window.
- **Index used only on the primary, but pgsleuth is run against a replica.** `idx_scan` on a replica reflects replica reads, not primary reads — a hot primary index can show zero on the replica.
- **Hot-standby or read-replica role.** Same caveat — verify against the right node.

If you're keeping a flagged index deliberately, suppress just that finding via baseline mode (preferred) or disable the rule cluster-wide:

```toml
[pgsleuth.checkers.unused_index]
enabled = false
```

Or escalate severity once you've cleaned house and want CI to fail on *new* unused indexes:

```toml
[pgsleuth.checkers.unused_index]
severity = "warning"
```

## See also

- [`redundant_index`](redundant_index.md) — finds prefix-redundant indexes via the catalog (no stats needed).
- PostgreSQL documentation — [Statistics views: `pg_stat_user_indexes`](https://www.postgresql.org/docs/current/monitoring-stats.html#MONITORING-PG-STAT-ALL-INDEXES-VIEW).
