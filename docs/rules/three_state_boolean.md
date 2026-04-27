# three_state_boolean

> **Severity:** warning
> Boolean columns without `NOT NULL` are effectively three-valued.

## What it catches

Any column of type `boolean` (Postgres' `bool`) on an ordinary or partitioned table where `attnotnull = false`.

```sql
CREATE TABLE users (
    id          bigserial PRIMARY KEY,
    is_admin    boolean,                          -- ⚠️  nullable
    is_active   boolean NOT NULL DEFAULT true     -- ✅  fine
);
```

## Why it matters

Boolean is the type that makes the strongest implicit promise: "this is yes or no." A nullable boolean breaks that promise — every value is `true`, `false`, **or** `null`, and almost no application code is written to handle the third case.

Concrete consequences with a 1000-row `users` table where 600 have `is_admin = false`, 50 have `true`, and 350 have `null`:

```sql
SELECT count(*) FROM users WHERE is_admin = false;  -- returns 600  ❌
SELECT count(*) FROM users WHERE NOT is_admin;      -- returns 600  ❌
SELECT count(*) FROM users WHERE is_admin IS DISTINCT FROM true;  -- returns 950 ✅
```

What just happened to the 350 nulls?

1. **`WHERE is_admin = false` doesn't return null rows.** SQL three-valued logic: `null = false` evaluates to `null`, not `true`, so the row is excluded. Your "show all non-admins" query silently drops 35% of the user base.
2. **`WHERE NOT is_admin` has the same problem.** `NOT null` is `null`. Both forms look obviously correct and are obviously wrong.
3. **The fix is verbose.** `WHERE is_admin IS DISTINCT FROM true` or `WHERE COALESCE(is_admin, false) = false` works, but nobody remembers to write it that way the first time. Reviewers don't catch it. ORMs generate the simple form.
4. **Application code branches on `if user.is_admin:` and treats `null` as falsy.** Often correct by accident. Sometimes catastrophically wrong — a null-handling library treats `null` as truthy, the frontend renders `null` differently from `false`, etc.

The fix is almost always to pick a concrete default and add `NOT NULL`. If `null` actually carries meaning ("we don't know yet" vs "no"), the column should be modeled as a small `enum` or three-state state machine, not as a boolean.

## How to fix

Set a default and the constraint:

```sql
ALTER TABLE users
    ALTER COLUMN is_admin SET DEFAULT false,
    ALTER COLUMN is_admin SET NOT NULL;
```

The `SET NOT NULL` step requires a full table scan and an `ACCESS EXCLUSIVE` lock. On large hot tables, do it in three steps to keep locks brief:

```sql
-- 1. Backfill nulls (in batches if the table is large)
UPDATE users SET is_admin = false WHERE is_admin IS NULL;

-- 2. Set the default for new rows
ALTER TABLE users ALTER COLUMN is_admin SET DEFAULT false;

-- 3. Add a NOT VALID check, validate, then promote
ALTER TABLE users ADD CONSTRAINT users_is_admin_not_null
    CHECK (is_admin IS NOT NULL) NOT VALID;
ALTER TABLE users VALIDATE CONSTRAINT users_is_admin_not_null;
ALTER TABLE users ALTER COLUMN is_admin SET NOT NULL;
ALTER TABLE users DROP CONSTRAINT users_is_admin_not_null;
```

(PG 12+ uses the `CHECK ... NOT VALID` step to skip the table-rewrite scan when promoting to `NOT NULL`.)

## When to ignore

A nullable boolean is occasionally the right model:

- **Tri-state opt-in/out flags.** "Has the user opted in to marketing emails? null = haven't asked, true = yes, false = no." This is real, but consider modeling it as an `enum` (`'unknown' | 'opted_in' | 'opted_out'`) — boolean misleads readers.
- **Optional boolean answers in surveys / forms** where "didn't answer" must be distinguished from "answered no."

In those cases, suppress the column-level finding via the table:

```toml
[pgsleuth]
exclude_tables = ["^survey_responses$"]
```

Or per-checker if your codebase uses tri-state booleans deliberately and consistently:

```toml
[pgsleuth.checkers.three_state_boolean]
enabled = false
```

## See also

- [PostgreSQL documentation — Boolean Type](https://www.postgresql.org/docs/current/datatype-boolean.html)
- See also: `WHERE` clauses with `IS [NOT] DISTINCT FROM` — the SQL-correct way to compare possibly-null booleans.
