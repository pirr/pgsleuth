# pgsleuth

Static analysis for PostgreSQL schemas. Finds the schema smells that break replication, slow down queries, or quietly accumulate bad data — missing primary keys, foreign keys without indexes, type mismatches across FKs, redundant indexes, and more.

Works against PostgreSQL 10+. Pure-SQL checks against the system catalogs — no extensions, no superuser, read-only.

## Install

From source:

```bash
git clone https://github.com/pirr/pgsleuth.git
cd pgsleuth
pip install -e '.[dev]'
```

## Usage

```bash
pgsleuth check --dsn postgresql://user:pw@host/db
```

Or via env var:

```bash
export PGSLEUTH_DSN=postgresql://user:pw@host/db
pgsleuth check
```

Filter to specific checkers, change output format, or raise the severity floor:

```bash
pgsleuth check --dsn $DSN --checkers missing_primary_key,missing_fk_index
pgsleuth check --dsn $DSN --format json
pgsleuth check --dsn $DSN --min-severity warning
```

List every available check:

```bash
pgsleuth list-checkers
```

Exit codes: `0` = clean, `1` = issues found, `2` = error.

## Checks

| Name                   | Catches                                                                       |
| ---------------------- | ----------------------------------------------------------------------------- |
| `missing_primary_key`  | Ordinary tables without a PK (breaks logical replication, ambiguous rows).    |
| `primary_key_type`     | Primary keys whose type can run out of values (e.g. `int4` on a hot table).   |
| `missing_fk_index`     | Foreign-key columns without a covering index — slow joins, slow `ON DELETE`. |
| `fk_type_mismatch`     | FK columns whose type differs from the referenced PK/unique column.           |
| `redundant_index`      | Indexes whose column list is a strict prefix of another index on the table.   |
| `not_valid_constraints`| FK and CHECK constraints stuck at `NOT VALID` (`convalidated = false`).       |
| `sequence_drift`       | Sequences whose `nextval` would collide with rows already in the table.       |
| `three_state_boolean`  | Boolean columns without `NOT NULL` (true / false / null is rarely intended).  |

Each issue carries a severity (`info` / `warning` / `error`), a fully-qualified object name, a human-readable message, and — where applicable — a suggested fix and a link to the relevant Postgres docs.

## Configuration

Pass a TOML config file with `--config`:

```toml
[pgsleuth]
exclude_schemas = ["pg_catalog", "information_schema", "pg_toast", "audit"]
exclude_tables  = ["^tmp_", "_archive$"]   # regex, matched against table name

[pgsleuth.checkers.three_state_boolean]
severity = "error"

[pgsleuth.checkers.redundant_index]
enabled = false
```

The same filters can be passed on the command line:

```bash
pgsleuth check --dsn $DSN \
  --exclude-schema audit \
  --exclude-table '^tmp_'
```

## CI usage

`pgsleuth` is non-zero on findings, so it drops into a CI step:

```yaml
- run: pgsleuth check --dsn $DATABASE_URL --min-severity warning
```

Use `--format json` if you want to pipe results into a reporter.

## Development

```bash
pip install -e '.[dev]'
pytest -v
```

The test suite spins up real PostgreSQL containers (10, 13, 15, 17) via `testcontainers` and runs every check against each — Docker required.

## License

MIT.
