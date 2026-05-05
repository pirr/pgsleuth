"""Postgres test containers shared across the suite.

Each test gets a fresh schema (`test_<id>`) on the same database to keep
container startup amortized. The fixture sets `search_path` so checkers see
only the test's objects unless they explicitly scan all schemas.

The `postgres_container` fixture is parametrized over multiple major versions
so every test runs against each supported PG release.
"""

from __future__ import annotations

import uuid
from collections.abc import Iterator

import psycopg
import pytest
from testcontainers.postgres import PostgresContainer

from pgsleuth import baseline as baseline_module
from pgsleuth.checkers.base import Issue
from pgsleuth.config import Config
from pgsleuth.context import CheckerContext
from pgsleuth.db.connection import server_version_num
from pgsleuth.engine import RunResult

PG_VERSIONS = ["10-alpine", "13-alpine", "15-alpine", "17-alpine"]


def make_run_result(
    issues: list[Issue],
    *,
    baseline: baseline_module.Baseline | None = None,
    ran: frozenset[str] | None = None,
) -> RunResult:
    """Build a RunResult that mirrors what `engine.run` would produce.

    `ran` defaults to the set of checkers that produced an issue. Pass it
    explicitly when a test needs to distinguish "checker ran with no findings"
    (in `ran`, makes its baseline entries eligible for stale-warning) from
    "checker didn't run" (not in `ran`, baseline entries treated as unknown).
    """
    if ran is None:
        ran = frozenset(i.checker for i in issues)
    if baseline is None:
        return RunResult(issues=list(issues), skipped=(), ran=ran)
    filtered = baseline_module.filter_issues(issues, baseline)
    stale = tuple(
        e for e in baseline_module.stale_entries(baseline, filtered.matched_fps) if e.checker in ran
    )
    unknown = tuple(baseline_module.unknown_checker_entries(baseline, ran))
    return RunResult(
        issues=filtered.kept,
        skipped=(),
        ran=ran,
        suppressed_count=filtered.suppressed_count,
        matched_baseline_fps=filtered.matched_fps,
        stale_baseline_entries=stale,
        unknown_baseline_entries=unknown,
    )


def fake_engine_run(issues=(), ran: frozenset[str] | None = None):
    """side_effect for `patch("pgsleuth.engine.run", ...)`.

    Replaces the old pattern of patching `cli._run_all` with a flat issue list.
    The returned function honors the engine's `(ctx, *, threshold, baseline)`
    signature and runs baseline filtering through `make_run_result`.
    """

    def _side_effect(ctx, *, threshold, baseline=None):
        return make_run_result(list(issues), baseline=baseline, ran=ran)

    return _side_effect


@pytest.fixture(scope="session", params=PG_VERSIONS, ids=lambda p: f"pg{p.split('-')[0]}")
def postgres_container(request: pytest.FixtureRequest) -> Iterator[PostgresContainer]:
    with PostgresContainer(f"postgres:{request.param}") as pg:
        yield pg


@pytest.fixture()
def conn(postgres_container: PostgresContainer) -> Iterator[psycopg.Connection]:
    dsn = postgres_container.get_connection_url().replace("postgresql+psycopg2", "postgresql")
    with psycopg.connect(dsn, autocommit=True) as connection:
        yield connection


@pytest.fixture()
def schema(conn: psycopg.Connection) -> Iterator[str]:
    name = f"test_{uuid.uuid4().hex[:8]}"
    with conn.cursor() as cur:
        cur.execute(f"CREATE SCHEMA {name}")
        cur.execute(f"SET search_path TO {name}")
    try:
        yield name
    finally:
        with conn.cursor() as cur:
            cur.execute(f"DROP SCHEMA {name} CASCADE")


@pytest.fixture()
def ctx(conn: psycopg.Connection, schema: str) -> CheckerContext:
    # Default config keeps Postgres' own catalogs out, but does NOT exclude
    # the per-test schema, so checks see the fixture objects.
    return CheckerContext(
        conn=conn,
        config=Config(),
        server_version=server_version_num(conn),
    )
