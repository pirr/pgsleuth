# redundant_index

> **Severity:** info
> Indexes whose column list is a strict prefix of another index on the same table.

## What it catches

Two btree indexes on the same table where one's column list is a strict leading prefix of the other's, same access method, same partial-predicate state, and either both unique or only the longer one is unique.

```sql
CREATE INDEX idx_a ON orders (user_id);                -- prefix
CREATE INDEX idx_b ON orders (user_id, created_at);    -- covers idx_a
-- ⚠️  idx_a is flagged as redundant
```

Excluded by design:

- **Partial indexes** are never reported as covering or as covered (the predicate could disagree).
- **Unique indexes** are never flagged as redundant against a non-unique cover (the uniqueness guarantee would be lost).
- **Different access methods** (gin, gist, hash, brin, btree) — never compared.

## Why it matters

The redundant index is pure cost with no benefit.

### 1. Write amplification — every write pays twice

Postgres updates **every** index whose key columns are touched by an `INSERT` / `UPDATE`. With the example above:

```sql
INSERT INTO orders (user_id, created_at, ...) VALUES (...);
```

Postgres has to:

1. Insert the row into the heap.
2. Insert into `idx_a (user_id)`.
3. Insert into `idx_b (user_id, created_at)`.

Step 2 is pure waste — `idx_b` already covers every query that `idx_a` could satisfy. Same for `UPDATE`s that change `user_id`. On a write-heavy table that's measurable extra latency, extra WAL volume, and extra autovacuum pressure.

### 2. Disk and shared-buffer space

The redundant index occupies disk and competes for buffer cache pages with indexes that actually pull weight. On a 50M-row table, an unnecessary index can be tens of GB and push hot pages out of cache.

### 3. Plan-time confusion

The planner has to consider both indexes for every relevant query and pick one. Usually it picks correctly. Occasionally it doesn't — under skewed statistics or after a stats refresh, plan flips between the two cause inexplicable latency spikes. Dropping the loser eliminates the choice.

This is **info severity** because the cost is real but rarely production-critical, and false positives are conceivable (see *When to ignore*).

## How to fix

Drop the redundant one:

```sql
DROP INDEX orders_user_id_idx;   -- the prefix index
```

If the table is large and writes are heavy, use `CONCURRENTLY` to avoid a brief lock:

```sql
DROP INDEX CONCURRENTLY orders_user_id_idx;
```

## When to ignore

A few legitimate cases where the prefix index actually pulls weight:

- **Selectivity skew** — the prefix index is much smaller and Postgres uses it for `IS NOT NULL` / count-style queries. Rare, but verify with `EXPLAIN` before dropping.
- **Different `INCLUDE` / `WHERE` clauses** that the rule doesn't currently model. (pgsleuth treats partial indexes as ineligible, but `INCLUDE` columns aren't exposed in the simple comparison.)
- **Index-only scan vs index scan** — the prefix index has fewer columns, so its leaf pages are denser; for one specific query it might index-only scan where the longer index can't.

In every case: confirm with `EXPLAIN (ANALYZE, BUFFERS)` before dropping. If you're keeping it deliberately:

```toml
[pgsleuth.checkers.redundant_index]
enabled = false
```

Or escalate severity once you've cleaned house, so a *new* redundant index gets caught:

```toml
[pgsleuth.checkers.redundant_index]
severity = "warning"
```

## See also

- [PostgreSQL documentation — Multicolumn indexes](https://www.postgresql.org/docs/current/indexes-multicolumn.html)
- Related rule: [`missing_fk_index`](missing_fk_index.md) — sometimes a "redundant" prefix is actually the FK index, and the longer cover is the real redundancy.
