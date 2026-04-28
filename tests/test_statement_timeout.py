"""Tests for per-checker statement_timeout: config, CLI, and runtime behavior."""

from __future__ import annotations

from contextlib import contextmanager
from pathlib import Path
from typing import ClassVar, Iterable
from unittest.mock import patch

import psycopg
import pytest
from click.testing import CliRunner

from pgsleuth.checkers.base import Checker, Issue, Severity
from pgsleuth.cli import main
from pgsleuth.config import DEFAULT_STATEMENT_TIMEOUT_MS, CheckerOverride, Config
from pgsleuth.context import CheckerContext
from pgsleuth.db.connection import statement_timeout


# ---------- Unit tests on Config (no DB) ----------


def test_config_defaults_to_5s() -> None:
    assert Config().statement_timeout_ms == DEFAULT_STATEMENT_TIMEOUT_MS == 5000


def test_config_per_checker_override_wins() -> None:
    cfg = Config(
        statement_timeout_ms=5000,
        checker_overrides={"redundant_index": CheckerOverride(statement_timeout_ms=1000)},
    )
    assert cfg.statement_timeout_for("redundant_index") == 1000
    assert cfg.statement_timeout_for("missing_primary_key") == 5000


def test_config_none_means_no_timeout() -> None:
    cfg = Config(statement_timeout_ms=None)
    assert cfg.statement_timeout_for("anything") is None


def test_config_per_checker_override_none_falls_through_to_project_default() -> None:
    # If override is set but its timeout field is None, the project default applies.
    cfg = Config(
        statement_timeout_ms=5000,
        checker_overrides={"x": CheckerOverride(severity=Severity.ERROR)},
    )
    assert cfg.statement_timeout_for("x") == 5000


def test_config_from_file_parses_project_and_per_checker(tmp_path: Path) -> None:
    config_path = tmp_path / "pgsleuth.toml"
    config_path.write_text(
        """
[pgsleuth]
statement_timeout_ms = 10000

[pgsleuth.checkers.redundant_index]
statement_timeout_ms = 30000
"""
    )
    cfg = Config.from_file(config_path)
    assert cfg.statement_timeout_ms == 10000
    assert cfg.statement_timeout_for("redundant_index") == 30000
    assert cfg.statement_timeout_for("missing_primary_key") == 10000


def test_config_from_file_uses_default_when_omitted(tmp_path: Path) -> None:
    config_path = tmp_path / "pgsleuth.toml"
    config_path.write_text("[pgsleuth]\nexclude_schemas = ['public']\n")
    cfg = Config.from_file(config_path)
    assert cfg.statement_timeout_ms == 5000


# ---------- CLI flag tests (mocked, no DB) ----------


@contextmanager
def _fake_connect(_dsn: str):
    yield object()


def _capture_config(captured: list[Config]):
    """Return a side_effect that captures the CheckerContext.config and yields no findings."""

    def wrapper(ctx: CheckerContext, threshold: int):
        captured.append(ctx.config)
        return iter(())

    return wrapper


def test_cli_flag_overrides_config() -> None:
    runner = CliRunner()
    captured: list[Config] = []
    with (
        patch("pgsleuth.cli.connect", _fake_connect),
        patch("pgsleuth.cli.server_version_num", return_value=150004),
        patch("pgsleuth.cli._run_all", side_effect=_capture_config(captured)),
    ):
        result = runner.invoke(
            main,
            ["check", "--dsn", "postgresql://x/y", "--statement-timeout", "12.5"],
        )
    assert result.exit_code == 0, result.output
    assert captured and captured[0].statement_timeout_ms == 12500


def test_cli_no_statement_timeout_flag() -> None:
    runner = CliRunner()
    captured: list[Config] = []
    with (
        patch("pgsleuth.cli.connect", _fake_connect),
        patch("pgsleuth.cli.server_version_num", return_value=150004),
        patch("pgsleuth.cli._run_all", side_effect=_capture_config(captured)),
    ):
        result = runner.invoke(
            main,
            ["check", "--dsn", "postgresql://x/y", "--no-statement-timeout"],
        )
    assert result.exit_code == 0, result.output
    assert captured and captured[0].statement_timeout_ms is None


def test_cli_conflicting_flags_raise_usage_error() -> None:
    runner = CliRunner()
    with (
        patch("pgsleuth.cli.connect", _fake_connect),
        patch("pgsleuth.cli.server_version_num", return_value=150004),
    ):
        result = runner.invoke(
            main,
            [
                "check",
                "--dsn",
                "postgresql://x/y",
                "--statement-timeout",
                "5",
                "--no-statement-timeout",
            ],
        )
    assert result.exit_code == 2
    assert "--statement-timeout" in result.output and "--no-statement-timeout" in result.output


# ---------- Integration tests (testcontainers via the conn fixture) ----------


def test_context_manager_sets_and_resets(conn: psycopg.Connection) -> None:
    with conn.cursor() as cur:
        cur.execute("SHOW statement_timeout")
        before = cur.fetchone()[0]

    with statement_timeout(conn, 250):
        with conn.cursor() as cur:
            cur.execute("SHOW statement_timeout")
            inside = cur.fetchone()[0]

    with conn.cursor() as cur:
        cur.execute("SHOW statement_timeout")
        after = cur.fetchone()[0]

    # 250 ms shows up as "250ms" in SHOW output.
    assert inside == "250ms"
    # After the block, we're back to whatever the server default was; not asserting
    # the exact string because that's environment-dependent. Just confirm it's not
    # "250ms" anymore.
    assert after != "250ms"
    # And before == after (RESET went back to the same default).
    assert before == after


def test_query_exceeding_timeout_raises_querycanceled(conn: psycopg.Connection) -> None:
    with statement_timeout(conn, 200), pytest.raises(psycopg.errors.QueryCanceled):
        with conn.cursor() as cur:
            cur.execute("SELECT pg_sleep(2)")


# Synthetic checkers used by the slow-checker test below.


class _SlowChecker(Checker):
    name: ClassVar[str] = "_test_slow"
    description: ClassVar[str] = "test-only"
    default_severity: ClassVar[Severity] = Severity.WARNING

    def run(self, ctx: CheckerContext) -> Iterable[Issue]:
        with ctx.conn.cursor() as cur:
            cur.execute("SELECT pg_sleep(2)")
        # Never reached when the timeout is tight.
        yield Issue(
            checker=self.name,
            severity=Severity.WARNING,
            object_type="table",
            object_name="never.reached",
            message="should never appear when the slow checker is timed out",
        )


class _FastChecker(Checker):
    name: ClassVar[str] = "_test_fast"
    description: ClassVar[str] = "test-only"
    default_severity: ClassVar[Severity] = Severity.WARNING

    def run(self, ctx: CheckerContext) -> Iterable[Issue]:
        yield Issue(
            checker=self.name,
            severity=Severity.WARNING,
            object_type="table",
            object_name="public.fast_finding",
            message="this finding must survive the slow-checker timeout",
        )


def test_run_all_skips_slow_checker_continues_with_others(
    conn: psycopg.Connection, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A slow checker is skipped; the other checker's findings still surface."""
    from pgsleuth.checkers.base import _Registry
    from pgsleuth.cli import _run_all

    test_registry = _Registry()
    test_registry.register(_SlowChecker)
    test_registry.register(_FastChecker)
    monkeypatch.setattr("pgsleuth.cli.registry", test_registry)

    cfg = Config(statement_timeout_ms=200)
    ctx = CheckerContext(conn=conn, config=cfg, server_version=150004)

    issues = list(_run_all(ctx, threshold=Severity.INFO.rank))

    object_names = {i.object_name for i in issues}
    assert "public.fast_finding" in object_names
    assert "never.reached" not in object_names
