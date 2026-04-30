# fk_without_on_delete

> **Severity:** info
> Foreign keys with no explicit `ON DELETE` policy.

## What it catches

Any foreign key whose `ON DELETE` action is `NO ACTION` — both the implicit case (no `ON DELETE` clause at all) and the explicit `ON DELETE NO ACTION`. The catalog can't tell them apart, so the rule treats them the same.

```sql
-- ⚠️  flagged: implicit NO ACTION
ALTER TABLE child
    ADD FOREIGN KEY (parent_id) REFERENCES parent(id);

-- ⚠️  flagged: explicit NO ACTION
ALTER TABLE child
    ADD FOREIGN KEY (parent_id) REFERENCES parent(id) ON DELETE NO ACTION;

-- ok: an intentional choice
ALTER TABLE child
    ADD FOREIGN KEY (parent_id) REFERENCES parent(id) ON DELETE CASCADE;
ALTER TABLE child
    ADD FOREIGN KEY (parent_id) REFERENCES parent(id) ON DELETE RESTRICT;
ALTER TABLE child
    ADD FOREIGN KEY (parent_id) REFERENCES parent(id) ON DELETE SET NULL;
```

## Why it matters

`NO ACTION` is the SQL standard's default — and the one Postgres applies silently if you leave the `ON DELETE` clause off. It is rarely the deliberate choice. The other options force the team to decide what should happen to dependent rows when a parent goes away:

| Action | What it does |
| --- | --- |
| `RESTRICT` | Reject the parent delete immediately. Strict, explicit, easy to reason about. |
| `CASCADE` | Delete the child rows too. Right when the child has no meaning without the parent. |
| `SET NULL` | Null the FK column on the child. Right when the child is independent but related. |
| `SET DEFAULT` | Replace the FK with the column's default. Rare; mostly used with sentinel rows. |
| `NO ACTION` | Reject the parent delete *at the end of the statement*, allowing transient violations inside it. Almost identical to `RESTRICT` in practice — the difference is the deferral window. |

The hidden cost of leaving it as the default isn't usually a correctness bug *today* — it's that the schema doesn't record the team's *intent*. When the question comes up later ("can I delete this parent? what happens to the children?"), the answer is "whatever `NO ACTION` does," which most engineers can't recite from memory. Stating the policy explicitly turns a hidden assumption into a reviewable line of SQL.

There's also a real (if narrow) operational difference between `NO ACTION` and `RESTRICT`:

- `NO ACTION` defers the check to the end of the statement; combined with `INITIALLY DEFERRED`, it can defer to the end of the transaction. Inside that window, the schema temporarily contains orphan rows.
- `RESTRICT` rejects the offending modification *immediately*, before any other rows in the same statement are processed.

Most teams want `RESTRICT`-or-explicit-cascade semantics; `NO ACTION` lands them in deferred-check territory by accident.

## How to fix

Pick the action that matches the relationship and rewrite the constraint. Postgres can't change `ON DELETE` in place — drop and re-add:

```sql
ALTER TABLE child
    DROP CONSTRAINT child_parent_id_fkey,
    ADD  CONSTRAINT child_parent_id_fkey
         FOREIGN KEY (parent_id) REFERENCES parent(id)
         ON DELETE RESTRICT;
```

Both operations happen in a single transaction, so the constraint is never absent from the schema. The new constraint is `VALIDATED` immediately because all existing rows already satisfy it (the previous FK enforced the same referential rule).

For very large child tables, an alternative is to add a *new* `NOT VALID` constraint with the desired `ON DELETE` policy, validate it, then drop the old one:

```sql
ALTER TABLE child
    ADD CONSTRAINT child_parent_id_fkey_new
        FOREIGN KEY (parent_id) REFERENCES parent(id)
        ON DELETE RESTRICT
        NOT VALID;

ALTER TABLE child VALIDATE CONSTRAINT child_parent_id_fkey_new;

ALTER TABLE child DROP CONSTRAINT child_parent_id_fkey;
ALTER TABLE child RENAME CONSTRAINT child_parent_id_fkey_new TO child_parent_id_fkey;
```

This avoids the brief `ACCESS EXCLUSIVE` window of the drop-and-add, at the cost of a longer migration script.

## When to ignore

Suppress the rule project-wide if your team has a deliberate policy of relying on `NO ACTION` (rare but valid — e.g. you depend on deferred-check semantics inside a multi-statement transaction):

```toml
[pgsleuth.checkers.fk_without_on_delete]
enabled = false
```

Or scope it to a specific table where the deferred-check window is intentional:

```toml
[pgsleuth]
exclude_tables = ["^ledger_entries$"]
```

## See also

- [PostgreSQL — `CREATE TABLE`: foreign key actions](https://www.postgresql.org/docs/current/sql-createtable.html#SQL-CREATETABLE-PARMS-REFERENCES)
- [`missing_fk_index`](missing_fk_index.md) — pairs naturally; `ON DELETE CASCADE` without an index on the FK column is the slowest delete in your schema.
