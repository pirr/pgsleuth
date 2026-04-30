"""Verify pgsleuth.checkers auto-discovery wires every checker module.

These tests run without a database — they only inspect the import graph
and the in-memory registry.
"""

from __future__ import annotations

import pkgutil

import pgsleuth.checkers  # noqa: F401 — triggers auto-discovery
from pgsleuth.checkers.base import registry


def _checker_module_names() -> set[str]:
    """Module names that should each register at least one Checker."""
    return {
        f"pgsleuth.checkers.{info.name}"
        for info in pkgutil.iter_modules(pgsleuth.checkers.__path__)
        if info.name != "base" and not info.name.startswith("_")
    }


def test_registry_is_populated():
    """Sanity: importing pgsleuth.checkers yields a non-empty registry."""
    assert len(registry.names()) > 0


def test_every_checker_module_registers_at_least_one_checker():
    """Each non-framework module under pgsleuth.checkers must call
    register(MyChecker) on import. If a module is silently not registering,
    the rule is invisible to the CLI even though the file exists."""
    expected = _checker_module_names()
    registered = {cls.__module__ for cls in registry.all()}

    missing = expected - registered
    assert not missing, (
        f"these checker modules did not register a Checker — "
        f"forgot `register(MyChecker)` at the bottom? {sorted(missing)}"
    )
