# sequence_drift

> **Severity:** error
> A sequence's next value is at or below `MAX()` of the column it backs.

## What it catches

For every sequence owned by a column (via `pg_depend.deptype = 'a'`, the relationship `bigserial` / `GENERATED ... AS IDENTITY` creates), pgsleuth compares the sequence's next-issue value to `MAX(owner_column)`. If `nextval ≤ MAX(...)`, the very next `INSERT` will collide.

```text
sequence orders_id_seq:  last_value=12345, is_called=true → next = 12346
column   orders.id   :  MAX = 89000
-- ⚠️  next INSERT will fail with duplicate key value
```

pgsleuth reads `last_value` directly from the sequence relation rather than from `pg_sequences`, because `pg_sequences.last_value` is `NULL` when `is_called = false` — exactly the state we care about (sequence reset but never advanced).

## Why it matters

This is the rule with the highest production-incident value of any in pgsleuth, because:

1. **The failure mode is "all writes to this table start raising `unique_violation`".** Hard outage, immediate paging, real money.
2. **The cause is invisible until it bites.** The schema looks fine. The data looks fine. The constraint is even being honored — there's just a sequence pointing at a row that already exists.
3. **The trigger is a routine operation.** `pg_restore` of a logical dump, manual `INSERT` with explicit `id`, `COPY` from a backup — anything that bypasses `nextval()` leaves the sequence behind. Most teams do at least one of these per quarter.

The first time this hits a team, debugging takes hours. After that, you start asking "did anyone restore something?" before checking the schema.

## How to fix

Reset the sequence to the column's max + 1:

```sql
SELECT setval(
    'public.orders_id_seq',
    (SELECT MAX(id) FROM public.orders)
);
```

`setval(seq, n)` makes the next `nextval` return `n + 1` — exactly what you want.

For batch fixes after a restore, generate the `setval` calls dynamically:

```sql
SELECT
    'SELECT setval(' || quote_literal(seq_oid::regclass::text) || ', '
    || '(SELECT MAX(' || quote_ident(col) || ') FROM '
    || quote_ident(tab_schema) || '.' || quote_ident(tab_name) || '));'
FROM (
    SELECT
        d.objid                  AS seq_oid,
        a.attname                AS col,
        n.nspname                AS tab_schema,
        c.relname                AS tab_name
    FROM pg_depend     d
    JOIN pg_class      c  ON c.oid = d.refobjid
    JOIN pg_namespace  n  ON n.oid = c.relnamespace
    JOIN pg_attribute  a  ON a.attrelid = c.oid AND a.attnum = d.refobjsubid
    WHERE d.deptype = 'a'
      AND d.classid = 'pg_class'::regclass
) s;
```

Pipe the output back into `psql`. (This is what most Postgres-restore-recovery one-liners do under the hood.)

## When to ignore

Not really applicable. There is no defensible reason to leave a drifted sequence — by definition it's about to cause an outage.

The only legitimate suppression is:

- **You're about to fix it in the next minute** and don't want CI to fail your unrelated PR. Use a config override locally:

  ```toml
  [pgsleuth.checkers.sequence_drift]
  enabled = false   # remove after fixing the sequence
  ```

  Then put it back.

## See also

- [PostgreSQL documentation — Sequence Manipulation Functions](https://www.postgresql.org/docs/current/functions-sequence.html)
- This rule is one of the checks `pg_dump`/`pg_restore` users hit most frequently, and is part of why pgsleuth exists.
