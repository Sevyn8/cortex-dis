"""The dis-pii fail-loud gate, wired over the sniffed CSV header (hard rule 2, D40).

WIRED, NOT EXTENDED: dis-pii's public surface takes a source-mapping-shaped dict
(``detectors._iter_mapping_columns`` reads ``rename``/``normalize``/``cast``/
``derive``), so the worker feeds the sniffed header names through a synthetic
``{"rename": {name: name}}`` mapping. The detector sees exactly the header names a
real mapping's rename would expose (the unit test proves the equivalence), and
dis-pii itself stays untouched.

Under the live schema no authoritative per-column PII flag exists (D40 limitation 2),
so the CSV-flag path is inert and only heuristic NAME detection can fire, with
bounded coverage / false negatives (D40 limitation 1). No tokenizer, key vault, or
flag mechanism is built here; in v1.0 no backend exists, so a detected PII column
ALWAYS raises before any persistence. The not-raise branch is reachable only via an
explicitly injected backend (tests); there is no config default that disables the
gate (dis-pii owns that invariant).
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

from dis_pii import PiiBackend, assert_pii_handled, detect_pii_columns


def synthetic_mapping(columns: Sequence[str]) -> dict[str, Any]:
    """The mapping-shaped dict that exposes CSV header names to dis-pii detection."""
    return {"rename": {name: name for name in columns}}


def gate_csv_headers(
    columns: Sequence[str],
    *,
    tenant_id: str,
    trace_id: str,
    backend: PiiBackend | None = None,
) -> frozenset[str]:
    """Pass the sniffed header through the fail-loud gate, BEFORE any persistence.

    Raises ``PiiBackendNotConfiguredError`` (carrying column NAMES only, never
    values) when the heuristic detects PII and no backend is configured — which in
    v1.0 is always, since no real backend exists (D40). Returns the detected column
    names (empty when none) for the audit event's metadata on the pass path.
    """
    mapping = synthetic_mapping(columns)
    assert_pii_handled(mapping, backend=backend, tenant_id=tenant_id, trace_id=trace_id)
    return detect_pii_columns(mapping)
