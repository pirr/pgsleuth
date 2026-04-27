"""pgsleuth — database consistency checker."""

from pgsleuth.checkers.base import Checker, Issue, Severity, registry
from pgsleuth.context import CheckerContext

__all__ = ["Checker", "CheckerContext", "Issue", "Severity", "registry"]
__version__ = "0.1.0"
