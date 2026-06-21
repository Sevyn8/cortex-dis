"""Inert placeholder seam for per-tenant key handling.

Marks where per-tenant key lookup + rotation lands later (D24: right-to-erasure is a
key-vault delete). Import-safe: construction does no I/O and contacts no KMS; the
method raises. No real key management exists in v1.0.

*Deferred. Trigger: the first slice that provisions cloud infrastructure (cloud KMS).*
"""

from __future__ import annotations


class KeyVault:
    """Per-tenant key lookup/rotation seam. v1.0 stub — performs no I/O."""

    def __init__(self) -> None:
        # Opens no KMS connection and reads no key material.
        pass

    def get_key(self, *, tenant_id: str) -> bytes:
        raise NotImplementedError(
            "KeyVault is a Slice 4 placeholder seam; real cloud KMS key handling is "
            "deferred to the first cloud-infra slice (see decisions.md D24/D40)"
        )
