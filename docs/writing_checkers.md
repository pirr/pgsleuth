# Writing a new checker

A checker is a class that runs a SQL query against `pg_catalog`, builds an `Issue` per problematic row, and yields the result. The framework handles registration, version gating, statement timeouts, schema/table exclusion, severity overrides, and reporter dispatch — your job is the SQL and the per-row formatting.

## Pick a base class

| Pattern | Base class | When |
| --- | --- | --- |
| One SQL query, each row maps to 0 or 1 Issue | `RowChecker` | almost always |
| Multiple queries, N+1, no SQL at all | `Checker` (escape hatch) | see `sequence_drift.py` |

Default to `RowChecker`. Drop down to `Checker` only when you can already see the row-mapped shape won't fit.

## Write the SQL

Include the literal placeholder `{schema_filter}` somewhere in your `WHERE` clause. The framework expands it to `AND <alias>.nspname NOT IN ('pg_catalog', ...)` based on the user's `exclude_schemas` config:

```sql
WHERE c.relkind = 'r'
  {schema_filter}     -- becomes: AND n.nspname NOT IN ('pg_catalog', ...)
```

Defaults expect the SQL to:
- alias `pg_namespace` as `n`
- expose two columns named `schema` and `table` in the result set

If your SQL aliases differ — common when a rule joins two namespaces, like `sequence_drift` — override `schema_alias`, `schema_key`, `table_key` on the class.

## Define the class

Create a new module under `src/pgsleuth/checkers/`:

```python
"""Tables without a `created_at` column.

Audit trails get harder when half the tables don't record creation time.
This is opinionated; teams without that convention should disable the rule.
"""

from __future__ import annotations

from typing import ClassVar

from pgsleuth.checkers.base import Issue, RowChecker, Severity, register
from pgsleuth.context import CheckerContext

_SQL = """
SELECT n.nspname AS schema, c.relname AS table
FROM pg_class      c
JOIN pg_namespace  n ON n.oid = c.relnamespace
WHERE c.relkind = 'r'
  AND NOT EXISTS (
        SELECT 1 FROM pg_attribute a
        WHERE a.attrelid = c.oid
          AND a.attname = 'created_at'
          AND NOT a.attisdropped
      )
  {schema_filter}
ORDER BY n.nspname, c.relname;
"""


class MissingCreatedAt(RowChecker):
    name: ClassVar[str] = "missing_created_at"
    description: ClassVar[str] = "Tables without a `created_at` timestamp column."
    default_severity: ClassVar[Severity] = Severity.INFO
    sql: ClassVar[str] = _SQL

    def check_row(self, ctx: CheckerContext, row: dict) -> Issue | None:
        obj = f"{row['schema']}.{row['table']}"
        return self.issue(
            ctx,
            object_type="table",
            object_name=obj,
            message=f"Table {obj} has no `created_at` column.",
            suggestion=(
                f"ALTER TABLE {obj} "
                f"ADD COLUMN created_at timestamptz NOT NULL DEFAULT now();"
            ),
        )


register(MissingCreatedAt)
```

Return `None` from `check_row` to skip a row that the SQL pulled in but doesn't actually warrant a finding (see `column_value_at_risk.py` for an example — it filters out sequences below the threshold).

## Wire it up

Three things, none of them automatic:

1. **Add the import** to `src/pgsleuth/checkers/__init__.py`. **The CLI imports that barrel exactly once at startup; checkers not imported there are silently invisible.** This is the single most common new-checker mistake.

2. **Add a docs page** at `docs/rules/<name>.md` covering rationale, an example of the smell, the fix SQL, and "when to ignore." Each `Issue` carries a `docs_url` that points here automatically — no need to wire it.

3. **Add a row** to the `## Checks` table in the README, linking to the docs page.

## Pick the severity

The `default_severity` you set is the framework's *default*; users can override per-rule via `pgsleuth.toml`. Read `docs/severity.md` for the full philosophy. The short version:

| Level | When to pick it |
| --- | --- |
| `error` | Schema is *currently* broken or about to fail on the next normal write. |
| `warning` | Debt that doesn't fail today but will hurt later (perf, future overflow, correctness papered over by app code). |
| `info` | Real cost, but small or context-dependent — don't recommend a build-blocker policy out of the box. |

Inflation hurts: if every rule is `error`, no rule is.

## Version gating

Set `min_version` / `max_version` (PG-encoded ints — `100000` is PG10, `150000` is PG15, etc.) when the SQL relies on a catalog or column that doesn't exist in older releases. The framework prints `[skipped] <name>` to stderr for checkers that don't support the connected server.

```python
class MyCheck(RowChecker):
    min_version: ClassVar[int] = 100000  # pg_class.relispartition lands in PG10
```

Gates are inclusive on `min`, exclusive on `max`. If you need *different SQL* per version (instead of skipping below some version), leave the gates as `None` and branch on `ctx.server_version` inside `check_row` or `run`.

## Tests

Each test runs against real Postgres 10 / 13 / 15 / 17 via `testcontainers` (Docker required). The `ctx`, `conn`, `schema` fixtures from `tests/conftest.py` give every test a fresh schema. Because the checker scans the whole DB and tests can run in parallel, filter results to the test's own schema:

```python
from pgsleuth.checkers.missing_created_at import MissingCreatedAt


def test_clean_when_column_present(ctx, conn, schema):
    with conn.cursor() as cur:
        cur.execute("CREATE TABLE t (id bigserial PRIMARY KEY, created_at timestamptz)")

    issues = [i for i in MissingCreatedAt().run(ctx) if i.object_name.startswith(schema)]
    assert issues == []


def test_flags_table_without_column(ctx, conn, schema):
    with conn.cursor() as cur:
        cur.execute("CREATE TABLE t (id bigserial PRIMARY KEY)")

    issues = [i for i in MissingCreatedAt().run(ctx) if i.object_name.startswith(schema)]
    assert len(issues) == 1
    assert issues[0].object_name == f"{schema}.t"
```

Aim for at least one *clean* test (no finding) and one *positive* test (finding present). Add edge-case tests for any predicate the SQL deliberately excludes (partitioned tables, dropped columns, etc.).

## Escape hatch — subclass `Checker` directly

If your rule needs follow-up queries *per row* (read `sequence_drift.py`: it scans for owned sequences, then does `SELECT last_value...` and `SELECT MAX(...)` per result), or doesn't fit the row-mapped shape at all, subclass `Checker` and write `run(self, ctx) -> Iterable[Issue]` yourself:

```python
from pgsleuth.checkers.base import Checker, Issue, Severity, register
from pgsleuth.db.catalog import iter_objects


class MyCustom(Checker):
    name: ClassVar[str] = "my_custom"
    ...

    def run(self, ctx: CheckerContext) -> Iterable[Issue]:
        for row in iter_objects(ctx, _MAIN_SQL):
            extra = self._fetch_extra(ctx.conn, row)
            if not _qualifies(row, extra):
                continue
            yield self.issue(ctx, object_type=..., object_name=..., message=..., ...)
```

You still get `self.issue(...)` for boilerplate-free Issue construction. Always walk the main query through `iter_objects(ctx, sql, ...)` rather than calling `fetch_all` directly — that's what applies the `--exclude-schema` and `--exclude-table` filters. Bypassing it silently breaks those flags for your rule.
