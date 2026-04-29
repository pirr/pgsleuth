"""pgsleuth CLI entry point."""

from __future__ import annotations

import re
import sys
from contextlib import nullcontext
from pathlib import Path
from typing import Iterable

import click
import psycopg
from rich.console import Console

import pgsleuth.checkers  # noqa: F401  -- registers built-in checkers
from pgsleuth.checkers.base import Issue, Severity, registry
from pgsleuth.config import DEFAULT_EXCLUDED_SCHEMAS, Config
from pgsleuth.context import CheckerContext
from pgsleuth.db.connection import (
    SUPPORTED_VERSION_MIN,
    SUPPORTED_VERSION_NAMES,
    connect,
    server_version_num,
    statement_timeout,
)
from pgsleuth.reporters import json as json_reporter
from pgsleuth.reporters import text as text_reporter


@click.group()
@click.version_option()
def main() -> None:
    """pgsleuth — database consistency checker."""


@main.command("list-checkers")
def list_checkers() -> None:
    """Print all registered checkers."""
    console = Console()
    for cls in sorted(registry.all(), key=lambda c: c.name):
        console.print(f"[bold]{cls.name}[/bold] [dim]({cls.default_severity.value})[/dim]")
        console.print(f"  {cls.description}")


@main.command("check")
@click.option("--dsn", required=True, envvar="PGSLEUTH_DSN", help="Postgres DSN.")
@click.option(
    "--checkers",
    "checker_filter",
    default=None,
    help="Comma-separated list of checker names to run (default: all).",
)
@click.option(
    "--exclude-schema",
    "exclude_schemas",
    multiple=True,
    help=(f"Schemas to skip. Pass multiple times. Default: {', '.join(DEFAULT_EXCLUDED_SCHEMAS)}."),
)
@click.option(
    "--exclude-table",
    "exclude_tables",
    multiple=True,
    help="Regex pattern for tables to skip. Pass multiple times.",
)
@click.option(
    "--format",
    "output_format",
    type=click.Choice(["text", "json"]),
    default="text",
)
@click.option(
    "--min-severity",
    type=click.Choice([s.value for s in Severity]),
    default="info",
)
@click.option(
    "--config",
    "config_path",
    type=click.Path(exists=True, dir_okay=False, path_type=Path),
)
@click.option(
    "--statement-timeout",
    "statement_timeout_seconds",
    type=float,
    default=None,
    help=(
        "Per-checker SQL timeout in seconds. Overrides the project default (5s) "
        "and any TOML setting. Per-checker overrides go in pgsleuth.toml."
    ),
)
@click.option(
    "--no-statement-timeout",
    "no_statement_timeout",
    is_flag=True,
    default=False,
    help="Disable the per-checker statement timeout entirely.",
)
def check(
    dsn: str,
    checker_filter: str | None,
    exclude_schemas: tuple[str, ...],
    exclude_tables: tuple[str, ...],
    output_format: str,
    min_severity: str,
    config_path: Path | None,
    statement_timeout_seconds: float | None,
    no_statement_timeout: bool,
) -> None:
    """Run consistency checks against a database."""
    if statement_timeout_seconds is not None and no_statement_timeout:
        raise click.UsageError(
            "--statement-timeout and --no-statement-timeout are mutually exclusive."
        )

    config = Config.from_file(config_path) if config_path else Config()

    if exclude_schemas:
        config.excluded_schemas = tuple(exclude_schemas)
    if exclude_tables:
        config.excluded_table_patterns = tuple(re.compile(p) for p in exclude_tables)
    if checker_filter:
        config.enabled_checkers = frozenset(
            s.strip() for s in checker_filter.split(",") if s.strip()
        )
        for name in config.enabled_checkers:
            if name not in registry.names():
                raise click.UsageError(f"unknown checker: {name!r}")
    if no_statement_timeout:
        config.statement_timeout_ms = None
    elif statement_timeout_seconds is not None:
        config.statement_timeout_ms = int(statement_timeout_seconds * 1000)

    threshold = Severity(min_severity).rank

    try:
        issues = _collect_issues(dsn, config, threshold)
    except Exception as exc:  # noqa: BLE001
        click.echo(f"pgsleuth: {exc}", err=True)
        sys.exit(2)

    if output_format == "json":
        json_reporter.render(issues)
    else:
        text_reporter.render(issues)

    sys.exit(1 if issues else 0)


def _collect_issues(dsn: str, config: Config, threshold: int) -> list[Issue]:
    """Connect, version-check, run enabled checkers, return filtered issues.

    Shared by the `check` command and the upcoming `baseline write` /
    `baseline prune` subcommands so the connect-and-run pipeline lives
    in one place. On unsupported server version, prints a stderr message
    and `sys.exit(2)` — propagates as `SystemExit`, which the caller's
    `except Exception` does not catch (intentional). Other DB errors
    propagate as ordinary exceptions for the caller to log.
    """
    with connect(dsn) as conn:
        version = server_version_num(conn)
        if version < SUPPORTED_VERSION_MIN:
            click.echo(
                f"pgsleuth: PostgreSQL {_pg_version_str(version)} is not supported. "
                f"Supported versions: {SUPPORTED_VERSION_NAMES}.",
                err=True,
            )
            sys.exit(2)
        ctx = CheckerContext(conn=conn, config=config, server_version=version)
        return list(_run_all(ctx, threshold))


def _run_all(ctx: CheckerContext, threshold: int) -> Iterable[Issue]:
    for cls in registry.all():
        if not ctx.config.is_checker_enabled(cls.name):
            continue
        if not cls.supports(ctx.server_version):
            click.echo(
                f"[skipped] {cls.name} — requires PostgreSQL "
                f"{_pg_version_label(cls.min_version, cls.max_version)} "
                f"(connected: {_pg_version_str(ctx.server_version)})",
                err=True,
            )
            continue

        timeout_ms = ctx.config.statement_timeout_for(cls.name)
        cm = statement_timeout(ctx.conn, timeout_ms) if timeout_ms is not None else nullcontext()
        # Materialize per-checker so a QueryCanceled mid-yield discards partial
        # findings rather than surfacing a half-result.
        try:
            with cm:
                checker_issues = list(cls().run(ctx))
        except psycopg.errors.QueryCanceled:
            click.echo(
                f"[skipped] {cls.name} — exceeded statement_timeout of {timeout_ms}ms",
                err=True,
            )
            continue

        for issue in checker_issues:
            if issue.severity.rank >= threshold:
                yield issue


def _pg_version_str(num: int) -> str:
    # PG10 changed the encoding: pre-10 is M_mm_pp (e.g. 90603 = 9.6.3),
    # post-10 is M0_mmmm (e.g. 150004 = 15.4).
    if num >= 100000:
        return f"{num // 10000}.{num % 10000}"
    return f"{num // 10000}.{(num // 100) % 100}"


def _pg_version_label(min_version: int | None, max_version: int | None) -> str:
    parts = []
    if min_version is not None:
        parts.append(f"{min_version // 10000}+")
    if max_version is not None:
        parts.append(f"<{max_version // 10000}")
    return " and ".join(parts) if parts else "any"


if __name__ == "__main__":
    main()
