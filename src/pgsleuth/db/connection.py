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
