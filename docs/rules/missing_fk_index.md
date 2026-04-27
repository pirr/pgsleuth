# missing_fk_index

> **Severity:** warning
> Foreign key columns without a leading index — slow cascades and slow joins.

## What it catches

Any `FOREIGN KEY` constraint whose column list is **not** the leading prefix of any index on the same table.

```sql
CREATE TABLE users (id bigserial PRIMARY KEY);

CREATE TABLE orders (
    id      bigserial PRIMARY KEY,
    user_id bigint REFERENCES users(id)   -- ⚠️  flagged: no index on (user_id)
);
```

The check uses `pg_constraint` joined to `pg_index`; an index whose first columns exactly match the FK column list satisfies the rule, even if it has additional trailing columns. Composite FKs are handled the same way.

## Why it matters

Two real costs, both invisible until production traffic finds them.

### 1. Parent-side `DELETE` / `UPDATE` seq-scans the child, **per row**

When you `DELETE` or `UPDATE` a row in the parent, Postgres must find every referencing row in the child to enforce the FK (or to cascade). Without an index on the FK column, that's a full table scan of the child — and Postgres does it **once per affected parent row**.

A migration that deletes 10,000 users:

```sql
DELETE FROM users WHERE deleted_at < now() - interval '2 years';
-- 10,000 rows deleted on prod
```

…silently runs 10,000 sequential scans on `orders`. With 50M rows in `orders`, that's 500 billion row visits before the migration finishes. The migration window blows up from a minute to half a day, and the table is locked the whole time.

### 2. The most common application query — parent-to-child join — falls off the index

```sql
SELECT * FROM users u JOIN orders o ON u.id = o.user_id WHERE u.id = $1;
```

This is the single most common query shape in any line-of-business app: "show me everything related to user X." With an index on `orders(user_id)` it's an index lookup. Without:

```text
Nested Loop  (cost=0.42..312918.47 rows=42 width=...)
  -> Index Scan using users_pkey on users   (rows=1)
  -> Seq Scan on orders                     ← scans all 50M rows
        Filter: (user_id = $1)
```

Fast in dev (10k rows), brutal in prod (50M rows). This is the single most common source of "the query was fast in dev and is now timing out in prod" tickets.

## How to fix

Add a btree index on the FK column(s):

```sql
CREATE INDEX ON orders (user_id);
```

For composite FKs, the index column order must match the FK column order:

```sql
CREATE INDEX ON line_items (order_id, sku);  -- if FK is (order_id, sku)
```

For very large tables, use `CREATE INDEX CONCURRENTLY` to avoid taking an `ACCESS EXCLUSIVE` lock on the table:

```sql
CREATE INDEX CONCURRENTLY ON orders (user_id);
```

## When to ignore

The rule is **not** worth following in two narrow cases:

- **Reference / lookup tables that never have rows deleted from the parent.** If `country_code` references `countries(code)` and `countries` is read-only, the cascade cost is zero. The join-path argument still applies, though — most of the time you do want the index anyway.
- **Tables that are append-only and never joined back to from the parent.** Pure event logs sometimes fit this. Be honest: if any analytics query joins `events` back to `users`, you do want the index.

If you're sure the rule doesn't apply, suppress for that specific table via config:

```toml
[pgsleuth]
exclude_tables = ["^events$"]
```

## See also

- [PostgreSQL documentation — Multicolumn indexes](https://www.postgresql.org/docs/current/indexes-multicolumn.html)
- Related rule: [`redundant_index`](redundant_index.md) — sometimes a "missing" FK index is actually present as a longer composite that already covers it.
