# pgsleuth rules

Each rule has a dedicated page covering what it catches, why it matters, how to fix it, and when it's safe to ignore. The `docs_url` field on every reported `Issue` links here.

| Rule | Severity | Catches |
| --- | --- | --- |
| [`column_value_at_risk`](column_value_at_risk.md) | warning | Sequence-backed columns whose sequence is past 70% of its type's max. |
| [`fk_type_mismatch`](fk_type_mismatch.md) | error | FK column type differs from the referenced column type. |
| [`missing_fk_index`](missing_fk_index.md) | warning | Foreign-key columns not covered by a leading index — slow cascades and joins. |
| [`missing_primary_key`](missing_primary_key.md) | warning | Ordinary tables without a primary key. |
| [`not_valid_constraints`](not_valid_constraints.md) | error | FK and CHECK constraints stuck at `NOT VALID`. |
| [`primary_key_type`](primary_key_type.md) | warning | Primary keys typed as `integer` / `smallint` will eventually overflow. |
| [`redundant_index`](redundant_index.md) | info | An index whose column list is a strict prefix of another on the same table. |
| [`sequence_drift`](sequence_drift.md) | error | Sequence's next value would collide with rows already in the column. |
| [`three_state_boolean`](three_state_boolean.md) | warning | Nullable boolean columns turn `bool` into three-valued logic. |

## Severity philosophy

- **error** — likely to cause an incident. Bugs hiding in the schema, sequence drift, mismatched FK types, half-applied constraints. Treat as a build-blocker.
- **warning** — performance or correctness debt that won't fail today but will later. Fix on the timescale of weeks, not minutes.
- **info** — schema smells worth knowing about but not always worth fixing immediately. Useful as a `--min-severity=warning` skip.

## Suppressing a rule

For a single object, prefer table-level exclusion:

```toml
[pgsleuth]
exclude_tables = ["^my_legacy_table$"]
```

To disable a whole rule:

```toml
[pgsleuth.checkers.redundant_index]
enabled = false
```

To downgrade or upgrade a rule's severity:

```toml
[pgsleuth.checkers.three_state_boolean]
severity = "error"
```
