# json_over_jsonb

> **Severity:** warning
> Columns typed as `json` (instead of `jsonb`).

## What it catches

Any column whose type is `json`. The `jsonb` type — almost always what you actually want — is **not** flagged.

```sql
CREATE TABLE events (
    id      bigserial PRIMARY KEY,
    payload json,    -- ⚠️  flagged
    meta    jsonb    -- ok
);
```

## Why it matters

`json` and `jsonb` look similar but have very different storage and read characteristics:

| | `json` | `jsonb` |
| --- | --- | --- |
| Storage | text, exactly as written | parsed binary, normalized |
| Read | reparses every time | parses once at write |
| Whitespace, key order, duplicates | preserved | normalized away |
| GIN index | not supported | supported |
| Containment operators (`@>`, `<@`, `?`, `?&`, `?|`) | not supported | supported |
| Speed on read | slower | faster |

The practical impact:

### 1. No indexing

`json` columns can't be indexed in any useful way. Once your table has more than a few thousand rows, every `WHERE payload->>'status' = 'pending'` becomes a sequential scan. `jsonb` lets you build a GIN index that turns the same query into a quick lookup:

```sql
CREATE INDEX events_payload_gin ON events USING gin (payload jsonb_path_ops);
```

### 2. Reparsing on every read

Every time you read a `json` column, Postgres parses the text again from scratch. For a heavily-read table, this is a measurable per-query cost — and it's silent, because the parsing is fast on small payloads but compounds at scale.

### 3. No containment / existence operators

`payload @> '{"status": "pending"}'` or `payload ? 'user_id'` are `jsonb`-only. With `json` you fall back to text comparison or extracting individual fields, which is both slower and more code.

### 4. Duplicate keys silently kept

`{"a": 1, "a": 2}` is preserved as-is in `json`. `jsonb` keeps the last occurrence. Most clients (Python, JS, Go) take the same "last wins" approach when reading, but the database and the client now disagree about what the value is.

The only thing `json` is *better* at is preserving the literal input bytes, including whitespace and key order. Almost no application needs this — and the few that do (some signature-verification flows) usually want the bytes stored as plain `text`, not `json`.

## How to fix

Convert in place:

```sql
ALTER TABLE events
    ALTER COLUMN payload TYPE jsonb
    USING payload::jsonb;
```

The cast is straightforward — Postgres reparses each value into the binary representation. For very large tables, the `ALTER COLUMN ... TYPE` rewrite takes a full ACCESS EXCLUSIVE lock for the duration; copy-table-and-swap is usually safer than an in-place rewrite on a hot table.

## When to ignore

Suppress the rule for a specific column if you genuinely need byte-for-byte preservation (rare):

```toml
[pgsleuth]
exclude_tables = ["^webhook_signatures$"]
```

Or, project-wide:

```toml
[pgsleuth.checkers.json_over_jsonb]
enabled = false
```

## See also

- [PostgreSQL — JSON Types](https://www.postgresql.org/docs/current/datatype-json.html)
- [PostgreSQL — JSON Functions and Operators](https://www.postgresql.org/docs/current/functions-json.html)
