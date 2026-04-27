"""Checker package. Importing this module loads all built-in checkers and
registers them with the global registry."""

from pgsleuth.checkers import (  # noqa: F401
    fk_type_mismatch,
    missing_fk_index,
    missing_primary_key,
    not_valid_constraints,
    primary_key_type,
    redundant_index,
    sequence_drift,
    three_state_boolean,
)
