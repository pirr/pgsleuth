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

The redundant index is pure cost with no benefit:

1. **Write amplification.** Every `INSERT` / `UPDATE` on an indexed column updates *every* matching index. A redundant index doubles the write work for any operation that touches its key columns.
2. **Disk and shared-buffer space.** Bloated table, bloated cache footprint.
3. **Plan-time confusion.** The planner has to consider both indexes for every relevant query and pick one — usually correctly, occasionally not. Removing the loser saves the planner work and makes plan stability easier.

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
