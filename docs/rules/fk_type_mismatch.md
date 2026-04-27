# fk_type_mismatch

> **Severity:** error
> Foreign key column type differs from the referenced column type.

## What it catches

A `FOREIGN KEY` constraint where the child column's type doesn't match the parent column's type byte-for-byte (`integer` vs `bigint`, `text` vs `varchar(64)`, `uuid` vs `text`, etc.).

```sql
CREATE TABLE users (id bigint PRIMARY KEY);

CREATE TABLE orders (
    id      bigserial PRIMARY KEY,
    user_id integer REFERENCES users(id)   -- ⚠️  flagged: integer vs bigint
);
```

The check compares `format_type(...)` output for child and parent columns, so it catches type-modifier mismatches too (e.g. `varchar(64)` vs `varchar(128)`).

## Why it matters

Three real costs:

### 1. Silent casts on every join — sometimes blocking index use

When the column types differ, every join condition `users.id = orders.user_id` is rewritten internally as `users.id = orders.user_id::bigint`. The cast is per-row, and the planner can't always push it through to use an existing index.

Concretely, with the schema above (`users.id` is `bigint`, `orders.user_id` is `integer`, both columns indexed):

```sql
EXPLAIN ANALYZE
SELECT u.* FROM users u JOIN orders o ON u.id = o.user_id WHERE u.id = 42;
```

```text
-- Types match (both bigint):
Nested Loop  (cost=0.85..16.91 rows=2 width=...)
  -> Index Scan using users_pkey on users     (rows=1)
        Index Cond: (id = 42)
  -> Index Scan using orders_user_id_idx on orders   ← uses the index
        Index Cond: (user_id = 42)

-- Types differ (orders.user_id is integer):
Nested Loop  (cost=0.42..5421.13 rows=2 width=...)
  -> Index Scan using users_pkey on users     (rows=1)
  -> Seq Scan on orders                       ← falls back to seq scan
        Filter: ((user_id)::bigint = 42)
```

The seq scan is on every joined row. At 10M rows in `orders`, what should be a microsecond join becomes a several-second one.

### 2. Range mismatch eventually rejects writes

A 32-bit `integer` FK can't reference all valid 64-bit `bigint` parent rows. Once the parent table grows past 2.1B and starts issuing `bigint` ids that don't fit in `integer`, every `INSERT` into the child fails:

```text
INSERT INTO orders (user_id, ...) VALUES (3000000000, ...);
ERROR:  integer out of range
```

This is the same overflow described in [`primary_key_type`](primary_key_type.md), but on the child side and harder to diagnose because the schema *looks* internally consistent.

### 3. The opposite: child type wider than parent

If the parent is `integer` and the child is `bigint`, the join still casts (one direction or the other) and the same index-use issues apply. There's no scenario where mismatched types are *better* than matched.

This is almost always a bug — someone copied a `serial` column shape onto an FK that should have been `bigint`, or evolved the parent's type (`primary_key_type` migration) without evolving the children.

## How to fix

Bring the child's type into alignment with the parent. The suggestion in the finding does exactly this:

```sql
ALTER TABLE orders ALTER COLUMN user_id TYPE bigint;
```

For large tables this is a full table rewrite. Plan accordingly:

```sql
-- safer for big tables: shadow column + backfill + swap
ALTER TABLE orders ADD COLUMN user_id_new bigint;
UPDATE orders SET user_id_new = user_id WHERE user_id_new IS NULL;
-- (in batches, with a recheck loop)
ALTER TABLE orders DROP CONSTRAINT orders_user_id_fkey;
ALTER TABLE orders DROP COLUMN user_id;
ALTER TABLE orders RENAME COLUMN user_id_new TO user_id;
ALTER TABLE orders ADD FOREIGN KEY (user_id) REFERENCES users(id);
```

## When to ignore

Effectively never. The narrow exception:

- **Cross-domain references where the cast is intentional and stable** — e.g. a `varchar(50)` column that references a `text` PK and you know all values fit. Even then, just match the types — the rule isn't worth fighting.

If you must, suppress for the specific table:

```toml
[pgsleuth]
exclude_tables = ["^legacy_imports$"]
```

## See also

- [PostgreSQL documentation — ALTER TABLE](https://www.postgresql.org/docs/current/sql-altertable.html)
- Related rule: [`primary_key_type`](primary_key_type.md) — fixing a parent PK from `integer` to `bigint` will cascade into this rule firing on every child FK.
