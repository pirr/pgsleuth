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

from pgsleuth.config import Config
from pgsleuth.context import CheckerContext
from pgsleuth.db.connection import server_version_num

PG_VERSIONS = ["10-alpine", "13-alpine", "15-alpine", "17-alpine"]


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
