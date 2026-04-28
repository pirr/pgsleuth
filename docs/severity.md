# Severity levels

pgsleuth uses three severities. The mapping is intentional — each level corresponds to a real, distinct decision a CI pipeline has to make.

| Level | Meaning | What CI should do |
| --- | --- | --- |
| **`error`** | Schema or data is *currently* broken or incident-imminent. | Fail the build. |
| **`warning`** | Debt that doesn't fail today but will hurt later — performance, future overflow, correctness papered over by app code. | Fail the build at `--min-severity warning` or `info` (the CLI default). Teams can defer with the upcoming baseline mode. |
| **`info`** | Schema smell worth knowing about; cost is small or context-dependent. | Don't fail; surface in periodic audits. |

The pgsleuth team's opinion is encoded as the per-rule defaults below. Every team can override per rule via `pgsleuth.toml` — the defaults are a starting point, not a verdict.

## error — incident-causing or actively wrong

A rule fires at `error` when triggering means **something is broken right now** in your schema or data. Concretely, at least one of:

- Production writes will start failing on the next normal operation.
- The schema makes a claim about the data that the data doesn't satisfy.
- Referential integrity is mechanically broken (joins fall off the index, range overflow looms, FK is enforcing nothing).

Reserve `error` for cases that justify paging someone. Inflation hurts: if every rule is an error, no rule is.

| Rule | Why it's an error |
| --- | --- |
| `sequence_drift` | Next plain `INSERT` raises `unique_violation` and stays broken until the sequence is reset. |
| `fk_type_mismatch` | Joins seq-scan instead of index-scan; eventual range overflow on the narrower side rejects valid writes. |
| `not_valid_constraints` | The schema says an invariant holds (`CHECK (total >= 0)`, FK semantics) but the data already violates it. Every dashboard built on the assumption is wrong. |

## warning — debt that will hurt you, eventually

A rule fires at `warning` when there's no incident today but the cost is real and accumulates. Performance debt, future-overflow debt, correctness bugs that the application is accidentally papering over. Most rules sit here.

By default, `pgsleuth check` runs at `--min-severity info`, which means warnings (and infos) all count toward the exit code. The README's CI snippet uses `--min-severity warning` to skip info-level smells. For brownfield projects either is too aggressive on day one — combine with the upcoming baseline mode (or temporarily run at `--min-severity error`) and tighten over time.

| Rule | Why it's a warning |
| --- | --- |
| `missing_primary_key` | Logical replication breaks, ORM identity becomes ambiguous, common tooling refuses to operate. |
| `primary_key_type` | `integer` PK overflow is months or years away on most tables; the migration to fix it is the painful part. |
| `column_value_at_risk` | Sequence-backed columns past 70% of `max_value` will overflow soon. Reactive companion to `primary_key_type` — fires before the failure but after the runway is short. |
| `missing_fk_index` | Slow cascades and slow joins. Often invisible until a parent-side `DELETE` migration or a 10× traffic spike. |
| `three_state_boolean` | `WHERE col = false` silently excludes nulls. Bug surfaces as "where did half my users go?" under SQL three-valued logic. |

## info — schema smells worth knowing

A rule fires at `info` when the cost is real but small or context-dependent enough that we don't recommend a build-blocker policy out of the box. Run periodically (`--min-severity info`) to see the full list.

| Rule | Why it's info |
| --- | --- |
| `redundant_index` | Write amplification, disk space, plan-flip risk are real, but rarely production-critical. False positives are conceivable (the prefix index occasionally pulls weight via `INCLUDE` columns or planner quirks). |

## Choosing severity for a new rule

When adding a checker, ask in order:

1. **If this rule fires, is the database currently broken or about to be?** → `error`
2. **If a team leaves this unfixed, will they regret it within 6–12 months?** → `warning`
3. **Is this a smell that's worth showing a senior engineer but not blocking deploys?** → `info`

Two failure modes to avoid:

- **Inflation.** If everything is `error`, nothing is. Reserve `error` for things that justify waking someone up at 3 AM.
- **Deflation.** If a real correctness bug like sequence drift is "warning," teams tune it out. Match severity to actual incident impact.

## Overriding severity

The defaults above are pgsleuth's opinion. Your team's policy can override any rule per-project via `pgsleuth.toml`:

```toml
# We ship to a regulated industry; nullable booleans are not acceptable.
[pgsleuth.checkers.three_state_boolean]
severity = "error"

# We have legitimate reasons for some FKs to be unindexed (small read-only
# reference tables); we'll review them by hand when the count grows.
[pgsleuth.checkers.missing_fk_index]
severity = "info"

# We've audited all redundant indexes; from now on, fail the build on new ones.
[pgsleuth.checkers.redundant_index]
severity = "warning"
```

You can also disable a rule entirely:

```toml
[pgsleuth.checkers.redundant_index]
enabled = false
```

## CI thresholds

The `--min-severity` flag controls which findings count toward the exit code:

| Threshold | Use case |
| --- | --- |
| `--min-severity error` | Conservative gate — only block on incidents-in-the-making. Recommended starting point for brownfield projects without baseline mode. |
| `--min-severity warning` | Pragmatic CI gate. Fails on errors and warnings, ignores info smells. Good for greenfield projects and the CI snippet in the README. |
| `--min-severity info` *(CLI default)* | Full report — every finding counts toward the exit code. Useful for periodic schema audits. Often too noisy for every PR; pair with baseline mode. |

For brownfield adoption, the planned `baseline mode` (snapshot all current findings, fail only on *new* ones) is the safe on-ramp at any threshold.
