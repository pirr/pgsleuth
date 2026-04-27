# not_valid_constraints

> **Severity:** error
> Foreign keys or `CHECK` constraints added with `NOT VALID` and never validated.

## What it catches

Any constraint of type `f` (foreign key) or `c` (check) with `pg_constraint.convalidated = false`. These are constraints created via `ALTER TABLE ... ADD CONSTRAINT ... NOT VALID`, which **enforces the constraint for new rows but skips checking pre-existing rows**.

```sql
ALTER TABLE orders
    ADD CONSTRAINT orders_total_positive CHECK (total >= 0) NOT VALID;
-- ⚠️  flagged: pre-existing rows with total < 0 are tolerated
```

The expected follow-up is `ALTER TABLE ... VALIDATE CONSTRAINT ...`. When that step is forgotten, the constraint is in a halfway state forever.

## Why it matters

`NOT VALID` is a tool, not a state to live in. Three concrete problems with leaving it:

1. **The constraint is a lie about your data.** Anyone reading the schema sees `CHECK (total >= 0)` and assumes that holds. It doesn't — only for rows added after the constraint. Bug reports and data-corruption hunts go down the wrong path.
2. **Reporting and analytics break silently.** A query that joins on a `NOT VALID` foreign key may return rows with no matching parent — exactly the kind of orphan the constraint exists to prevent.
3. **Subsequent migrations behave unpredictably.** Some operations (e.g. `ALTER TABLE ... ATTACH PARTITION`) treat `NOT VALID` constraints differently from validated ones.

The whole point of `NOT VALID` is to *defer* the lock-heavy full-table scan, run it later, and then validate. Deferring forever defeats the deferral.

## How to fix

Run the validation:

```sql
ALTER TABLE orders VALIDATE CONSTRAINT orders_total_positive;
```

`VALIDATE CONSTRAINT` takes only a `SHARE UPDATE EXCLUSIVE` lock — it does **not** block reads or writes — so it's safe in production. It will fail loudly if any pre-existing row violates the constraint, at which point you have a real data problem to fix:

```sql
-- Find the offenders
SELECT * FROM orders WHERE total < 0;
```

Once data is clean, re-run `VALIDATE CONSTRAINT`.

For unvalidated foreign keys, the same pattern applies — `VALIDATE CONSTRAINT fk_name` does the work without blocking traffic.

## When to ignore

Two narrow cases:

- **You're mid-migration.** The whole point of `NOT VALID` is to add the constraint cheaply, then validate later. If the validation is genuinely scheduled for tomorrow, the rule firing today is just a reminder. (Suppress until your migration is done, then re-enable.)
- **Legacy data you've consciously decided to grandfather.** Rare, almost always a smell — the right answer is usually to fix the data, not the constraint.

In neither case should this rule stay disabled long-term. If you must:

```toml
[pgsleuth.checkers.not_valid_constraints]
enabled = false   # (re-enable after the migration window)
```

## See also

- [PostgreSQL documentation — ALTER TABLE](https://www.postgresql.org/docs/current/sql-altertable.html)
- The `VALIDATE CONSTRAINT` clause is documented under `ALTER TABLE` action *VALIDATE CONSTRAINT*.
