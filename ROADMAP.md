# Roadmap

What's planned, in rough order. The "why" matters more than the "when" — anything below can be picked up by anyone, in any order, as long as the rationale still holds.

## Near-term — quality and adoption

### Inline suppression via table comments
Allow per-object suppression without an external config file:

```sql
COMMENT ON TABLE legacy_orders IS 'pgsleuth: ignore=missing_fk_index reason="read-only ledger"';
```

The reason field is required so suppressions don't rot. *Why:* TOML-only suppression forces config sprawl and divorces "why we ignored this" from the schema. Comments live with the object they describe and survive `pg_dump` / `pg_restore`.

### SARIF reporter
`--format sarif` outputs SARIF 2.1.0 JSON — a vendor-neutral OASIS standard supported by GitHub code scanning, GitLab security dashboards, Azure DevOps, Bitbucket, the official VS Code SARIF Viewer, JetBrains plugins, SonarQube imports, and Microsoft's `sarif-multitool`. *Why:* one output format for every consumer worth integrating with. The reporter itself is roughly 80 lines; downstream tools (the GitHub Action wrapper, plus whatever pipelines teams build) consume it without further work from us.

### HTML reporter (coverage.py-style)
`--format-html <directory>` generates a coverage.py-styled report — `index.html` with a summary band (counts by severity, by checker, run timestamp, server version), a sortable + filterable findings table, and per-checker drill-down pages. Self-contained: inline CSS, vanilla JS, no external CDN dependencies. Includes a print stylesheet so `File → Print → Save as PDF` produces a clean archive artifact.

```
pgsleuth check --dsn $DSN --format-html ./pgsleuth-report/
open pgsleuth-report/index.html
```

*Why:* fills the "share this with someone who doesn't live in the terminal" gap. DBAs, SREs, engineering managers, security reviewers, auditors — none of them will pip-install pgsleuth and pipe through `jq`. The text reporter is for the developer running pgsleuth; SARIF is for the dashboard pgsleuth feeds; HTML is for the human reading the result. Pairs naturally with the *out-of-CI execution guide* — cron the report into S3 weekly, link from the team wiki. ~250 LOC including the templates.

### `statement_timeout` guardrails
Each checker runs with a configurable `statement_timeout` (default ~5s) so a single slow check can't hang CI on a 50TB database. Configurable via `pgsleuth.toml` per checker, with a project-wide default. *Why:* without this, `redundant_index` and `not_valid_constraints` are O(catalog size) and can easily exceed CI step timeouts on real production schemas.

### GitHub Action wrapper (for ephemeral-DB CI)
Composite `action.yml` at the repo root so users get pgsleuth into CI with five lines of YAML:

```yaml
- uses: pirr/pgsleuth@v1
  with:
    dsn: postgresql://postgres:x@localhost   # ephemeral CI Postgres
    min-severity: warning
```

Internally: install pgsleuth from the matching release tag, run `--format sarif`, upload via `github/codeql-action/upload-sarif`.

*Why:* lowest-friction adoption path for the use case CI is actually good at — applying migrations to an ephemeral Postgres in the job and linting the resulting schema. *Not* for connecting GitHub-hosted runners to staging or production databases — that path has all the usual problems (network whitelisting, credentials with prod blast radius, exposure to transitively-trusted Actions). For private DBs, see *Out-of-CI execution guide* below.

### Out-of-CI execution guide
A short doc explaining how to run pgsleuth against a private database (staging, prod) from somewhere with legitimate network access — Kubernetes CronJob, dedicated bastion, scheduled task on the DB platform — and ship the SARIF / JSON / HTML output to wherever the team triages it (S3, Slack, an issue tracker, a metrics pipeline). Same pattern PgHero, pganalyze, and pgwatch use.

```cron
# nightly machine-readable snapshot
0 2 * * * pgsleuth check --dsn $PROD_DSN --format sarif > /audit/pgsleuth-$(date +\%F).sarif
# weekly human-readable report linked from the team wiki
0 3 * * 1 pgsleuth check --dsn $PROD_DSN --format-html /var/www/pgsleuth-report/
```

*Why:* the "lint a live DB on a schedule" use case is just as common as "lint the migration in CI," but the two need different deployment shapes. The current docs nudge readers toward the CI shape; this fills the gap so teams running pgsleuth against private DBs don't accidentally end up whitelisting GitHub's runner ranges.

## Medium-term — more rules

Get from 8 to ~30 rules so the tool is taken seriously next to schemacrawler. High-leverage adds, in rough order:

- **`unused_index`** — `pg_stat_user_indexes.idx_scan = 0` over a configurable window. Pairs naturally with `redundant_index`.
- **`varchar_length`** — `varchar(N)` where `text` would be equivalent (Postgres has no perf benefit to a length cap).
- **`timestamp_without_tz`** — `timestamp` columns that should almost certainly be `timestamptz`.
- **`json_over_jsonb`** — `json` columns that should be `jsonb`.
- **`missing_replica_identity`** — tables with no `REPLICA IDENTITY` (extends `missing_primary_key` to cover tables with explicit replica identity but no PK).
- **`fk_without_on_delete`** — foreign keys without an explicit `ON DELETE` policy (forcing the team to think about cascade vs restrict vs set null).
- **`partition_without_pk`** — partitioned tables where the partition key isn't part of the PK.
- **`default_now_text`** — `default 'now()'` (string literal!) instead of `default now()`.

## Longer-term — research

### Migration-aware mode
Pre-merge linting: given a migration file (or a set of pending migrations), apply them to a sandbox via `testcontainers` and report the *new* findings the migration would introduce. Combines the static-text approach of `squawk` with pgsleuth's live-DB analysis.

```bash
pgsleuth check-migration migrations/0042_add_audit.sql --against $PROD_DSN
# expected output: "this migration would introduce 1 new finding"
```

*Why:* would let teams catch schema regressions before merge, not after deploy. Category-defining if it works.

### Per-rule docs site
Move `docs/rules/*.md` from GitHub-rendered Markdown to a real docs site (mkdocs/material or Astro Starlight). Cleaner URLs, search, version-pinned snapshots per release. *Why:* every linter that reached "trusted" status (ruff, eslint, sqlfluff) has one. Not urgent — the GitHub-rendered version works for v1.

### Pre-built deprecation infrastructure
`Checker` base class gains `deprecated_in: ClassVar[str | None]` and `removed_in: ClassVar[str | None]` so we can announce upcoming rule removals before they happen. Captured as a follow-up from the baseline-mode design.

## How to contribute

Pick anything from "Near-term" or "Medium-term — more rules." Open an issue to claim it (so two people don't pick the same one), then send a PR. The new-rule shape is intentionally simple: one file in `src/pgsleuth/checkers/`, one rule doc in `docs/rules/`, one test in `tests/checkers/`, severity chosen per [`docs/severity.md`](docs/severity.md).
