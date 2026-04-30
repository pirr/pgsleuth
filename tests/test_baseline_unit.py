"""Pure-function tests for the baseline module. No DB required."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from pgsleuth.baseline import (
    BASELINE_VERSION,
    Baseline,
    BaselineError,
    FilterResult,
    dump,
    filter_issues,
    fingerprint,
    fingerprint_for,
    from_issues,
    load,
    prune,
    stale_entries,
    unknown_checker_entries,
)
from pgsleuth.checkers.base import Issue, Severity


def _issue(checker: str, obj: str, message: str = "") -> Issue:
    return Issue(
        checker=checker,
        severity=Severity.WARNING,
        object_type="table",
        object_name=obj,
        message=message,
    )


def test_fingerprint_stable_across_messages() -> None:
    a = _issue("missing_fk_index", "public.orders(user_id)", message="one wording")
    b = _issue("missing_fk_index", "public.orders(user_id)", message="completely different")
    assert fingerprint(a) == fingerprint(b)


def test_fingerprint_changes_with_object_name() -> None:
    a = _issue("missing_fk_index", "public.orders(user_id)")
    b = _issue("missing_fk_index", "public.orders(account_id)")
    assert fingerprint(a) != fingerprint(b)


def test_fingerprint_changes_with_checker() -> None:
    a = _issue("missing_fk_index", "public.orders(user_id)")
    b = _issue("redundant_index", "public.orders(user_id)")
    assert fingerprint(a) != fingerprint(b)


def test_fingerprint_for_matches_fingerprint() -> None:
    issue = _issue("missing_fk_index", "public.orders(user_id)")
    assert fingerprint(issue) == fingerprint_for("missing_fk_index", "public.orders(user_id)")


def test_from_issues_dedupes_duplicate_findings() -> None:
    """Same (checker, object_name) reported twice → one baseline entry, not two.

    A buggy checker SQL could yield the same finding twice. Without dedup
    we'd write both entries; subsequent runs would show inflated "Suppressed
    N" counts and confusing diffs in code review.
    """
    a = _issue("missing_fk_index", "public.orders(user_id)", message="first wording")
    b = _issue("missing_fk_index", "public.orders(user_id)", message="second wording")

    baseline = from_issues([a, b], now="2026-01-01T00:00:00Z")

    assert len(baseline.fingerprints) == 1
    assert baseline.fingerprints[0].object == "public.orders(user_id)"


def test_from_issues_sorts_by_checker_then_object() -> None:
    issues = [
        _issue("redundant_index", "public.b"),
        _issue("missing_fk_index", "public.z"),
        _issue("missing_fk_index", "public.a"),
    ]
    baseline = from_issues(issues, now="2026-01-01T00:00:00Z")
    assert [(e.checker, e.object) for e in baseline.fingerprints] == [
        ("missing_fk_index", "public.a"),
        ("missing_fk_index", "public.z"),
        ("redundant_index", "public.b"),
    ]
    assert baseline.version == BASELINE_VERSION
    assert baseline.generated_at == "2026-01-01T00:00:00Z"


def test_unicode_object_names_round_trip(tmp_path: Path) -> None:
    """Non-ASCII identifiers in object_name survive write→load and produce
    stable fingerprints. Confidence test: Postgres allows UTF-8 identifiers,
    so a real schema could have e.g. emoji or non-Latin script in a name.
    """
    issue = _issue("missing_fk_index", "public.users_📊(пользователь)")

    baseline = from_issues([issue], now="2026-01-01T00:00:00Z")
    path = tmp_path / "b.json"
    dump(baseline, path)
    loaded = load(path)

    assert loaded.fingerprints[0].object == "public.users_📊(пользователь)"
    # Fingerprint must match what fingerprint() would compute fresh from
    # the same Issue — i.e. the file's `fp` field is recoverable, not
    # corrupted by encoding round-trip.
    assert loaded.fingerprints[0].fp == fingerprint(issue)


def test_dump_then_load_roundtrip(tmp_path: Path) -> None:
    issues = [
        _issue("missing_fk_index", "public.orders(user_id)"),
        _issue("redundant_index", "public.orders.idx_a"),
    ]
    baseline = from_issues(issues, now="2026-01-01T00:00:00Z")
    path = tmp_path / "pgsleuth.baseline.json"
    dump(baseline, path)
    loaded = load(path)
    assert loaded == baseline


def test_dump_writes_pretty_json_with_trailing_newline(tmp_path: Path) -> None:
    """Diff-friendly format: indent=2, ends with a newline."""
    baseline = from_issues([_issue("missing_fk_index", "public.t")], now="2026-01-01T00:00:00Z")
    path = tmp_path / "b.json"
    dump(baseline, path)
    text = path.read_text()
    assert text.endswith("\n")
    parsed = json.loads(text)
    assert parsed["version"] == BASELINE_VERSION
    assert parsed["generated_at"] == "2026-01-01T00:00:00Z"
    assert isinstance(parsed["fingerprints"], list)
    assert parsed["fingerprints"][0].keys() == {"checker", "object", "fp"}


def test_dump_is_atomic(tmp_path: Path) -> None:
    """Successful write replaces the original cleanly and removes .tmp."""
    path = tmp_path / "b.json"
    path.write_text('{"original": true}\n')

    baseline = from_issues([_issue("missing_fk_index", "public.t")], now="2026-01-01T00:00:00Z")
    dump(baseline, path)

    parsed = json.loads(path.read_text())
    assert "original" not in parsed
    assert parsed["version"] == BASELINE_VERSION
    assert not (tmp_path / "b.json.tmp").exists()


def test_dump_preserves_existing_file_permissions(tmp_path: Path) -> None:
    """An atomic write must not silently widen `chmod` on the baseline file.

    A team that has explicitly set 0o600 on pgsleuth.baseline.json (e.g.
    because the file lives next to other sensitive config) shouldn't see
    it widened to default umask just because pgsleuth re-wrote it.
    """
    path = tmp_path / "b.json"
    path.write_text(
        json.dumps({"version": BASELINE_VERSION, "generated_at": "x", "fingerprints": []})
    )
    path.chmod(0o600)

    baseline = from_issues([_issue("missing_fk_index", "public.t")], now="2026-01-01T00:00:00Z")
    dump(baseline, path)

    new_mode = path.stat().st_mode & 0o777
    assert new_mode == 0o600


def test_dump_uses_default_permissions_when_no_existing_file(tmp_path: Path) -> None:
    """First-time write doesn't try to copy permissions from a nonexistent file."""
    path = tmp_path / "fresh.json"
    baseline = from_issues([_issue("missing_fk_index", "public.t")], now="2026-01-01T00:00:00Z")
    dump(baseline, path)
    # File exists and is readable. Don't assert exact mode — depends on umask.
    assert path.exists()
    assert path.stat().st_size > 0


def test_dump_failure_preserves_original(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """If os.replace fails after the .tmp is written, the original survives."""
    path = tmp_path / "b.json"
    original_payload = '{"original": true}\n'
    path.write_text(original_payload)

    baseline = from_issues([_issue("missing_fk_index", "public.t")], now="2026-01-01T00:00:00Z")

    def boom(*_args, **_kwargs):
        raise OSError("simulated crash mid-replace")

    monkeypatch.setattr("pgsleuth.baseline.os.replace", boom)

    with pytest.raises(OSError, match="simulated crash"):
        dump(baseline, path)

    # The original file is exactly what it was before the failed dump.
    assert path.read_text() == original_payload


def test_load_rejects_missing_file(tmp_path: Path) -> None:
    with pytest.raises(BaselineError, match="could not read"):
        load(tmp_path / "does-not-exist.json")


def test_load_rejects_corrupt_json(tmp_path: Path) -> None:
    path = tmp_path / "b.json"
    path.write_text("{ not valid json")
    with pytest.raises(BaselineError, match="not valid JSON"):
        load(path)


def test_load_rejects_wrong_version(tmp_path: Path) -> None:
    path = tmp_path / "b.json"
    path.write_text(
        json.dumps({"version": 99, "generated_at": "2026-01-01T00:00:00Z", "fingerprints": []})
    )
    with pytest.raises(BaselineError, match="version 99"):
        load(path)


def test_load_rejects_missing_keys(tmp_path: Path) -> None:
    path = tmp_path / "b.json"
    path.write_text(
        json.dumps(
            {
                "version": BASELINE_VERSION,
                "generated_at": "2026-01-01T00:00:00Z",
                "fingerprints": [{"checker": "x", "object": "y"}],  # no "fp"
            }
        )
    )
    with pytest.raises(BaselineError, match="missing key"):
        load(path)


def test_filter_issues_drops_baselined_keeps_new() -> None:
    baselined = _issue("missing_fk_index", "public.orders(user_id)")
    new = _issue("missing_fk_index", "public.invoices(customer_id)")
    baseline = from_issues([baselined], now="2026-01-01T00:00:00Z")

    result = filter_issues([baselined, new], baseline)

    assert isinstance(result, FilterResult)
    assert result.suppressed_count == 1
    assert [i.object_name for i in result.kept] == [new.object_name]
    assert fingerprint(baselined) in result.matched_fps


def test_filter_issues_handles_empty_baseline() -> None:
    issues = [_issue("missing_fk_index", "public.t")]
    empty = Baseline(version=BASELINE_VERSION, generated_at="2026-01-01T00:00:00Z", fingerprints=())
    result = filter_issues(issues, empty)
    assert result.suppressed_count == 0
    assert result.kept == issues
    assert result.matched_fps == frozenset()


def test_stale_entries_returns_unmatched() -> None:
    a = _issue("missing_fk_index", "public.a")
    b = _issue("missing_fk_index", "public.b")  # in baseline, doesn't reproduce
    baseline = from_issues([a, b], now="2026-01-01T00:00:00Z")
    matched = frozenset({fingerprint(a)})

    stale = stale_entries(baseline, matched)

    assert [e.object for e in stale] == ["public.b"]


def test_unknown_checker_entries_returns_only_unknowns() -> None:
    keep = _issue("missing_fk_index", "public.a")
    drop = _issue("removed_checker", "public.b")
    baseline = from_issues([keep, drop], now="2026-01-01T00:00:00Z")

    unknowns = unknown_checker_entries(baseline, known=frozenset({"missing_fk_index"}))

    assert [e.checker for e in unknowns] == ["removed_checker"]


def test_prune_drops_stale_known_keeps_unknown() -> None:
    matched = _issue("missing_fk_index", "public.a")
    stale_known = _issue("missing_fk_index", "public.b")  # in baseline, doesn't match this run
    stale_unknown = _issue("removed_checker", "public.c")
    baseline = from_issues([matched, stale_known, stale_unknown], now="2026-01-01T00:00:00Z")

    pruned = prune(
        baseline,
        matched_fps=frozenset({fingerprint(matched)}),
        known_checkers=frozenset({"missing_fk_index"}),
        now="2026-02-01T00:00:00Z",
    )

    objects = sorted(e.object for e in pruned.fingerprints)
    assert objects == ["public.a", "public.c"]  # matched + stale-of-unknown-checker
    assert pruned.generated_at == "2026-02-01T00:00:00Z"


def test_prune_with_no_known_set_keeps_everything_stale() -> None:
    """Without registry info, prune is a no-op on stale entries."""
    matched = _issue("missing_fk_index", "public.a")
    stale = _issue("missing_fk_index", "public.b")
    baseline = from_issues([matched, stale], now="2026-01-01T00:00:00Z")

    pruned = prune(
        baseline,
        matched_fps=frozenset({fingerprint(matched)}),
        known_checkers=None,  # no info — be conservative
        now="2026-02-01T00:00:00Z",
    )

    assert sorted(e.object for e in pruned.fingerprints) == ["public.a", "public.b"]


def test_baseline_fingerprint_set_returns_all_fps() -> None:
    issues = [
        _issue("missing_fk_index", "public.a"),
        _issue("redundant_index", "public.b"),
    ]
    baseline = from_issues(issues, now="2026-01-01T00:00:00Z")
    assert baseline.fingerprint_set() == frozenset(fingerprint(i) for i in issues)
