"""JSON reporter — stable shape for CI consumption."""

from __future__ import annotations

import json
import sys
from typing import Iterable, TextIO

from pgsleuth.checkers.base import Issue


def render(issues: Iterable[Issue], stream: TextIO | None = None) -> None:
    stream = stream or sys.stdout
    payload = {"issues": [issue.to_dict() for issue in issues]}
    json.dump(payload, stream, indent=2, sort_keys=True)
    stream.write("\n")
