"""Version detection helper and per-checker `supports()` gate."""

from __future__ import annotations

from typing import ClassVar, Iterable

import psycopg

from pgsleuth.checkers.base import Checker, Issue
from pgsleuth.context import CheckerContext
from pgsleuth.db.connection import SUPPORTED_VERSION_MIN, pg_docs_url, server_version_num


def test_server_version_num_returns_int(conn: psycopg.Connection) -> None:
    version = server_version_num(conn)
    assert isinstance(version, int)
    assert version >= SUPPORTED_VERSION_MIN


class _NoGate(Checker):
    name: ClassVar[str] = "_test_no_gate"
    description: ClassVar[str] = ""

    def run(self, ctx: CheckerContext) -> Iterable[Issue]:
        return ()


class _MinOnly(Checker):
    name: ClassVar[str] = "_test_min_only"
    description: ClassVar[str] = ""
    min_version: ClassVar[int] = 130000

    def run(self, ctx: CheckerContext) -> Iterable[Issue]:
        return ()


class _Range(Checker):
    name: ClassVar[str] = "_test_range"
    description: ClassVar[str] = ""
    min_version: ClassVar[int] = 120000
    max_version: ClassVar[int] = 150000

    def run(self, ctx: CheckerContext) -> Iterable[Issue]:
        return ()


def test_supports_no_gate_accepts_all() -> None:
    assert _NoGate.supports(100000)
    assert _NoGate.supports(170000)


def test_supports_min_inclusive() -> None:
    assert not _MinOnly.supports(120000)
    assert _MinOnly.supports(130000)
    assert _MinOnly.supports(170000)


def test_supports_max_exclusive() -> None:
    assert not _Range.supports(110000)
    assert _Range.supports(120000)
    assert _Range.supports(140000)
    assert not _Range.supports(150000)


def test_pg_docs_url_uses_connected_major() -> None:
    assert pg_docs_url(150004, "ddl-constraints.html") == (
        "https://www.postgresql.org/docs/15/ddl-constraints.html"
    )
    assert pg_docs_url(170000, "indexes-multicolumn.html") == (
        "https://www.postgresql.org/docs/17/indexes-multicolumn.html"
    )
    assert pg_docs_url(100023, "functions-sequence.html") == (
        "https://www.postgresql.org/docs/10/functions-sequence.html"
    )
