"""pgsleuth CLI entry point."""

from __future__ import annotations

import re
import sys
from pathlib import Path

import click
from rich.console import Console

import pgsleuth.checkers  # noqa: F401  -- registers built-in checkers
from pgsleuth import baseline as baseline_module
from pgsleuth import engine
from pgsleuth.checkers.base import Severity, registry
from pgsleuth.config import DEFAULT_EXCLUDED_SCHEMAS, Config
from pgsleuth.engine import RunResult
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


def _common_options(f):
    """Click options shared by `check` and `baseline write`.

    Applied in reverse source order: the bottom-most option is the
    *outermost* decorator and so appears first in --help.
    """
    f = click.option(
        "--no-statement-timeout",
        "no_statement_timeout",
        is_flag=True,
        default=False,
        help="Disable the per-checker statement timeout entirely.",
    )(f)
    f = click.option(
        "--statement-timeout",
        "statement_timeout_seconds",
        type=float,
        default=None,
        help=(
            "Per-checker SQL timeout in seconds. Overrides the project default (5s) "
            "and any TOML setting. Per-checker overrides go in pgsleuth.toml."
        ),
    )(f)
    f = click.option(
        "--config",
        "config_path",
        type=click.Path(exists=True, dir_okay=False, path_type=Path),
    )(f)
    f = click.option(
        "--exclude-table",
        "exclude_tables",
        multiple=True,
        help="Regex pattern for tables to skip. Pass multiple times.",
    )(f)
    f = click.option(
        "--exclude-schema",
        "exclude_schemas",
        multiple=True,
        help=(
            f"Schemas to skip. Pass multiple times. Default: {', '.join(DEFAULT_EXCLUDED_SCHEMAS)}."
        ),
    )(f)
    f = click.option(
        "--checkers",
        "checker_filter",
        default=None,
        help="Comma-separated list of checker names to run (default: all).",
    )(f)
    f = click.option(
        "--dsn",
        required=True,
        envvar="PGSLEUTH_DSN",
        help="Postgres DSN.",
    )(f)
    return f


def _build_config_from_options(
    *,
    config_path: Path | None,
    exclude_schemas: tuple[str, ...],
    exclude_tables: tuple[str, ...],
    checker_filter: str | None,
    statement_timeout_seconds: float | None,
    no_statement_timeout: bool,
) -> Config:
    """Apply shared CLI options to a Config and return it.

    Raises click.UsageError on conflicting flags or unknown checker names —
    the caller's Click context translates that into a usage-error exit.
    """
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

    return config


def _print_skipped(result: RunResult) -> None:
    """Emit one stderr line per skipped checker (version-gated or timed out)."""
    for sk in result.skipped:
        click.echo(f"[skipped] {sk.checker} — {sk.detail}", err=True)


def _run_engine(dsn: str, config: Config, threshold: int, baseline=None) -> RunResult:
    """Open a context and dispatch the engine, translating engine errors to exit-2.

    Centralizes the try/except so the three subcommands stay focused on their
    own argument-handling. `engine.UnsupportedServerVersionError` and any other
    DB exception both exit 2 with the message echoed to stderr.
    """
    try:
        with engine.open_context(dsn, config) as ctx:
            return engine.run(ctx, threshold=threshold, baseline=baseline)
    except engine.UnsupportedServerVersionError as exc:
        click.echo(f"pgsleuth: {exc}", err=True)
        sys.exit(2)
    except Exception as exc:  # noqa: BLE001
        click.echo(f"pgsleuth: {exc}", err=True)
        sys.exit(2)


@main.command("check")
@_common_options
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
    "--baseline",
    "baseline_path",
    type=click.Path(dir_okay=False, path_type=Path),
    default=None,
    help=(
        f"Suppress findings listed in this baseline file. If omitted, "
        f"./{baseline_module.DEFAULT_BASELINE_PATH} is auto-discovered when present."
    ),
)
@click.option(
    "--no-baseline",
    "no_baseline",
    is_flag=True,
    default=False,
    help="Disable baseline auto-discovery — report every finding.",
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
    baseline_path: Path | None,
    no_baseline: bool,
) -> None:
    """Run consistency checks against a database."""
    if baseline_path is not None and no_baseline:
        raise click.UsageError("--baseline and --no-baseline are mutually exclusive.")

    config = _build_config_from_options(
        config_path=config_path,
        exclude_schemas=exclude_schemas,
        exclude_tables=exclude_tables,
        checker_filter=checker_filter,
        statement_timeout_seconds=statement_timeout_seconds,
        no_statement_timeout=no_statement_timeout,
    )

    threshold = Severity(min_severity).rank

    # Resolve and load the baseline (if any) BEFORE running checkers, so a
    # corrupt or unreadable baseline fails fast without paying for a full DB
    # scan first.
    effective_baseline_path = _resolve_baseline_path(baseline_path, no_baseline)
    baseline = None
    if effective_baseline_path is not None:
        try:
            baseline = baseline_module.load(effective_baseline_path)
        except baseline_module.BaselineError as exc:
            click.echo(f"pgsleuth: {exc}", err=True)
            sys.exit(2)

    result = _run_engine(dsn, config, threshold, baseline=baseline)
    _print_skipped(result)

    if result.stale_baseline_entries:
        n = len(result.stale_baseline_entries)
        click.echo(
            f"pgsleuth: {n} baseline "
            f"{'entry' if n == 1 else 'entries'} did not reproduce. "
            f"Run 'pgsleuth baseline prune' to clean up.",
            err=True,
        )

    if output_format == "json":
        json_reporter.render(result.issues, suppressed=result.suppressed_count)
    else:
        text_reporter.render(result.issues, suppressed=result.suppressed_count)

    sys.exit(1 if result.issues else 0)


@main.group("baseline")
def baseline_group() -> None:
    """Manage the pgsleuth baseline file."""


@baseline_group.command("write")
@_common_options
@click.option(
    "--output",
    "-o",
    "output_path",
    type=click.Path(dir_okay=False, path_type=Path),
    default=baseline_module.DEFAULT_BASELINE_PATH,
    show_default=True,
    help="Where to write the baseline file. Overwrites any existing file freely.",
)
def baseline_write(
    dsn: str,
    checker_filter: str | None,
    exclude_schemas: tuple[str, ...],
    exclude_tables: tuple[str, ...],
    config_path: Path | None,
    statement_timeout_seconds: float | None,
    no_statement_timeout: bool,
    output_path: Path,
) -> None:
    """Snapshot every current finding to a baseline file.

    Runs every enabled checker at info+ severity (no `--min-severity`
    filter — the baseline should cover everything you might later raise
    a threshold for) and writes the resulting fingerprints to
    `--output` (default: pgsleuth.baseline.json in cwd). Overwrites
    any existing file freely; the team is in version control.
    """
    config = _build_config_from_options(
        config_path=config_path,
        exclude_schemas=exclude_schemas,
        exclude_tables=exclude_tables,
        checker_filter=checker_filter,
        statement_timeout_seconds=statement_timeout_seconds,
        no_statement_timeout=no_statement_timeout,
    )

    # Capture everything; threshold=info means "all severities count".
    result = _run_engine(dsn, config, Severity.INFO.rank)
    _print_skipped(result)

    baseline = baseline_module.from_issues(result.issues)
    baseline_module.dump(baseline, output_path)
    n = len(baseline.fingerprints)
    click.echo(
        f"Wrote {n} {'finding' if n == 1 else 'findings'} to {output_path}",
        err=True,
    )


@baseline_group.command("show")
@click.option(
    "--baseline",
    "baseline_path",
    type=click.Path(exists=True, dir_okay=False, path_type=Path),
    default=baseline_module.DEFAULT_BASELINE_PATH,
    show_default=True,
    help="Path to the baseline file to display.",
)
def baseline_show(baseline_path: Path) -> None:
    """Display the contents of a baseline file in a human-readable form.

    Read-only — does not connect to a database. Useful for auditing what
    a team has accepted in their baseline before agreeing to a PR that
    modifies it.
    """
    try:
        baseline = baseline_module.load(baseline_path)
    except baseline_module.BaselineError as exc:
        click.echo(f"pgsleuth: {exc}", err=True)
        sys.exit(2)

    console = Console()
    console.print(f"[bold]Baseline:[/bold] {baseline_path}")
    console.print(f"[dim]Generated:[/dim] {baseline.generated_at}")

    n_entries = len(baseline.fingerprints)
    n_checkers = len({e.checker for e in baseline.fingerprints})
    if n_entries == 0:
        console.print("[dim]Entries:[/dim] 0")
        console.print()
        console.print("[dim](empty)[/dim]")
        return
    console.print(
        f"[dim]Entries:[/dim] {n_entries} "
        f"({n_checkers} {'checker' if n_checkers == 1 else 'checkers'})"
    )
    console.print()

    grouped: dict[str, list[baseline_module.BaselineEntry]] = {}
    for entry in baseline.fingerprints:
        grouped.setdefault(entry.checker, []).append(entry)

    for checker_name in sorted(grouped):
        entries = grouped[checker_name]
        console.rule(f"[bold]{checker_name}[/bold] ({len(entries)})")
        for entry in sorted(entries, key=lambda e: e.object):
            console.print(f"  {entry.object}")
        console.print()


@baseline_group.command("prune")
@_common_options
@click.option(
    "--baseline",
    "baseline_path",
    type=click.Path(exists=True, dir_okay=False, path_type=Path),
    default=baseline_module.DEFAULT_BASELINE_PATH,
    show_default=True,
    help="Path to the baseline file to prune.",
)
@click.option(
    "--dry-run",
    "dry_run",
    is_flag=True,
    default=False,
    help="Show what would change but don't write the file.",
)
@click.option(
    "--ignore-unknown-checkers",
    "ignore_unknown_checkers",
    is_flag=True,
    default=False,
    help=(
        "Suppress the warning about baseline entries whose checker is no longer "
        "registered. Those entries are preserved either way (warn-before-remove)."
    ),
)
def baseline_prune(
    dsn: str,
    checker_filter: str | None,
    exclude_schemas: tuple[str, ...],
    exclude_tables: tuple[str, ...],
    config_path: Path | None,
    statement_timeout_seconds: float | None,
    no_statement_timeout: bool,
    baseline_path: Path,
    dry_run: bool,
    ignore_unknown_checkers: bool,
) -> None:
    """Remove baseline entries that no longer reproduce.

    Loads the baseline, runs every enabled checker at info+ severity (a
    user's --min-severity is intentionally ignored here so we don't drop
    entries the current run merely wouldn't surface), and rewrites the
    file with stale entries removed. Entries whose checker did not run
    in this invocation (filtered out, version-gated, or timed out) are
    kept and warned about — pass --ignore-unknown-checkers to silence.
    """
    config = _build_config_from_options(
        config_path=config_path,
        exclude_schemas=exclude_schemas,
        exclude_tables=exclude_tables,
        checker_filter=checker_filter,
        statement_timeout_seconds=statement_timeout_seconds,
        no_statement_timeout=no_statement_timeout,
    )

    try:
        baseline = baseline_module.load(baseline_path)
    except baseline_module.BaselineError as exc:
        click.echo(f"pgsleuth: {exc}", err=True)
        sys.exit(2)

    # Capture every finding regardless of user's --min-severity, so we don't
    # mistake "did not surface in this run" for "no longer present."
    result = _run_engine(dsn, config, Severity.INFO.rank, baseline=baseline)
    _print_skipped(result)

    unknowns = result.unknown_baseline_entries
    if unknowns and not ignore_unknown_checkers:
        unknown_names = ", ".join(sorted({e.checker for e in unknowns}))
        click.echo(
            f"pgsleuth: {len(unknowns)} baseline "
            f"{'entry has' if len(unknowns) == 1 else 'entries have'} "
            f"a checker not run in this invocation: {unknown_names}. "
            f"Preserved (warn-before-remove). "
            f"Pass --ignore-unknown-checkers to silence.",
            err=True,
        )

    pruned = baseline_module.prune(baseline, result.matched_baseline_fps, known_checkers=result.ran)
    pruned_set = set(pruned.fingerprints)
    removed = [e for e in baseline.fingerprints if e not in pruned_set]

    word = "entry" if len(removed) == 1 else "entries"
    if dry_run:
        click.echo(
            f"pgsleuth: dry run — would remove {len(removed)} stale {word}. "
            f"Re-run without --dry-run to apply.",
            err=True,
        )
    else:
        baseline_module.dump(pruned, baseline_path)
        click.echo(
            f"pgsleuth: removed {len(removed)} stale {word}; "
            f"baseline now has {len(pruned.fingerprints)} "
            f"{'entry' if len(pruned.fingerprints) == 1 else 'entries'}.",
            err=True,
        )

    for entry in removed:
        click.echo(f"  - {entry.checker}: {entry.object}", err=True)


def _resolve_baseline_path(explicit_path: Path | None, no_baseline: bool) -> Path | None:
    """Pick the effective baseline file path.

    Precedence:
        --no-baseline       → None (skip even auto-discovery)
        --baseline PATH     → PATH
        ./pgsleuth.baseline.json exists → it (with a stderr notice so the
                              user is never surprised that suppression
                              happened on their behalf)
        otherwise           → None (no baseline in effect)
    """
    if no_baseline:
        return None
    if explicit_path is not None:
        return explicit_path
    if baseline_module.DEFAULT_BASELINE_PATH.exists():
        click.echo(
            f"pgsleuth: using {baseline_module.DEFAULT_BASELINE_PATH} "
            f"(auto-discovered; pass --no-baseline to skip)",
            err=True,
        )
        return baseline_module.DEFAULT_BASELINE_PATH
    return None


if __name__ == "__main__":
    main()
