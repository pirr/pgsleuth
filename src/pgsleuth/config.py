"""Runtime config: schema/table filters and per-checker overrides."""

from __future__ import annotations

import re
import tomllib
from dataclasses import dataclass, field
from pathlib import Path

from pgsleuth.checkers.base import Severity

DEFAULT_EXCLUDED_SCHEMAS = ("pg_catalog", "information_schema", "pg_toast")


@dataclass
class CheckerOverride:
    enabled: bool = True
    severity: Severity | None = None


@dataclass
class Config:
    excluded_schemas: tuple[str, ...] = DEFAULT_EXCLUDED_SCHEMAS
    excluded_table_patterns: tuple[re.Pattern[str], ...] = ()
    enabled_checkers: frozenset[str] | None = None  # None = all
    checker_overrides: dict[str, CheckerOverride] = field(default_factory=dict)

    def is_table_excluded(self, schema: str, table: str) -> bool:
        if schema in self.excluded_schemas:
            return True
        return any(p.search(table) for p in self.excluded_table_patterns)

    def is_checker_enabled(self, name: str) -> bool:
        override = self.checker_overrides.get(name)
        if override and not override.enabled:
            return False
        if self.enabled_checkers is None:
            return True
        return name in self.enabled_checkers

    def severity_for(self, name: str, default: Severity) -> Severity:
        override = self.checker_overrides.get(name)
        if override and override.severity is not None:
            return override.severity
        return default

    @classmethod
    def from_file(cls, path: Path) -> "Config":
        data = tomllib.loads(path.read_text())
        section = data.get("pgsleuth", {})
        excluded_schemas = tuple(section.get("exclude_schemas", DEFAULT_EXCLUDED_SCHEMAS))
        excluded_table_patterns = tuple(
            re.compile(p) for p in section.get("exclude_tables", [])
        )

        overrides: dict[str, CheckerOverride] = {}
        for name, opts in section.get("checkers", {}).items():
            sev = opts.get("severity")
            overrides[name] = CheckerOverride(
                enabled=opts.get("enabled", True),
                severity=Severity(sev) if sev else None,
            )

        return cls(
            excluded_schemas=excluded_schemas,
            excluded_table_patterns=excluded_table_patterns,
            checker_overrides=overrides,
        )
