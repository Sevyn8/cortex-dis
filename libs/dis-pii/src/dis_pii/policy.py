"""Inert placeholder seam for the per-source tokenization policy and token backend.

Marks where "what to tokenize per source type" and the token/ciphertext storage
backend land later. Import-safe: construction does no I/O; methods raise. No real
storage or policy resolution exists in v1.0.

The long-term posture (one-way HMAC tokenization vs reversible encryption, and what
"configured backend" ultimately means) is **OPEN** — see ``decisions.md`` D40. This
seam does not settle it.

*Deferred. Trigger: the recoverability intent is decided, or the first PII-carrying
receiver.*
"""

from __future__ import annotations


class TokenizationPolicy:
    """Resolves which columns to tokenize for a source. v1.0 stub — performs no I/O."""

    def __init__(self) -> None:
        pass

    def columns_for_source(self, *, source_id: str) -> frozenset[str]:
        raise NotImplementedError(
            "TokenizationPolicy is a Slice 4 placeholder seam; per-source PII policy is "
            "deferred (see decisions.md D40)"
        )
