"""The fail-loud PII gate.

If a source mapping flags any PII column and no backend is configured to handle it,
:func:`assert_pii_handled` raises :class:`PiiBackendNotConfiguredError` *before* any
persistence path can run, so accidental PII landing fails loudly rather than silently
(root CLAUDE.md hard rule 2, code-quality rule 4).

The **only** way to reach the not-raise branch is to pass a non-``None`` ``backend``.
There is deliberately no config default or flag that disables the gate — that would be
a silent fallback. In v1.0 no real backend exists, so the gate raises on every detected
PII column; an explicitly injected placeholder backend (in tests) exercises the
not-raise branch.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any, Protocol, runtime_checkable

from dis_core.errors import PiiBackendNotConfiguredError
from dis_pii.detectors import detect_pii_columns


@runtime_checkable
class PiiBackend(Protocol):
    """Contract a real PII backend must satisfy to handle flagged columns.

    No implementation exists in v1.0 (the tokenizer / key-vault seams are inert).
    The gate only checks for *presence* of a backend; it never invokes it here.
    """

    def tokenize(self, value: str, *, tenant_id: str) -> str:
        """Return a deterministic token for ``value`` scoped to ``tenant_id``."""
        ...


def assert_pii_handled(
    mapping: Mapping[str, Any],
    *,
    backend: PiiBackend | None = None,
    tenant_id: str | None = None,
    trace_id: str | None = None,
) -> None:
    """Raise unless every PII column the mapping flags has a configured backend.

    Pure detection + a presence check; no DB, no crypto, no network. The raised error
    carries the flagged column *names* and tenant/trace context — never a raw PII value.
    """
    columns = detect_pii_columns(mapping)
    if columns and backend is None:
        ordered = tuple(sorted(columns))
        raise PiiBackendNotConfiguredError(
            f"source mapping flags {len(ordered)} PII column(s) with no configured "
            "backend; refusing to proceed (no real PII backend exists in v1.0)",
            columns=ordered,
            tenant_id=tenant_id,
            trace_id=trace_id,
        )
