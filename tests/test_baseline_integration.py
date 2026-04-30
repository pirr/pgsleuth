"""End-to-end integration test for baseline mode against a real Postgres.

The unit and CLI tests cover edge cases with mocks. This file proves the
write → check → prune flow works against a live database across the full
PG 10 / 13 / 15 / 17 matrix from `conftest.py`. One comprehensive
happy-path run is enough; we don't repeat every edge case here.
"""

from __future__ import annotations

import json
from pathlib import Path

import psycopg
from click.testing import CliRunner
from testcontainers.postgres import PostgresContainer

from pgsleuth.cli import main


def test_baseline_write_check_prune_against_real_postgres(
    conn: psycopg.Connection,
    schema: str,
    postgres_container: PostgresContainer,
    tmp_path: Path,
) -> None:
    """Canonical workflow: snapshot, suppress, modify schema, prune, re-check."""
    dsn = postgres_container.get_connection_url().replace("postgresql+psycopg2", "postgresql")
    runner = CliRunner()
    baseline_path = tmp_path / "pgsleuth.baseline.json"

    # Two tables that will both fire missing_primary_key.
    with conn.cursor() as cur:
        cur.execute(f"CREATE TABLE {schema}.events (data text)")
        cur.execute(f"CREATE TABLE {schema}.audit (data text)")

    # Step 1: snapshot. Limit to one checker so test schema is the only
    # source of findings.
    result = runner.invoke(
        main,
        [
            "baseline",
            "write",
            "--dsn",
            dsn,
            "--checkers",
            "missing_primary_key",
            "--output",
            str(baseline_path),
        ],
    )
    assert result.exit_code == 0, result.output

    payload = json.loads(baseline_path.read_text())
    our_entries = sorted(
        e["object"] for e in payload["fingerprints"] if e["object"].startswith(schema)
    )
    assert our_entries == [f"{schema}.audit", f"{schema}.events"]

    # Step 2: check with the baseline applied — both findings suppressed, exit 0.
    result = runner.invoke(
        main,
        [
            "check",
            "--dsn",
            dsn,
            "--checkers",
            "missing_primary_key",
            "--baseline",
            str(baseline_path),
        ],
    )
    assert result.exit_code == 0, result.output
    assert "Suppressed" in result.output
    # Suppressed findings should not appear as current issues in the report.
    after_suppress = result.output.split("Suppressed", 1)[1]
    assert f"{schema}.events" not in after_suppress
    assert f"{schema}.audit" not in after_suppress

    # Step 3: schema change — drop one of the tables.
    with conn.cursor() as cur:
        cur.execute(f"DROP TABLE {schema}.events")

    # Step 4: prune — the entry for the dropped table should disappear.
    result = runner.invoke(
        main,
        [
            "baseline",
            "prune",
            "--dsn",
            dsn,
            "--checkers",
            "missing_primary_key",
            "--baseline",
            str(baseline_path),
            "--ignore-unknown-checkers",
        ],
    )
    assert result.exit_code == 0, result.output

    payload_after = json.loads(baseline_path.read_text())
    our_entries_after = sorted(
        e["object"] for e in payload_after["fingerprints"] if e["object"].startswith(schema)
    )
    assert our_entries_after == [f"{schema}.audit"]

    # Step 5: a new migration introduces a new finding — CI fails on just that one.
    with conn.cursor() as cur:
        cur.execute(f"CREATE TABLE {schema}.audit_log (data text)")

    result = runner.invoke(
        main,
        [
            "check",
            "--dsn",
            dsn,
            "--checkers",
            "missing_primary_key",
            "--baseline",
            str(baseline_path),
        ],
    )
    assert result.exit_code == 1, result.output
    assert f"{schema}.audit_log" in result.output
    # The previously-baselined `audit` table is still suppressed, not surfaced.
    assert f"{schema}.audit" not in result.output.split("audit_log")[0]
