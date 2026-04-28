"""Thin wrapper over psycopg.connect — keeps connection management in one place."""

from __future__ import annotations

from contextlib import contextmanager
from typing import Iterator

import psycopg

SUPPORTED_VERSION_MIN = 100000  # PG10
SUPPORTED_VERSION_NAMES = "10, 11, 12, 13, 14, 15, 16, 17"


@contextmanager
def connect(dsn: str) -> Iterator[psycopg.Connection]:
    """Open a read-only-friendly connection. Checkers should never write."""
    with psycopg.connect(dsn, autocommit=True) as conn:
        yield conn


def server_version_num(conn: psycopg.Connection) -> int:
    """Return the connected server version as an int (e.g. 150004 for PG 15.4)."""
    with conn.cursor() as cur:
        cur.execute("SELECT current_setting('server_version_num')::int")
        row = cur.fetchone()
    if not row:
        raise RuntimeError("server_version_num returned no row")
    return row[0]


def pg_docs_url(server_version: int, page: str) -> str:
    """Build a versioned PostgreSQL docs URL for the connected server.

    pgsleuth supports PG 10+, where the major version is encoded as
    server_version // 10000 (e.g. 150004 -> 15). Pinning the URL to the
    actual server version means users land on docs that describe the
    behavior they will actually see, not whatever the latest release does.
    """
    major = server_version // 10000
    return f"https://www.postgresql.org/docs/{major}/{page}"


def rule_docs_url(name: str) -> str:
    """Return the URL to the pgsleuth rule documentation for a checker.

    Rule docs live in the project repo under docs/rules/<name>.md and are
    rendered by GitHub. Linking here (instead of straight to the Postgres
    docs) gives every Issue a place to explain *why* the rule exists and
    when it's safe to ignore — Postgres docs themselves are linked from
    inside each rule page as further reading.
    """
    return f"https://github.com/pirr/pgsleuth/blob/main/docs/rules/{name}.md"


@contextmanager
def statement_timeout(conn: psycopg.Connection, timeout_ms: int) -> Iterator[None]:
    """SET statement_timeout for the duration of the block, then RESET.

    Used to bound how long any single checker can spend waiting on Postgres.
    On timeout, the next query the block issues raises
    ``psycopg.errors.QueryCanceled`` — callers are responsible for catching it.

    `RESET` returns the connection to the postgresql.conf default rather than
    any prior session value, which is fine because pgsleuth processes are
    short-lived and don't share connections.
    """
    # SET cannot be parameterized; the value is an int from typed config so
    # there's no injection surface, but we cast defensively.
    with conn.cursor() as cur:
        cur.execute(f"SET statement_timeout = {int(timeout_ms)}")
    try:
        yield
    finally:
        with conn.cursor() as cur:
            cur.execute("RESET statement_timeout")
