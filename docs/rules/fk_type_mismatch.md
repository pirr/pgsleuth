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

1. **Silent casts on every join.** `users.id = orders.user_id` becomes `users.id = orders.user_id::bigint` under the hood. The cast is per-row, the planner can't always push it down to use an index, and you get nested-loop plans where you wanted hash joins.
2. **Index unusability in some plans.** An index on `orders(user_id)` (integer) is **not directly usable** to satisfy a query parameterized as `bigint`. Postgres sometimes works around it, sometimes doesn't — the failures are version- and statistics-dependent, which makes them maddening to debug.
3. **Range mismatch.** A 32-bit `integer` FK can't reference all valid 64-bit `bigint` parent rows. The constraint will eventually start rejecting writes that should logically succeed once the parent table grows past 2.1B.

This is almost always a bug — someone copied a `serial` column shape onto an FK that should have been `bigint`, or evolved the parent's type without evolving the child.

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
