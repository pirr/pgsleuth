# timestamp_without_tz

> **Severity:** warning
> Columns typed as `timestamp` (without time zone) drop timezone information.

## What it catches

Any column whose type is `timestamp without time zone` (the SQL standard's `timestamp`, and Postgres' default when you write just `timestamp`). The other timestamp type — `timestamp with time zone` (`timestamptz`) — is **not** flagged.

```sql
CREATE TABLE events (
    id          bigserial PRIMARY KEY,
    occurred_at timestamp,    -- ⚠️  flagged
    created_at  timestamptz   -- ok
);
```

## Why it matters

The two types sound similar but represent different things:

- **`timestamptz`** stores the moment as UTC internally; on read it converts to the session's timezone. The value `2026-01-15 10:00:00 UTC` and `2026-01-15 11:00:00 Europe/Berlin` are the same instant — the database knows.
- **`timestamp` (without time zone)** stores wall-clock digits with **no timezone awareness**. The value `2026-01-15 10:00:00` means whatever the writer thought it meant.

The bugs this introduces are silent and often only surface when something changes:

### 1. Values written from different timezones merge incorrectly

Two services or two operators writing to the same column from different timezones produce values that *look* comparable but aren't. Sorting them, comparing them, or computing intervals across them gives wrong answers — and the database happily reports the wrong answer with no error.

### 2. Daylight-saving transitions corrupt local times

`'2025-03-30 02:30:00'` written into a `timestamp` column on the day Europe springs forward is a moment that **never existed**. `timestamptz` rejects or normalizes it; `timestamp` stores it as-is and you reread garbage on autumn comparisons.

### 3. Reads are session-timezone-dependent (for `timestamptz`) or not (for `timestamp`)

Most frameworks and ORMs assume `timestamptz` semantics. Mixing the two types in one schema means some columns convert on read and some don't, and developers learn the difference by debugging production at 3 AM.

### 4. Timezone changes (`SET TIMEZONE`, server move, container redeploy) don't migrate the data

Move your application from `UTC` to `Europe/Berlin` for any reason and every existing `timestamp` value now means something different than what was written. `timestamptz` is unaffected.

The rule of thumb: **if the value represents a real moment in time** — created_at, updated_at, occurred_at, expires_at, scheduled_for — use `timestamptz`. The very narrow case for `timestamp without time zone` is "wall clock" data: a 09:00 alarm that should fire at 09:00 local time *wherever the user is right now*, even if they cross timezones. That's rare.

## How to fix

Convert in place. The `USING` clause is required; pick the timezone that the *existing values* were written in:

```sql
ALTER TABLE events
    ALTER COLUMN occurred_at TYPE timestamptz
    USING occurred_at AT TIME ZONE 'UTC';
```

If you're not sure what timezone existing values represent, **stop and find out** before migrating. `AT TIME ZONE` reinterprets them — the wrong source timezone shifts every value silently.

For very large tables, the `ALTER COLUMN ... TYPE` rewrite takes a full ACCESS EXCLUSIVE lock for the duration. Plan accordingly: copy-table-and-swap is usually safer than an in-place rewrite on a hot table.

## When to ignore

Suppress the rule for a specific column if it really is wall-clock data with no timezone semantics:

```toml
[pgsleuth]
exclude_tables = ["^calendar_events$"]
```

Or, if your team has a project-wide reason to use `timestamp without time zone` (rare), disable it:

```toml
[pgsleuth.checkers.timestamp_without_tz]
enabled = false
```

## See also

- [PostgreSQL — Date/Time Types](https://www.postgresql.org/docs/current/datatype-datetime.html)
- [Don't Do This — `timestamp (without time zone)`](https://wiki.postgresql.org/wiki/Don't_Do_This#Don.27t_use_timestamp_.28without_time_zone.29)
