# column_value_at_risk

> **Severity:** warning
> Sequence-backed columns whose sequence is past 70% of its type's max value.

## What it catches

For every column owned by a sequence (created via `SERIAL` / `bigserial` / `GENERATED ... AS IDENTITY` / explicit `OWNED BY`), the checker compares the sequence's `last_value` against `max_value`. If the ratio is past **0.70**, the column is flagged.

```sql
CREATE TABLE orders (id serial PRIMARY KEY, ...);
-- under heavy traffic, the sequence advances:
SELECT last_value FROM orders_id_seq;        -- 1700000000
-- max_value for an integer / serial sequence is 2147483647
-- 1700000000 / 2147483647 ≈ 79.2%   ⚠️  flagged
```

Excluded by design:

- **Cycle sequences** (`CREATE SEQUENCE ... CYCLE`) — they wrap around at max, so "near max" is never a failure.
- **Sequences that have never been called** (`is_called = false`, surfaced as `last_value IS NULL` in `pg_sequences`) — risk is zero.
- **Bigint sequences** in practice — even at 1B inserts, a bigint sequence is at 0.00000001% of its max. The rule fires correctly but you'll never see it on a healthy bigint.

## Why it matters

This is the same failure mode as [`primary_key_type`](primary_key_type.md), but caught **before** the type alone proves the case. Companion piece, not duplicate:

- `primary_key_type` says "this PK is `int4` / `smallint`; you'll regret it eventually." Preventive — fires on every `int4` PK regardless of usage.
- `column_value_at_risk` says "this column is going to overflow soon." Reactive — fires only when actual sequence usage is above threshold.

When this rule fires, you have **runway, not a bug yet**. Time to plan the migration to `bigint` while you still have weeks of headroom.

What overflow looks like in production:

```text
INSERT INTO orders DEFAULT VALUES;
ERROR:  nextval: reached maximum value of sequence "orders_id_seq" (2147483647)
```

Every `INSERT` that omits the column starts failing. The migration to fix it (`ALTER TABLE ... ALTER COLUMN ... TYPE bigint`) rewrites the entire table under an `ACCESS EXCLUSIVE` lock — see `primary_key_type` for what that means at scale. Hours of downtime if you wait until the failure; planned over a quiet weekend if you act on this warning.

## How to fix

The straightforward fix is to widen the column to `bigint`:

```sql
ALTER TABLE orders ALTER COLUMN id TYPE bigint;
```

This rewrites every row in the table and rebuilds every index touching the column. For large tables, do this through a shadow column / dual-write / cut-over pattern instead of one statement — see `primary_key_type` for the long form.

If you skipped `bigserial` originally because "rows will never exceed 2 billion," that assumption is being disproved by this finding. Going `int4 → int4` again (e.g. via `setval` reset) doesn't buy time; the only durable fix is `bigint`.

## When to ignore

A few legitimate cases:

- **Test / staging tables that are recreated frequently.** If the table is dropped weekly, the sequence advance is meaningless. Suppress that table:

  ```toml
  [pgsleuth]
  exclude_tables = ["^staging_events$"]
  ```

- **Sequences you've intentionally bounded with a small `MAXVALUE`** for cycle / partition-key reasons. If the constraint is by design, suppress per-checker:

  ```toml
  [pgsleuth.checkers.column_value_at_risk]
  enabled = false
  ```

- **Reference tables with a hard cap on rows.** If `currencies(id)` is a `serial` and `max(id)` will never plausibly exceed 200, the warning at 80% (= 1717 rows) is noise. Either suppress the table or re-architect — most of the time the "small reference" assumption is the bug.

The rule is **not** a good candidate for blanket disabling. Letting it fire and ignoring it on a per-table basis preserves the protection on the columns that genuinely need it.

## See also

- [`primary_key_type`](primary_key_type.md) — the preventive companion that flags by type alone.
- [`sequence_drift`](sequence_drift.md) — the related-but-different failure where the sequence is *behind* the table; this rule fires when the sequence is *ahead* and approaching its ceiling.
- [PostgreSQL documentation — Sequence Manipulation Functions](https://www.postgresql.org/docs/current/functions-sequence.html)
