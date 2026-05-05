"""Checker-dispatch engine.

The CLI is a shell — this module is what makes pgsleuth pgsleuth. Build a
`Config`, open a context with `open_context(dsn, config)`, call `run(ctx, ...)`,
hand the resulting `RunResult` to a reporter (or to whatever your library code
needs).

Pure: no Click imports, no `sys.exit`, no stderr writes. Per-checker timeouts
and version-gating are returned as `SkippedChecker` records instead of being
printed; an unsupported server version raises `UnsupportedServerVersionError`
instead of exiting. Callers (the CLI, library users) decide how to surface
those.
"""

from __future__ import annotations

from contextlib import contextmanager, nullcontext
from dataclasses import dataclass
from typing import Iterator, Literal

import psycopg

from pgsleuth import baseline as baseline_module
from pgsleuth.baseline import Baseline, BaselineEntry
from pgsleuth.checkers.base import Issue, registry
from pgsleuth.config import Config
from pgsleuth.context import CheckerContext
from pgsleuth.db.connection import (
    SUPPORTED_VERSION_MIN,
    SUPPORTED_VERSION_NAMES,
    connect,
    server_version_num,
    statement_timeout,
)

SkipReason = Literal["version_gated", "statement_timeout"]


@dataclass(frozen=True)
class SkippedChecker:
    """A checker that didn't run to completion this invocation.

    `detail` is a ready-to-display human string (the CLI prints it after
    `[skipped] <name> — `; library callers can log it as-is).
    """

    checker: str
    reason: SkipReason
    detail: str


@dataclass(frozen=True)
class RunResult:
    """Outcome of a single engine.run() invocation.

    `issues` is post-threshold and (if a baseline was supplied) post-baseline.
    `ran` is the precise set of checker names that actually executed — a
    checker filtered out by config, version-gated, or aborted by
    `statement_timeout` is *not* in `ran`. Use this set (rather than the
    config-only "enabled" set) to reason about which baseline entries are
    stale: a checker that didn't run can't tell us whether its findings are
    gone.
    """

    issues: list[Issue]
    skipped: tuple[SkippedChecker, ...]
    ran: frozenset[str]
    suppressed_count: int = 0
    matched_baseline_fps: frozenset[str] = frozenset()
    stale_baseline_entries: tuple[BaselineEntry, ...] = ()
    unknown_baseline_entries: tuple[BaselineEntry, ...] = ()


class UnsupportedServerVersionError(Exception):
    """Raised by `open_context` when the connected server is below the floor."""

    def __init__(self, server_version: int) -> None:
        super().__init__(
            f"PostgreSQL {pg_version_str(server_version)} is not supported. "
            f"Supported versions: {SUPPORTED_VERSION_NAMES}."
        )
        self.server_version = server_version


@contextmanager
def open_context(dsn: str, config: Config) -> Iterator[CheckerContext]:
    """Connect, verify the server version, yield a CheckerContext.

    Raises `UnsupportedServerVersionError` for servers below
    `SUPPORTED_VERSION_MIN`. Other connection errors propagate as ordinary
    `psycopg` exceptions for the caller to handle.
    """
    with connect(dsn) as conn:
        version = server_version_num(conn)
        if version < SUPPORTED_VERSION_MIN:
            raise UnsupportedServerVersionError(version)
        yield CheckerContext(conn=conn, config=config, server_version=version)


def run(
    ctx: CheckerContext,
    *,
    threshold: int,
    baseline: Baseline | None = None,
) -> RunResult:
    """Run every enabled checker; return findings, skips, and optional baseline summary.

    For each registered checker:

    - Skip silently if the config disables it (filter not surfaced — the user
      asked for it).
    - Record a `SkippedChecker(reason="version_gated")` if the connected
      server is outside the checker's `[min_version, max_version)` range.
    - Apply the per-checker `statement_timeout` and materialize results to a
      list inside the timeout block (so a mid-iteration `QueryCanceled`
      drops partial findings rather than leaking them); on cancel, record a
      `SkippedChecker(reason="statement_timeout")` and continue with the
      next checker.
    - Otherwise add the checker to `ran` and append findings whose severity
      meets `threshold`.

    With `baseline` supplied, fingerprints in the baseline are filtered out
    of `issues` (counted in `suppressed_count`); `stale_baseline_entries`
    enumerates baseline entries whose checker `ran` but whose fingerprint
    didn't reproduce, and `unknown_baseline_entries` enumerates entries whose
    checker did *not* run (filtered, gated, or timed out — we have no
    information about whether their findings still exist).
    """
    issues: list[Issue] = []
    skipped: list[SkippedChecker] = []
    ran: list[str] = []

    for cls in registry.all():
        if not ctx.config.is_checker_enabled(cls.name):
            continue
        if not cls.supports(ctx.server_version):
            skipped.append(
                SkippedChecker(
                    checker=cls.name,
                    reason="version_gated",
                    detail=(
                        f"requires PostgreSQL "
                        f"{pg_version_label(cls.min_version, cls.max_version)} "
                        f"(connected: {pg_version_str(ctx.server_version)})"
                    ),
                )
            )
            continue

        timeout_ms = ctx.config.statement_timeout_for(cls.name)
        cm = statement_timeout(ctx.conn, timeout_ms) if timeout_ms is not None else nullcontext()
        try:
            with cm:
                checker_issues = list(cls().run(ctx))
        except psycopg.errors.QueryCanceled:
            skipped.append(
                SkippedChecker(
                    checker=cls.name,
                    reason="statement_timeout",
                    detail=f"exceeded statement_timeout of {timeout_ms}ms",
                )
            )
            continue

        ran.append(cls.name)
        for issue in checker_issues:
            if issue.severity.rank >= threshold:
                issues.append(issue)

    ran_set = frozenset(ran)

    if baseline is None:
        return RunResult(
            issues=issues,
            skipped=tuple(skipped),
            ran=ran_set,
        )

    filtered = baseline_module.filter_issues(issues, baseline)
    stale = tuple(
        e
        for e in baseline_module.stale_entries(baseline, filtered.matched_fps)
        if e.checker in ran_set
    )
    unknown = tuple(baseline_module.unknown_checker_entries(baseline, ran_set))

    return RunResult(
        issues=filtered.kept,
        skipped=tuple(skipped),
        ran=ran_set,
        suppressed_count=filtered.suppressed_count,
        matched_baseline_fps=filtered.matched_fps,
        stale_baseline_entries=stale,
        unknown_baseline_entries=unknown,
    )


def pg_version_str(num: int) -> str:
    """Format a PG-encoded version int as a human string ("15.4", "9.6.3")."""
    # PG10 changed the encoding: pre-10 is M_mm_pp (e.g. 90603 = 9.6.3),
    # post-10 is M0_mmmm (e.g. 150004 = 15.4).
    if num >= 100000:
        return f"{num // 10000}.{num % 10000}"
    return f"{num // 10000}.{(num // 100) % 100}"


def pg_version_label(min_version: int | None, max_version: int | None) -> str:
    """Render a checker's version gate as "10+", "<14", "10+ and <14", or "any"."""
    parts = []
    if min_version is not None:
        parts.append(f"{min_version // 10000}+")
    if max_version is not None:
        parts.append(f"<{max_version // 10000}")
    return " and ".join(parts) if parts else "any"
