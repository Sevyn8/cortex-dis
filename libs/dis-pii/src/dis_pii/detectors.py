"""PII detection over a caller-supplied source mapping.

Detection is **field-name and pattern based** (root CLAUDE.md hard rule 2 names the
PII set: phone, email, loyalty_id, PAN, Aadhaar, and tenant-policy fields; D24). It
inspects the column names the mapping references and matches them against a PII
name set.

There is **no explicit per-column PII flag** in the live ``config.source_mappings``
schema or in the ``mapping_rules`` shape (introspected Slice 4), so there is no flag
to read and no "honour an explicit list" path — detection is purely heuristic. The
consequence is a **false-negative risk**: a PII column whose name the matcher does
not recognise is not detected, so the gate does not fire on it. This limit is
recorded in ``decisions.md`` D40; do not read it as a guarantee that all PII is caught.
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Mapping
from typing import Any

# Substring patterns — long enough that a substring match is safe (no false hits on
# unrelated columns). Applied to the normalised (lower-cased) column name.
_PII_NAME_PATTERN = re.compile(r"email|phone|mobile|msisdn|loyalty|aadhaa?r")

# Short, ambiguous identifiers matched only as a whole token (so "pan" matches
# "customer_pan" but never "company"/"japan").
_PII_EXACT_TOKENS = frozenset({"pan", "ssn", "aadhaar", "aadhar"})

_TOKEN_SPLIT = re.compile(r"[^a-z0-9]+")


def is_pii_column(name: str) -> bool:
    """True if a column name looks like PII under the heuristic name/pattern rules."""
    normalised = name.strip().lower()
    if not normalised:
        return False
    if _PII_NAME_PATTERN.search(normalised):
        return True
    tokens = {t for t in _TOKEN_SPLIT.split(normalised) if t}
    return bool(tokens & _PII_EXACT_TOKENS)


def _iter_mapping_columns(mapping: Mapping[str, Any]) -> Iterable[str]:
    """Yield every column name the mapping references.

    Reads ``mapping_rules`` (the live shape: ``rename`` / ``normalize`` / ``cast`` /
    ``derive``); both the source names (keys) and canonical targets (values of
    ``rename``) are considered, since PII can be named on either side. Unknown shapes
    are tolerated — a mapping without these keys simply yields nothing.
    """
    rules = mapping.get("mapping_rules", mapping)
    if not isinstance(rules, Mapping):
        return

    rename = rules.get("rename")
    if isinstance(rename, Mapping):
        for source_name, canonical_name in rename.items():
            yield str(source_name)
            yield str(canonical_name)

    for section in ("normalize", "cast", "derive"):
        block = rules.get(section)
        if isinstance(block, Mapping):
            for column in block:
                yield str(column)


def detect_pii_columns(mapping: Mapping[str, Any]) -> frozenset[str]:
    """Return the set of column names the given source mapping flags as PII.

    Pure function: no DB access, no crypto, no network. ``mapping`` is the
    caller-supplied source mapping (e.g. a ``config.source_mappings`` row as a dict,
    or its ``mapping_rules`` object).
    """
    return frozenset(c for c in _iter_mapping_columns(mapping) if is_pii_column(c))
