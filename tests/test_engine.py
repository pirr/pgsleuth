"""Unit tests for the engine module — exercising what was previously only
reachable through the Click runner.

Most tests don't touch a real DB: synthetic checkers yield prebaked Issues,
and `Config(statement_timeout_ms=None)` skips the `SET statement_timeout`
side trip. The timeout-skip test needs a real `conn` to trigger
`psycopg.errors.QueryCanceled`.
"""

from __future__ import annotations

from contextlib import contextmanager
from typing import ClassVar, Iterable
from unittest.mock import patch

import psycopg
import pytest

from pgsleuth import baseline as baseline_module
from pgsleuth import engine
from pgsleuth.baseline import fingerprint_for
from pgsleuth.checkers.base import Checker, Issue, Severity, _Registry
from pgsleuth.config import Config
from pgsleuth.context import CheckerContext


def _issue(checker: str, obj: str, severity: Severity = Severity.WARNING) -> Issue:
    return Issue(
        checker=checker,
        severity=severity,
        object_type="table",
        object_name=obj,
        message=f"finding on {obj}",
    )


def _ctx(
    *, conn=None, config: Config | None = None, server_version: int = 150004
) -> CheckerContext:
    """Build a CheckerContext for tests that don't need a real DB connection.

    The default Config has `statement_timeout_ms=None` so the engine's
    `statement_timeout(ctx.conn, ...)` branch is bypassed and `conn` can be a
    placeholder.
    """
    return CheckerContext(
        conn=conn,
        config=config or Config(statement_timeout_ms=None),
        server_version=server_version,
    )


# ---------- Fixed-output checkers (no DB required) ----------


def _make_checker(
    name_: str,
    issues: list[Issue],
    *,
    severity: Severity = Severity.WARNING,
    min_version: int | None = None,
    max_version: int | None = None,
) -> type[Checker]:
    class _Synthetic(Checker):
        name: ClassVar[str] = name_
        description: ClassVar[str] = "test-only"
        default_severity: ClassVar[Severity] = severity

        def run(self, ctx: CheckerContext) -> Iterable[Issue]:
            yield from issues

    _Synthetic.min_version = min_version
    _Synthetic.max_version = max_version
    return _Synthetic


@pytest.fixture()
def isolated_registry(monkeypatch: pytest.MonkeyPatch) -> _Registry:
    """Replace `pgsleuth.engine.registry` with a fresh, empty registry."""
    reg = _Registry()
    monkeypatch.setattr("pgsleuth.engine.registry", reg)
    return reg


# ---------- threshold filtering ----------


def test_run_keeps_issues_at_or_above_threshold(isolated_registry: _Registry) -> None:
    isolated_registry.register(
        _make_checker(
            "c_warning",
            [
                _issue("c_warning", "public.t_info", severity=Severity.INFO),
                _issue("c_warning", "public.t_warn", severity=Severity.WARNING),
                _issue("c_warning", "public.t_err", severity=Severity.ERROR),
            ],
        )
    )
    result = engine.run(_ctx(), threshold=Severity.WARNING.rank)
    objects = sorted(i.object_name for i in result.issues)
    assert objects == ["public.t_err", "public.t_warn"]


def test_run_keeps_all_at_info_threshold(isolated_registry: _Registry) -> None:
    isolated_registry.register(
        _make_checker(
            "c",
            [
                _issue("c", "public.a", severity=Severity.INFO),
                _issue("c", "public.b", severity=Severity.WARNING),
            ],
        )
    )
    result = engine.run(_ctx(), threshold=Severity.INFO.rank)
    assert len(result.issues) == 2


# ---------- version gating ----------


def test_run_records_version_gated_skip(isolated_registry: _Registry) -> None:
    # Checker requires PG 16+; ctx is on PG 15.
    isolated_registry.register(
        _make_checker("needs_pg16", [_issue("needs_pg16", "public.t")], min_version=160000)
    )
    result = engine.run(_ctx(server_version=150004), threshold=0)

    assert result.issues == []
    assert "needs_pg16" not in result.ran
    assert len(result.skipped) == 1
    assert result.skipped[0].checker == "needs_pg16"
    assert result.skipped[0].reason == "version_gated"
    assert "16+" in result.skipped[0].detail
    assert "15.4" in result.skipped[0].detail


def test_run_records_max_version_gated_skip(isolated_registry: _Registry) -> None:
    isolated_registry.register(
        _make_checker("obsolete", [_issue("obsolete", "public.t")], max_version=140000)
    )
    result = engine.run(_ctx(server_version=150004), threshold=0)
    assert result.issues == []
    assert "obsolete" not in result.ran
    assert result.skipped[0].reason == "version_gated"


# ---------- statement_timeout skip (needs real conn) ----------


class _SlowChecker(Checker):
    name: ClassVar[str] = "_test_slow_engine"
    description: ClassVar[str] = "test-only"
    default_severity: ClassVar[Severity] = Severity.WARNING

    def run(self, ctx: CheckerContext) -> Iterable[Issue]:
        with ctx.conn.cursor() as cur:
            cur.execute("SELECT pg_sleep(2)")
        yield _issue(self.name, "never.reached")


class _FastChecker(Checker):
    name: ClassVar[str] = "_test_fast_engine"
    description: ClassVar[str] = "test-only"
    default_severity: ClassVar[Severity] = Severity.WARNING

    def run(self, ctx: CheckerContext) -> Iterable[Issue]:
        yield _issue(self.name, "public.fast_finding")


def test_run_records_statement_timeout_skip_and_continues(
    conn: psycopg.Connection, monkeypatch: pytest.MonkeyPatch
) -> None:
    reg = _Registry()
    reg.register(_SlowChecker)
    reg.register(_FastChecker)
    monkeypatch.setattr("pgsleuth.engine.registry", reg)

    ctx = CheckerContext(
        conn=conn,
        config=Config(statement_timeout_ms=200),
        server_version=150004,
    )
    result = engine.run(ctx, threshold=Severity.INFO.rank)

    objects = {i.object_name for i in result.issues}
    assert "public.fast_finding" in objects
    assert "never.reached" not in objects

    assert "_test_fast_engine" in result.ran
    assert "_test_slow_engine" not in result.ran

    timeouts = [s for s in result.skipped if s.reason == "statement_timeout"]
    assert len(timeouts) == 1
    assert timeouts[0].checker == "_test_slow_engine"
    assert "200ms" in timeouts[0].detail


# ---------- baseline filtering ----------


def test_run_without_baseline_returns_zero_suppressed(isolated_registry: _Registry) -> None:
    isolated_registry.register(_make_checker("c", [_issue("c", "public.t")]))
    result = engine.run(_ctx(), threshold=0)
    assert result.suppressed_count == 0
    assert result.matched_baseline_fps == frozenset()
    assert result.stale_baseline_entries == ()
    assert result.unknown_baseline_entries == ()


def test_run_with_baseline_suppresses_matched(isolated_registry: _Registry) -> None:
    isolated_registry.register(
        _make_checker(
            "c",
            [
                _issue("c", "public.matched"),
                _issue("c", "public.new"),
            ],
        )
    )
    baseline = baseline_module.from_issues([_issue("c", "public.matched")])
    result = engine.run(_ctx(), threshold=0, baseline=baseline)

    assert result.suppressed_count == 1
    assert [i.object_name for i in result.issues] == ["public.new"]
    assert result.matched_baseline_fps == frozenset({fingerprint_for("c", "public.matched")})


def test_run_with_baseline_filters_stale_to_ran_checkers(isolated_registry: _Registry) -> None:
    """An entry whose checker actually ran but didn't reproduce → stale.
    An entry whose checker did *not* run → unknown (not stale).
    """
    isolated_registry.register(_make_checker("ran_checker", []))  # ran, no findings
    # Note: gone_checker is not registered → not in ran.

    baseline = baseline_module.from_issues(
        [
            _issue("ran_checker", "public.fixed"),  # checker ran, didn't produce → stale
            _issue("gone_checker", "public.unknown"),  # checker didn't run → unknown
        ]
    )
    result = engine.run(_ctx(), threshold=0, baseline=baseline)

    assert "ran_checker" in result.ran
    assert "gone_checker" not in result.ran

    stale_objects = {e.object for e in result.stale_baseline_entries}
    unknown_objects = {e.object for e in result.unknown_baseline_entries}
    assert stale_objects == {"public.fixed"}
    assert unknown_objects == {"public.unknown"}


def test_run_with_baseline_version_gated_checker_marked_unknown(
    isolated_registry: _Registry,
) -> None:
    """A version-gated checker isn't in `ran`, so its baseline entry is
    classified as unknown (we can't tell whether the finding still exists).
    Fixes the latent bug where a version-gated checker's baseline could be
    silently pruned.
    """
    isolated_registry.register(_make_checker("gated_checker", [], min_version=160000))

    baseline = baseline_module.from_issues([_issue("gated_checker", "public.t")])
    result = engine.run(_ctx(server_version=150004), threshold=0, baseline=baseline)

    assert "gated_checker" not in result.ran
    assert result.stale_baseline_entries == ()  # not stale — we couldn't run it
    assert {e.object for e in result.unknown_baseline_entries} == {"public.t"}


# ---------- open_context / UnsupportedServerVersionError ----------


def test_open_context_raises_on_unsupported_version() -> None:
    @contextmanager
    def fake_connect(_dsn):
        yield object()

    with (
        patch("pgsleuth.engine.connect", fake_connect),
        patch("pgsleuth.engine.server_version_num", return_value=90603),
    ):
        with pytest.raises(engine.UnsupportedServerVersionError) as exc_info:
            with engine.open_context("postgresql://x/y", Config()):
                pass

    assert exc_info.value.server_version == 90603
    msg = str(exc_info.value)
    assert "PostgreSQL 9.6 is not supported" in msg
    assert "Supported versions:" in msg


def test_open_context_yields_ctx_on_supported_version() -> None:
    sentinel = object()

    @contextmanager
    def fake_connect(_dsn):
        yield sentinel

    with (
        patch("pgsleuth.engine.connect", fake_connect),
        patch("pgsleuth.engine.server_version_num", return_value=150004),
    ):
        with engine.open_context("postgresql://x/y", Config()) as ctx:
            assert ctx.conn is sentinel
            assert ctx.server_version == 150004


# ---------- pure helpers ----------


def test_pg_version_str_post_pg10() -> None:
    assert engine.pg_version_str(150004) == "15.4"
    assert engine.pg_version_str(170000) == "17.0"


def test_pg_version_str_pre_pg10() -> None:
    assert engine.pg_version_str(90603) == "9.6"


def test_pg_version_label_min_only() -> None:
    assert engine.pg_version_label(140000, None) == "14+"


def test_pg_version_label_max_only() -> None:
    assert engine.pg_version_label(None, 160000) == "<16"


def test_pg_version_label_both() -> None:
    assert engine.pg_version_label(140000, 160000) == "14+ and <16"


def test_pg_version_label_neither() -> None:
    assert engine.pg_version_label(None, None) == "any"
