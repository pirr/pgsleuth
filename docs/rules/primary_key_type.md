# primary_key_type

> **Severity:** warning
> Primary keys typed as `integer` or `smallint` will eventually overflow.

## What it catches

Any primary key column whose type is `integer` (32-bit, max ~2.1B) or `smallint` (16-bit, max ~32K).

```sql
CREATE TABLE orders (
    id      serial PRIMARY KEY,   -- ⚠️  serial = integer; flagged
    user_id bigint
);
```

The `bigint` / `bigserial` / `uuid` types are not flagged.

## Why it matters

The `id` column on a hot table grows monotonically. At ~2.1 billion rows your `INSERT`s start failing with `ERROR: integer out of range`. The migration to fix it — `ALTER TABLE ... ALTER COLUMN id TYPE bigint` — **rewrites the entire table**, including every index that touches `id`, and takes a full `ACCESS EXCLUSIVE` lock the whole time. On a 1TB table that's hours of downtime.

The fix at table-creation time is one extra letter (`bigserial`). The fix once you're already at 1B rows is an outage.

`smallint` PK is even worse: 32k rows is reachable in a single afternoon for any non-trivial application.

## How to fix

In place (best done while the table is still small):

```sql
ALTER TABLE orders ALTER COLUMN id TYPE bigint;
```

For large tables, the modern path is a [shadow column + backfill + swap](https://www.cybertec-postgresql.com/en/postgresql-bigint-conversion/) approach. This is intentionally not a one-liner — by the time you need it, you should plan it like a real migration.

For new tables, just use `bigint` from the start:

```sql
CREATE TABLE orders (
    id bigserial PRIMARY KEY,
    ...
);
```

Or `uuid` if you have a reason (distributed ID generation, no leakage of row counts):

```sql
CREATE TABLE orders (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    ...
);
```

## When to ignore

- **Reference / lookup tables with a hard cap on rows.** A `currencies` table will never need more than ~200 rows; `integer` is fine. `smallint` would be too.
- **Tables that are guaranteed to be deleted-and-rebuilt periodically** with row counts well below 2B.

If you're confident the cap won't be hit, suppress per-table:

```toml
[pgsleuth]
exclude_tables = ["^currencies$", "^locales$"]
```

Or per-checker if you've audited every PK and want the rule out of the way:

```toml
[pgsleuth.checkers.primary_key_type]
enabled = false
```

## See also

- [PostgreSQL documentation — Numeric types](https://www.postgresql.org/docs/current/datatype-numeric.html)
- Future related rule: `pk_value_at_risk` — flags PKs that are *already* close to overflow based on actual `MAX(id)`.
