"""CLI exits cleanly when the connected server is below the supported floor."""

from __future__ import annotations

from contextlib import contextmanager
from unittest.mock import patch

from click.testing import CliRunner

from pgsleuth.cli import main


@contextmanager
def _fake_connect(_dsn: str):
    yield object()  # pretend connection; never used because we mock the version helper


def test_unsupported_version_exits_with_message() -> None:
    runner = CliRunner()
    with (
        patch("pgsleuth.cli.connect", _fake_connect),
        patch("pgsleuth.cli.server_version_num", return_value=90603),  # PG 9.6.3
    ):
        result = runner.invoke(main, ["check", "--dsn", "postgresql://x/y"])

    assert result.exit_code == 2
    assert "PostgreSQL 9.6 is not supported" in result.output
    assert "Supported versions:" in result.output


def test_supported_version_does_not_refuse() -> None:
    # When the server is supported but no checkers find anything, exit 0.
    runner = CliRunner()
    fake_issues: list = []
    with (
        patch("pgsleuth.cli.connect", _fake_connect),
        patch("pgsleuth.cli.server_version_num", return_value=150004),
        patch("pgsleuth.cli._run_all", return_value=iter(fake_issues)),
    ):
        result = runner.invoke(main, ["check", "--dsn", "postgresql://x/y"])

    assert result.exit_code == 0
    assert "is not supported" not in result.output
