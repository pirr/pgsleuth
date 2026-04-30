"""Core types: Issue, Severity, Checker, and the global registry."""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import asdict, dataclass, field
from enum import Enum
from typing import TYPE_CHECKING, ClassVar, Iterable

from pgsleuth.db.catalog import iter_objects
from pgsleuth.db.connection import rule_docs_url

if TYPE_CHECKING:
    from pgsleuth.context import CheckerContext


class Severity(str, Enum):
    ERROR = "error"
    WARNING = "warning"
    INFO = "info"

    @property
    def rank(self) -> int:
        return {"info": 0, "warning": 1, "error": 2}[self.value]


@dataclass(frozen=True)
class Issue:
    checker: str
    severity: Severity
    object_type: str  # "table" | "column" | "index" | "constraint" | "sequence"
    object_name: str  # fully qualified, e.g. "public.users.email"
    message: str
    suggestion: str | None = None
    docs_url: str | None = None
    extra: dict[str, str] = field(default_factory=dict)

    def to_dict(self) -> dict:
        d = asdict(self)
        d["severity"] = self.severity.value
        return d


class Checker(ABC):
    name: ClassVar[str]
    description: ClassVar[str]
    default_severity: ClassVar[Severity] = Severity.WARNING
    # Version gates are inclusive on min, exclusive on max. None means
    # "applies to every version we globally support". A checker that needs
    # different SQL on different versions can leave these as None and branch
    # on `ctx.server_version` inside `run()`.
    min_version: ClassVar[int | None] = None
    max_version: ClassVar[int | None] = None

    @abstractmethod
    def run(self, ctx: "CheckerContext") -> Iterable[Issue]:
        """Yield issues. Implementations should be idempotent and side-effect-free."""

    @classmethod
    def supports(cls, server_version: int) -> bool:
        if cls.min_version is not None and server_version < cls.min_version:
            return False
        if cls.max_version is not None and server_version >= cls.max_version:
            return False
        return True

    def issue(
        self,
        ctx: "CheckerContext",
        *,
        object_type: str,
        object_name: str,
        message: str,
        suggestion: str | None = None,
        extra: dict[str, str] | None = None,
    ) -> Issue:
        """Build an Issue with this checker's identity prefilled.

        `checker`, `severity`, and `docs_url` are derived from the class —
        passing them explicitly at every yield site is boilerplate and a
        silent-bug surface (typo or stale copy-paste produces a finding
        whose docs_url points at the wrong rule, or whose severity ignores
        the per-checker config override).
        """
        return Issue(
            checker=self.name,
            severity=ctx.config.severity_for(self.name, self.default_severity),
            object_type=object_type,
            object_name=object_name,
            message=message,
            suggestion=suggestion,
            docs_url=rule_docs_url(self.name),
            extra=extra or {},
        )


class RowChecker(Checker):
    """Checker for the common 1-row-to-0-or-1-Issue pattern.

    Subclass and set `sql` (a template containing `{schema_filter}`) plus
    override `check_row(ctx, row)` to return an Issue or None. The default
    `run()` walks `iter_objects(ctx, sql, ...)` and emits whatever
    `check_row` returns.

    Checkers whose shape is different — multiple queries, N+1, no SQL at
    all — should subclass `Checker` directly and write their own `run()`.
    """

    sql: ClassVar[str]
    schema_alias: ClassVar[str] = "n"
    schema_key: ClassVar[str] = "schema"
    table_key: ClassVar[str] = "table"

    def run(self, ctx: "CheckerContext") -> Iterable[Issue]:
        for row in iter_objects(
            ctx,
            self.sql,
            schema_alias=self.schema_alias,
            schema_key=self.schema_key,
            table_key=self.table_key,
        ):
            issue = self.check_row(ctx, row)
            if issue is not None:
                yield issue

    @abstractmethod
    def check_row(self, ctx: "CheckerContext", row: dict) -> Issue | None:
        """Map one catalog row to an Issue, or return None to skip it."""


class _Registry:
    def __init__(self) -> None:
        self._checkers: dict[str, type[Checker]] = {}

    def register(self, cls: type[Checker]) -> type[Checker]:
        if not getattr(cls, "name", None):
            raise ValueError(f"{cls.__qualname__} is missing a `name` attribute")
        if cls.name in self._checkers:
            raise ValueError(f"checker {cls.name!r} is already registered")
        self._checkers[cls.name] = cls
        return cls

    def all(self) -> list[type[Checker]]:
        return list(self._checkers.values())

    def get(self, name: str) -> type[Checker]:
        try:
            return self._checkers[name]
        except KeyError as exc:
            raise KeyError(f"unknown checker: {name!r}") from exc

    def names(self) -> list[str]:
        return sorted(self._checkers)


registry = _Registry()


def register(cls: type[Checker]) -> type[Checker]:
    return registry.register(cls)
