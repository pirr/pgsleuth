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

`NOT VALID` is a tool, not a state to live in. Three concrete problems with leaving it.

### 1. The constraint is a lie about your data

Setup:

```sql
-- already in the table from a buggy older release:
INSERT INTO orders VALUES (1, -50);   -- total = -50, definitely invalid

-- fix lands later, the team adds the constraint quickly to unblock a deploy:
ALTER TABLE orders
    ADD CONSTRAINT orders_total_positive CHECK (total >= 0) NOT VALID;
-- the team plans to run VALIDATE CONSTRAINT in a follow-up window.
-- The follow-up never happens.
```

Now check what the schema *says* versus what's actually true:

```sql
\d orders
-- ...
-- Check constraints:
--   "orders_total_positive" CHECK (total >= 0)

SELECT * FROM orders WHERE total < 0;
--  id | total
-- ----+-------
--   1 |   -50         ← still there. The constraint isn't enforcing what it claims.
```

A junior developer reads the schema, assumes `total >= 0` holds, and writes a query / report / dashboard that silently misbehaves on the rows the constraint was supposed to prevent. The bug looks impossible — until you `\d` and miss the absence of "VALID."

### 2. Reporting and analytics break silently

For a `NOT VALID` foreign key, the failure mode is orphan rows:

```sql
ALTER TABLE orders
    ADD CONSTRAINT orders_user_id_fkey FOREIGN KEY (user_id) REFERENCES users(id) NOT VALID;

-- the FK enforces against new INSERTs, but old orphans stay:
SELECT o.id, o.user_id FROM orders o
LEFT JOIN users u ON u.id = o.user_id
WHERE u.id IS NULL;
-- returns rows that "shouldn't exist" per the schema
```

Any query that assumes "every order has a user" — joins, dashboards, billing reports — quietly produces wrong numbers.

### 3. Subsequent migrations behave unpredictably

Some operations (`ATTACH PARTITION`, `INHERIT`, validation propagation) treat `NOT VALID` constraints differently from validated ones. You hit edge cases at exactly the moment you don't have time for them.

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
