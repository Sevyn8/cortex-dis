"""Inert placeholder seam for the PII tokenizer.

Marks where deterministic per-tenant HMAC tokenization lands later (D24). Import-safe:
construction does no I/O and contacts no KMS; the method raises. Mirrors the Slice 3
``BqClient`` stub discipline. No real crypto exists in v1.0.

*Deferred. Trigger: the first receiver that carries PII (a non-CSV receiver, or a CSV
source mapping that flags a PII column).*
"""

from __future__ import annotations


class HmacTokenizer:
    """Deterministic per-tenant HMAC tokenizer seam. v1.0 stub — performs no I/O."""

    def __init__(self, *, key_version: int | None = None) -> None:
        # Stores configuration only; opens no key vault and contacts no KMS.
        self.key_version = key_version

    def tokenize(self, value: str, *, tenant_id: str) -> str:
        raise NotImplementedError(
            "HmacTokenizer is a Slice 4 placeholder seam; real tokenization is deferred "
            "to the first PII-carrying receiver (see decisions.md D24/D40)"
        )
