"""JSON reporter — stable shape for CI consumption."""

from __future__ import annotations

import json
import sys
from typing import Iterable, TextIO

from pgsleuth.checkers.base import Issue
from pgsleuth.engine import SkippedChecker


def render(
    issues: Iterable[Issue],
    *,
    stream: TextIO | None = None,
    suppressed: int = 0,
    skipped: Iterable[SkippedChecker] = (),
) -> None:
    """Emit a stable JSON payload for CI consumers.

    `skipped` carries any checkers that the engine did not run to completion
    — version-gated or aborted by `statement_timeout`. Surfacing it in the
    structured output prevents a silent "clean run" on a database where
    half the checks never executed; CI can fail (or escalate) based on the
    list rather than scraping stderr.
    """
    stream = stream or sys.stdout
    payload = {
        "issues": [issue.to_dict() for issue in issues],
        "suppressed": suppressed,
        "skipped": [
            {"checker": s.checker, "reason": s.reason, "detail": s.detail} for s in skipped
        ],
    }
    json.dump(payload, stream, indent=2, sort_keys=True)
    stream.write("\n")
