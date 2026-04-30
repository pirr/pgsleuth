# varchar_length

> **Severity:** info
> `varchar(N)` columns where `text` would be equivalent.

## What it catches

Any column declared as `varchar(N)` — i.e. `character varying` with a length cap. Bare `varchar` (no length) and `text` are **not** flagged.

```sql
CREATE TABLE users (
    id    bigserial PRIMARY KEY,
    email varchar(255),   -- ⚠️  flagged
    name  varchar,        -- ok (no cap)
    bio   text            -- ok
);
```

## Why it matters

This is the highest-friction myth that follows people moving from other databases to Postgres:

- In **MySQL**, `VARCHAR(N)` and `TEXT` differ in storage and indexing.
- In **SQL Server**, `varchar(N)` and `varchar(MAX)` differ.
- In **Postgres**, `varchar(N)`, `varchar`, and `text` all use **the same underlying storage** — there is no performance benefit to picking one over another. The only effect of `(N)` is a constraint that rejects writes longer than `N`.

If you actually want to constrain the length, that's a legitimate use of `varchar(N)`. But most cases pgsleuth flags are accidental:

### 1. The cap doesn't reflect a real constraint

`varchar(255)` shows up in countless schemas because that was the largest "free" varchar in old MySQL (under 768 bytes for utf8mb3, fit in an indexed key). In Postgres it's a number with no meaning — and the day a real address, email, or user input exceeds 255 bytes, you discover the cap by way of a write rejected in production.

### 2. The cap is in the wrong layer

If you want "emails are at most 320 characters per RFC 5321," the right place is a `CHECK (length(email) <= 320)` constraint *or* application-level validation, not `varchar(320)`. The `varchar(N)` cap can't be relaxed without an `ALTER COLUMN ... TYPE` migration that takes a full table rewrite — a `CHECK` constraint can be relaxed with `ALTER TABLE ... DROP CONSTRAINT`.

### 3. Length-change migrations are surprisingly painful

Bumping `varchar(255)` to `varchar(500)` in PG ≥ 9.2 doesn't rewrite the table (it's metadata-only when the cap goes up), but **shrinking** does — and many teams discover this only the day they need to. Using `text` from the start removes the question.

The reason this is `info` rather than `warning`: `varchar(N)` isn't *broken*. Plenty of teams use it deliberately. The rule exists to catch the unintentional use, where the length cap is a fossil rather than a constraint. Run periodically (`--min-severity info`) rather than blocking PRs on it.

## How to fix

If the cap is unintentional, drop it:

```sql
ALTER TABLE users ALTER COLUMN email TYPE text;
```

If the cap is intentional, keep it as a `CHECK` constraint instead — easier to relax later:

```sql
ALTER TABLE users
    ALTER COLUMN email TYPE text,
    ADD CONSTRAINT email_length CHECK (length(email) <= 320);
```

For very large tables, the type rewrite is locked. Plan accordingly.

## When to ignore

If your team treats `varchar(N)` as a deliberate, application-meaningful constraint and prefers it over `CHECK`, disable the rule project-wide:

```toml
[pgsleuth.checkers.varchar_length]
enabled = false
```

Or skip individual columns by table:

```toml
[pgsleuth]
exclude_tables = ["^audit_"]
```

## See also

- [PostgreSQL — Character Types](https://www.postgresql.org/docs/current/datatype-character.html)
- [Don't Do This — `varchar(n)` by default](https://wiki.postgresql.org/wiki/Don't_Do_This#Don.27t_use_varchar.28n.29_by_default)
