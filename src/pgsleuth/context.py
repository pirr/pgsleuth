"""CheckerContext — the value handed to every checker.

Today it holds a psycopg connection and runtime config. ORM-aware checkers
will consume an extended context; existing DB-only checkers remain unchanged.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from psycopg import Connection

    from pgsleuth.config import Config


@dataclass
class CheckerContext:
    conn: "Connection"
    config: "Config"
    server_version: int  # e.g. 150004 for PG 15.4
