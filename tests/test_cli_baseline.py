"""Tests for --baseline / --no-baseline on the check command."""

from __future__ import annotations

import json
from contextlib import contextmanager
from pathlib import Path
from unittest.mock import patch

from click.testing import CliRunner

from pgsleuth.baseline import BASELINE_VERSION, fingerprint_for
from pgsleuth.checkers.base import Issue, Severity
from pgsleuth.cli import main


@contextmanager
def _fake_connect(_dsn: str):
    yield object()


def _issue(checker: str, obj: str) -> Issue:
    return Issue(
        checker=checker,
        severity=Severity.WARNING,
        object_type="table",
        object_name=obj,
        message=f"finding on {obj}",
        suggestion=f"fix {obj}",
    )


def _write_baseline(path: Path, entries: list[tuple[str, str]]) -> None:
    """Write a v1 baseline file from (checker, object_name) pairs."""
    payload = {
        "version": BASELINE_VERSION,
        "generated_at": "2026-01-01T00:00:00Z",
        "fingerprints": [
            {"checker": c, "object": o, "fp": fingerprint_for(c, o)} for c, o in entries
        ],
    }
    path.write_text(json.dumps(payload, indent=2) + "\n")


def test_check_with_baseline_suppresses_known() -> None:
    runner = CliRunner()
    with runner.isolated_filesystem():
        baseline = Path("baseline.json")
        _write_baseline(
            baseline,
            [
                ("missing_fk_index", "public.orders(user_id)"),
                ("missing_fk_index", "public.invoices(customer_id)"),
            ],
        )
        with (
            patch("pgsleuth.cli.connect", _fake_connect),
            patch("pgsleuth.cli.server_version_num", return_value=150004),
            patch(
                "pgsleuth.cli._run_all",
                return_value=[
                    _issue("missing_fk_index", "public.orders(user_id)"),
                    _issue("missing_fk_index", "public.invoices(customer_id)"),
                ],
            ),
        ):
            result = runner.invoke(
                main,
                ["check", "--dsn", "postgresql://x/y", "--baseline", str(baseline)],
            )

    assert result.exit_code == 0, result.output
    assert "Suppressed 2 findings via baseline" in result.output
    # the suppressed findings' objects should not be printed as current issues
    after_suppress = result.output.split("Suppressed")[1]
    assert "public.orders(user_id)" not in after_suppress
    assert "public.invoices(customer_id)" not in after_suppress


def test_check_with_baseline_reports_new_findings() -> None:
    runner = CliRunner()
    with runner.isolated_filesystem():
        baseline = Path("baseline.json")
        _write_baseline(baseline, [("missing_fk_index", "public.orders(user_id)")])
        with (
            patch("pgsleuth.cli.connect", _fake_connect),
            patch("pgsleuth.cli.server_version_num", return_value=150004),
            patch(
                "pgsleuth.cli._run_all",
                return_value=[
                    _issue("missing_fk_index", "public.orders(user_id)"),  # baselined
                    _issue("missing_fk_index", "public.audit_log(user_id)"),  # NEW
                ],
            ),
        ):
            result = runner.invoke(
                main,
                ["check", "--dsn", "postgresql://x/y", "--baseline", str(baseline)],
            )

    assert result.exit_code == 1, result.output
    assert "Suppressed 1 finding" in result.output
    assert "public.audit_log(user_id)" in result.output


def test_check_autodiscovers_default_baseline_in_cwd() -> None:
    runner = CliRunner()
    with runner.isolated_filesystem():
        # Drop the default-named file in cwd
        _write_baseline(
            Path("pgsleuth.baseline.json"),
            [("missing_fk_index", "public.orders(user_id)")],
        )
        with (
            patch("pgsleuth.cli.connect", _fake_connect),
            patch("pgsleuth.cli.server_version_num", return_value=150004),
            patch(
                "pgsleuth.cli._run_all",
                return_value=[_issue("missing_fk_index", "public.orders(user_id)")],
            ),
        ):
            result = runner.invoke(main, ["check", "--dsn", "postgresql://x/y"])

    assert result.exit_code == 0, result.output
    assert "auto-discovered" in result.output
    assert "Suppressed 1 finding" in result.output


def test_check_no_baseline_disables_autodiscovery() -> None:
    runner = CliRunner()
    with runner.isolated_filesystem():
        _write_baseline(
            Path("pgsleuth.baseline.json"),
            [("missing_fk_index", "public.orders(user_id)")],
        )
        with (
            patch("pgsleuth.cli.connect", _fake_connect),
            patch("pgsleuth.cli.server_version_num", return_value=150004),
            patch(
                "pgsleuth.cli._run_all",
                return_value=[_issue("missing_fk_index", "public.orders(user_id)")],
            ),
        ):
            result = runner.invoke(main, ["check", "--dsn", "postgresql://x/y", "--no-baseline"])

    assert result.exit_code == 1, result.output
    assert "auto-discovered" not in result.output
    assert "Suppressed" not in result.output
    assert "public.orders(user_id)" in result.output


def test_check_no_baseline_with_no_file_runs_normally() -> None:
    """No baseline file in cwd, no flags — runs as if the feature didn't exist."""
    runner = CliRunner()
    with runner.isolated_filesystem():
        with (
            patch("pgsleuth.cli.connect", _fake_connect),
            patch("pgsleuth.cli.server_version_num", return_value=150004),
            patch(
                "pgsleuth.cli._run_all",
                return_value=[_issue("missing_fk_index", "public.orders(user_id)")],
            ),
        ):
            result = runner.invoke(main, ["check", "--dsn", "postgresql://x/y"])

    assert result.exit_code == 1
    assert "auto-discovered" not in result.output
    assert "Suppressed" not in result.output


def test_check_baseline_corrupt_json_exits_2() -> None:
    runner = CliRunner()
    with runner.isolated_filesystem():
        baseline = Path("baseline.json")
        baseline.write_text("{ not valid json")
        with (
            patch("pgsleuth.cli.connect", _fake_connect),
            patch("pgsleuth.cli.server_version_num", return_value=150004),
        ):
            result = runner.invoke(
                main,
                ["check", "--dsn", "postgresql://x/y", "--baseline", str(baseline)],
            )

    assert result.exit_code == 2
    assert "not valid JSON" in result.output


def test_check_baseline_path_does_not_exist_exits_2() -> None:
    runner = CliRunner()
    with runner.isolated_filesystem():
        with (
            patch("pgsleuth.cli.connect", _fake_connect),
            patch("pgsleuth.cli.server_version_num", return_value=150004),
        ):
            result = runner.invoke(
                main,
                ["check", "--dsn", "postgresql://x/y", "--baseline", "missing.json"],
            )

    assert result.exit_code == 2
    assert "could not read" in result.output


def test_check_explicit_and_no_baseline_conflict() -> None:
    runner = CliRunner()
    with runner.isolated_filesystem():
        baseline = Path("baseline.json")
        _write_baseline(baseline, [])
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
                    "--baseline",
                    str(baseline),
                    "--no-baseline",
                ],
            )

    assert result.exit_code == 2
    assert "--baseline" in result.output
    assert "--no-baseline" in result.output


def test_check_baseline_warns_on_stale_entries() -> None:
    """Baseline lists a fingerprint that doesn't reproduce in the run."""
    runner = CliRunner()
    with runner.isolated_filesystem():
        baseline = Path("baseline.json")
        _write_baseline(baseline, [("missing_fk_index", "public.fixed_long_ago")])
        with (
            patch("pgsleuth.cli.connect", _fake_connect),
            patch("pgsleuth.cli.server_version_num", return_value=150004),
            patch("pgsleuth.cli._run_all", return_value=[]),  # nothing reproduces
        ):
            result = runner.invoke(
                main,
                ["check", "--dsn", "postgresql://x/y", "--baseline", str(baseline)],
            )

    # No findings overall (suppressed and stale don't fail CI).
    assert result.exit_code == 0
    assert "1 baseline entry did not reproduce" in result.output
    assert "pgsleuth baseline prune" in result.output


# ---------- baseline write subcommand ----------


def test_baseline_write_creates_file_with_default_path() -> None:
    runner = CliRunner()
    with runner.isolated_filesystem():
        with (
            patch("pgsleuth.cli.connect", _fake_connect),
            patch("pgsleuth.cli.server_version_num", return_value=150004),
            patch(
                "pgsleuth.cli._run_all",
                return_value=[
                    _issue("missing_fk_index", "public.orders(user_id)"),
                    _issue("missing_primary_key", "public.events"),
                ],
            ),
        ):
            result = runner.invoke(main, ["baseline", "write", "--dsn", "postgresql://x/y"])

        assert result.exit_code == 0, result.output
        assert "Wrote 2 findings to pgsleuth.baseline.json" in result.output
        assert Path("pgsleuth.baseline.json").exists()

        payload = json.loads(Path("pgsleuth.baseline.json").read_text())
        assert payload["version"] == BASELINE_VERSION
        assert len(payload["fingerprints"]) == 2
        objects = sorted(e["object"] for e in payload["fingerprints"])
        assert objects == ["public.events", "public.orders(user_id)"]


def test_baseline_write_to_custom_output_path() -> None:
    runner = CliRunner()
    with runner.isolated_filesystem():
        with (
            patch("pgsleuth.cli.connect", _fake_connect),
            patch("pgsleuth.cli.server_version_num", return_value=150004),
            patch(
                "pgsleuth.cli._run_all",
                return_value=[_issue("missing_fk_index", "public.t")],
            ),
        ):
            result = runner.invoke(
                main,
                [
                    "baseline",
                    "write",
                    "--dsn",
                    "postgresql://x/y",
                    "--output",
                    "custom.json",
                ],
            )

        assert result.exit_code == 0, result.output
        assert Path("custom.json").exists()
        assert not Path("pgsleuth.baseline.json").exists()


def test_baseline_write_overwrites_existing_file() -> None:
    """Plan: overwrite freely. Existing file is replaced without prompting."""
    runner = CliRunner()
    with runner.isolated_filesystem():
        # Pre-existing baseline with one entry
        _write_baseline(
            Path("pgsleuth.baseline.json"),
            [("missing_primary_key", "public.legacy")],
        )

        with (
            patch("pgsleuth.cli.connect", _fake_connect),
            patch("pgsleuth.cli.server_version_num", return_value=150004),
            patch(
                "pgsleuth.cli._run_all",
                return_value=[_issue("missing_fk_index", "public.new")],
            ),
        ):
            result = runner.invoke(main, ["baseline", "write", "--dsn", "postgresql://x/y"])

        assert result.exit_code == 0, result.output
        payload = json.loads(Path("pgsleuth.baseline.json").read_text())
        objects = [e["object"] for e in payload["fingerprints"]]
        # Original entry is gone; new entry is present
        assert "public.legacy" not in objects
        assert "public.new" in objects


def test_baseline_write_captures_all_severities() -> None:
    """No --min-severity filter — info findings are included in the baseline."""
    runner = CliRunner()
    with runner.isolated_filesystem():
        info_issue = Issue(
            checker="redundant_index",
            severity=Severity.INFO,
            object_type="index",
            object_name="public.idx_a",
            message="redundant",
        )
        warning_issue = _issue("missing_fk_index", "public.t")

        with (
            patch("pgsleuth.cli.connect", _fake_connect),
            patch("pgsleuth.cli.server_version_num", return_value=150004),
            patch("pgsleuth.cli._run_all", return_value=[info_issue, warning_issue]),
        ):
            result = runner.invoke(main, ["baseline", "write", "--dsn", "postgresql://x/y"])

        assert result.exit_code == 0, result.output
        payload = json.loads(Path("pgsleuth.baseline.json").read_text())
        objects = sorted(e["object"] for e in payload["fingerprints"])
        assert objects == ["public.idx_a", "public.t"]


def test_baseline_write_empty_when_no_findings() -> None:
    runner = CliRunner()
    with runner.isolated_filesystem():
        with (
            patch("pgsleuth.cli.connect", _fake_connect),
            patch("pgsleuth.cli.server_version_num", return_value=150004),
            patch("pgsleuth.cli._run_all", return_value=[]),
        ):
            result = runner.invoke(main, ["baseline", "write", "--dsn", "postgresql://x/y"])

        assert result.exit_code == 0, result.output
        assert "Wrote 0 findings" in result.output
        payload = json.loads(Path("pgsleuth.baseline.json").read_text())
        assert payload["fingerprints"] == []


def test_baseline_write_uses_pgsleuth_dsn_envvar() -> None:
    runner = CliRunner()
    with runner.isolated_filesystem():
        with (
            patch("pgsleuth.cli.connect", _fake_connect),
            patch("pgsleuth.cli.server_version_num", return_value=150004),
            patch(
                "pgsleuth.cli._run_all",
                return_value=[_issue("missing_fk_index", "public.t")],
            ),
        ):
            result = runner.invoke(
                main,
                ["baseline", "write"],
                env={"PGSLEUTH_DSN": "postgresql://envvar/db"},
            )

        assert result.exit_code == 0, result.output
        assert Path("pgsleuth.baseline.json").exists()


# ---------- check command, JSON output ----------


def test_check_baseline_json_output_includes_suppressed() -> None:
    runner = CliRunner()
    with runner.isolated_filesystem():
        baseline = Path("baseline.json")
        _write_baseline(baseline, [("missing_fk_index", "public.orders(user_id)")])
        with (
            patch("pgsleuth.cli.connect", _fake_connect),
            patch("pgsleuth.cli.server_version_num", return_value=150004),
            patch(
                "pgsleuth.cli._run_all",
                return_value=[
                    _issue("missing_fk_index", "public.orders(user_id)"),
                    _issue("missing_fk_index", "public.audit_log(user_id)"),
                ],
            ),
        ):
            result = runner.invoke(
                main,
                [
                    "check",
                    "--dsn",
                    "postgresql://x/y",
                    "--baseline",
                    str(baseline),
                    "--format",
                    "json",
                ],
            )

    assert result.exit_code == 1
    # JSON output is on stdout; stderr lines (auto-discover notice, etc.) are mixed
    # in via CliRunner's default. Extract the JSON object by finding the first '{'.
    start = result.output.index("{")
    payload = json.loads(result.output[start:])
    assert payload["suppressed"] == 1
    assert len(payload["issues"]) == 1
    assert payload["issues"][0]["object_name"] == "public.audit_log(user_id)"
