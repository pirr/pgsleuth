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

Or with Docker (no Python on the host required):

```bash
docker build -t pgsleuth .
docker run --rm pgsleuth check --dsn postgresql://user:pw@host/db
```

The image is built on `python:3.13-alpine` and runs as a non-root user.

**Passing a config file:** files on your host aren't visible inside the container — mount the config as a volume and point `--config` at the in-container path:

```bash
docker run --rm \
  -v "$PWD/pgsleuth.toml:/pgsleuth.toml:ro" \
  pgsleuth check \
  --dsn postgresql://user:pw@host/db \
  --config /pgsleuth.toml
```

The same pattern works for `--baseline` or any other path-typed flag.

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

| Name | Catches |
| --- | --- |
| [`missing_primary_key`](docs/rules/missing_primary_key.md) | Ordinary tables without a PK (breaks logical replication, ambiguous rows). |
| [`primary_key_type`](docs/rules/primary_key_type.md) | Primary keys whose type can run out of values (e.g. `int4` on a hot table). |
| [`column_value_at_risk`](docs/rules/column_value_at_risk.md) | Sequence-backed columns whose sequence is past 70% of its type's max value. |
| [`missing_fk_index`](docs/rules/missing_fk_index.md) | Foreign-key columns without a covering index — slow joins, slow `ON DELETE`. |
| [`fk_type_mismatch`](docs/rules/fk_type_mismatch.md) | FK columns whose type differs from the referenced PK/unique column. |
| [`redundant_index`](docs/rules/redundant_index.md) | Indexes whose column list is a strict prefix of another index on the table. |
| [`not_valid_constraints`](docs/rules/not_valid_constraints.md) | FK and CHECK constraints stuck at `NOT VALID` (`convalidated = false`). |
| [`sequence_drift`](docs/rules/sequence_drift.md) | Sequences whose `nextval` would collide with rows already in the table. |
| [`three_state_boolean`](docs/rules/three_state_boolean.md) | Boolean columns without `NOT NULL` (true / false / null is rarely intended). |

Every rule has a dedicated page covering rationale, examples, fix SQL, and "when to ignore." Each reported `Issue` carries a `docs_url` pointing at the rule's page, plus a severity (`info` / `warning` / `error`), a fully-qualified object name, a human-readable message, and a suggested fix.

Severity is intentional, not arbitrary — see [`docs/severity.md`](docs/severity.md) for the full philosophy, the per-rule rationale, and `--min-severity` guidance for CI.

## Baseline mode

For brownfield databases with hundreds of pre-existing findings, baseline mode lets you adopt pgsleuth in CI **without fixing all of them first**. Snapshot the current findings to a JSON file, commit it; from then on CI fails only on **new** findings.

```bash
# Day 1 — one-time setup
pgsleuth baseline write --dsn $DSN
git add pgsleuth.baseline.json
git commit -m "baseline: snapshot of accepted schema findings"

# Every CI run (auto-discovers ./pgsleuth.baseline.json)
pgsleuth check --dsn $DSN
# pgsleuth: using pgsleuth.baseline.json (auto-discovered; pass --no-baseline to skip)
# Suppressed 116 findings via baseline.
# No issues found.
# exit 0
```

When a migration introduces a new finding, CI fails on **just that one finding**:

```text
[WARNING]  public.audit_log(user_id)
  Foreign key 'audit_log_user_id_fkey' has no covering index.
  suggestion: CREATE INDEX ON public.audit_log (user_id);

Suppressed 116 findings via baseline.
Summary: 1 warning
exit 1
```

The author either fixes it (preferred) or runs `pgsleuth baseline write` again to accept it — visible in the PR diff for review.

### Other baseline commands

```bash
pgsleuth baseline show              # human-readable view of the file (no DB)
pgsleuth baseline prune --dsn $DSN  # remove entries that no longer reproduce
pgsleuth baseline prune --dry-run --dsn $DSN   # preview what prune would remove
```

Run `prune` periodically (every few months, or before reviewing the baseline). Without it, fixed-but-still-baselined entries silently mask regressions.

### Flags on the `check` command

| Flag | Effect |
| --- | --- |
| (none) | Auto-discovers `./pgsleuth.baseline.json` if present. Prints a one-line stderr notice when it does. |
| `--baseline PATH` | Use the baseline at `PATH` (overrides auto-discovery). |
| `--no-baseline` | Disable auto-discovery for this run; report every finding. Useful for periodic full audits. |

### How "same finding" is recognized

Each finding gets a stable fingerprint of `(checker, object_name)` — `sha256("pgsleuth/baseline/v1\0checker\0object_name")`. The fingerprint **excludes the human-readable message**, so rephrasing the message in a future pgsleuth release doesn't break committed baselines. The file is sorted deterministically by `(checker, object)` for clean diffs in code review.

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
  # If pgsleuth.baseline.json is in the repo root, it's auto-discovered
  # and CI fails only on new findings beyond it. See "Baseline mode" above.
```

Use `--format json` if you want to pipe results into a reporter.

## Development

```bash
pip install -e '.[dev]'
pytest -v
```

The test suite spins up real PostgreSQL containers (10, 13, 15, 17) via `testcontainers` and runs every check against each — Docker required.

See [`ROADMAP.md`](ROADMAP.md) for what's coming and how to pick up an item.

## License

MIT.
