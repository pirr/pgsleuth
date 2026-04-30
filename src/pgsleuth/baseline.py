"""Baseline mode: snapshot, suppress, and prune known findings.

A baseline is a JSON file listing fingerprints of findings that the team has
explicitly accepted (or has not gotten around to fixing). At check time, any
finding whose fingerprint is in the baseline is suppressed — CI fails only
on *new* findings.

The fingerprint is `sha256(checker + "\\0" + object_name)`, so phrasing
changes in the human-readable message don't break the baseline. Two findings
are "the same" when their (checker, object_name) pair matches.

This module is pure: no I/O at import time, no global state. The CLI wires
it into `check`, `baseline write`, and `baseline prune` subcommands.
"""

from __future__ import annotations

import hashlib
import json
import os
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable

from pgsleuth.checkers.base import Issue

BASELINE_VERSION = 1
DEFAULT_BASELINE_PATH = Path("pgsleuth.baseline.json")

# Algorithm tag baked into the fingerprint hash. Bumping this string
# invalidates every previously-generated fingerprint, which is the desired
# behavior if the algorithm or normalization rules ever change. Old entries
# in an existing baseline file simply stop matching and become "stale" at
# the next prune. File format version (BASELINE_VERSION) is independent —
# we can rotate the fingerprint algorithm without breaking the file shape.
_FINGERPRINT_ALGORITHM = "v1"


class BaselineError(Exception):
    """Raised for unreadable, malformed, or version-mismatched baseline files."""


@dataclass(frozen=True)
class BaselineEntry:
    """One fingerprinted finding in the baseline.

    `checker` and `object` are kept alongside the hash so a human can read
    the baseline file. `fp` is what's compared at filter time; the other
    two fields are for diff-readability.
    """

    checker: str
    object: str
    fp: str


@dataclass(frozen=True)
class Baseline:
    version: int
    generated_at: str
    fingerprints: tuple[BaselineEntry, ...]

    def fingerprint_set(self) -> frozenset[str]:
        return frozenset(e.fp for e in self.fingerprints)


@dataclass(frozen=True)
class FilterResult:
    """Result of applying a baseline to a stream of issues."""

    kept: list[Issue]
    suppressed_count: int
    matched_fps: frozenset[str]


def fingerprint_for(checker: str, object_name: str) -> str:
    """Stable fingerprint for a (checker, object_name) pair.

    Format: ``sha256("pgsleuth/baseline/<algo>" + NUL + checker + NUL + object_name)``.
    The leading algorithm tag (`_FINGERPRINT_ALGORITHM`) lets us rotate the
    hashing scheme later — bumping the tag changes every output, gracefully
    invalidating any baseline that referenced old fingerprints.
    """
    h = hashlib.sha256()
    h.update(f"pgsleuth/baseline/{_FINGERPRINT_ALGORITHM}".encode("utf-8"))
    h.update(b"\x00")
    h.update(checker.encode("utf-8"))
    h.update(b"\x00")
    h.update(object_name.encode("utf-8"))
    return h.hexdigest()


def fingerprint(issue: Issue) -> str:
    return fingerprint_for(issue.checker, issue.object_name)


def from_issues(issues: Iterable[Issue], *, now: str | None = None) -> Baseline:
    """Build a Baseline from a fresh check run.

    `now` defaults to the current UTC time as ISO-8601 with seconds precision
    and a trailing 'Z'. Tests pass a fixed value for determinism.

    Duplicate findings (same checker + object_name) are deduplicated to a
    single entry — first occurrence wins. A checker that mistakenly reports
    the same finding twice would otherwise pollute the baseline file and
    inflate the "Suppressed N" count on subsequent runs.
    """
    timestamp = now if now is not None else _utc_iso_seconds()
    by_fp: dict[str, BaselineEntry] = {}
    for issue in issues:
        fp = fingerprint(issue)
        if fp not in by_fp:
            by_fp[fp] = BaselineEntry(
                checker=issue.checker,
                object=issue.object_name,
                fp=fp,
            )
    entries = tuple(sorted(by_fp.values(), key=lambda e: (e.checker, e.object)))
    return Baseline(
        version=BASELINE_VERSION,
        generated_at=timestamp,
        fingerprints=entries,
    )


def load(path: Path) -> Baseline:
    """Read and parse a baseline file. Raises BaselineError on any problem."""
    try:
        raw = path.read_text(encoding="utf-8")
    except OSError as exc:
        raise BaselineError(f"could not read baseline file {path}: {exc}") from exc

    try:
        data = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise BaselineError(f"baseline file {path} is not valid JSON: {exc}") from exc

    if not isinstance(data, dict):
        raise BaselineError(f"baseline file {path} must contain a JSON object")

    version = data.get("version")
    if version != BASELINE_VERSION:
        raise BaselineError(
            f"baseline file {path} has version {version!r}; expected {BASELINE_VERSION}"
        )

    generated_at = data.get("generated_at")
    if not isinstance(generated_at, str):
        raise BaselineError(f"baseline file {path} is missing 'generated_at'")

    raw_fps = data.get("fingerprints")
    if not isinstance(raw_fps, list):
        raise BaselineError(f"baseline file {path} is missing 'fingerprints' list")

    entries: list[BaselineEntry] = []
    for i, item in enumerate(raw_fps):
        if not isinstance(item, dict):
            raise BaselineError(f"baseline file {path}: entry {i} is not an object")
        try:
            entries.append(
                BaselineEntry(
                    checker=str(item["checker"]),
                    object=str(item["object"]),
                    fp=str(item["fp"]),
                )
            )
        except KeyError as exc:
            raise BaselineError(f"baseline file {path}: entry {i} is missing key {exc}") from exc

    return Baseline(
        version=version,
        generated_at=generated_at,
        fingerprints=tuple(entries),
    )


def dump(baseline: Baseline, path: Path) -> None:
    """Atomically write a baseline to `path`.

    Writes to `<path>.tmp` first then `os.replace` — so a failed write or
    crash leaves the existing file intact. Pretty JSON, sorted entries,
    deterministic output for clean diffs in code review.
    """
    payload = {
        "version": baseline.version,
        "generated_at": baseline.generated_at,
        "fingerprints": [
            {"checker": e.checker, "object": e.object, "fp": e.fp} for e in baseline.fingerprints
        ],
    }
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(
        json.dumps(payload, indent=2, sort_keys=False) + "\n",
        encoding="utf-8",
    )
    os.replace(tmp, path)


def filter_issues(issues: Iterable[Issue], baseline: Baseline) -> FilterResult:
    """Drop any issue whose fingerprint is in the baseline.

    Returns the kept issues, the count of suppressed ones, and the set of
    fingerprints that actually matched (used by `prune` to detect stale
    entries).
    """
    baselined = baseline.fingerprint_set()
    kept: list[Issue] = []
    matched: set[str] = set()
    suppressed = 0
    for issue in issues:
        fp = fingerprint(issue)
        if fp in baselined:
            matched.add(fp)
            suppressed += 1
        else:
            kept.append(issue)
    return FilterResult(
        kept=kept,
        suppressed_count=suppressed,
        matched_fps=frozenset(matched),
    )


def stale_entries(baseline: Baseline, matched_fps: frozenset[str]) -> list[BaselineEntry]:
    """Entries in the baseline that didn't reproduce in the current run."""
    return [e for e in baseline.fingerprints if e.fp not in matched_fps]


def unknown_checker_entries(baseline: Baseline, known: frozenset[str]) -> list[BaselineEntry]:
    """Entries whose checker is not registered in the running pgsleuth.

    Pruning these silently is risky — a checker can appear missing because
    it was disabled, version-gated, or simply hasn't been imported yet. The
    CLI surfaces these as a stderr warning by default; users opt into
    auto-removal explicitly.
    """
    return [e for e in baseline.fingerprints if e.checker not in known]


def prune(
    baseline: Baseline,
    matched_fps: frozenset[str],
    *,
    known_checkers: frozenset[str] | None = None,
    now: str | None = None,
) -> Baseline:
    """Return a new Baseline with stale entries removed.

    "Stale" means a fingerprint did not reproduce in the current run.
    Stale entries for **known** checkers are removed. Stale entries for
    **unknown** checkers are kept, because pgsleuth's policy is to warn
    before silently removing — see CLI's ``--ignore-unknown-checkers``.

    The ``known_checkers`` argument tells us which checkers count as "known":

    - ``None`` (default): no registry info available. Every checker looks
      "unknown" and *no* stale entries are dropped — the safe shape for
      tests and Python-level callers without a populated registry.
    - ``frozenset({...})``: typically ``frozenset(registry.names())``.
      Entries whose checker is in this set are evaluated for staleness;
      entries whose checker is *not* in this set are preserved as
      "unknown" (warn-before-remove policy).
    """
    known = known_checkers if known_checkers is not None else None
    new_entries = tuple(
        e
        for e in baseline.fingerprints
        if e.fp in matched_fps  # matched in this run → keep
        or known is None  # no registry info → keep everything stale-looking
        or e.checker not in known  # unknown checker → keep (warn elsewhere)
    )
    return Baseline(
        version=baseline.version,
        generated_at=now if now is not None else _utc_iso_seconds(),
        fingerprints=new_entries,
    )


def _utc_iso_seconds() -> str:
    """ISO-8601 UTC with seconds precision and a trailing 'Z'."""
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")
