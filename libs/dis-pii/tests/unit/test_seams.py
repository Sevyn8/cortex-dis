"""The crypto / key / policy seams are import-safe and make no real call (AC5)."""

from __future__ import annotations

import pytest

from dis_pii.key_vault import KeyVault
from dis_pii.policy import TokenizationPolicy
from dis_pii.tokenizer import HmacTokenizer


def test_seams_construct_without_io() -> None:
    # Construction stores config only; no KMS / network / DB. If any did I/O offline
    # this would raise instead of returning an instance.
    assert HmacTokenizer(key_version=1) is not None
    assert KeyVault() is not None
    assert TokenizationPolicy() is not None


def test_tokenizer_method_is_an_unimplemented_seam() -> None:
    with pytest.raises(NotImplementedError):
        HmacTokenizer().tokenize("anything", tenant_id="t-1")


def test_key_vault_method_is_an_unimplemented_seam() -> None:
    with pytest.raises(NotImplementedError):
        KeyVault().get_key(tenant_id="t-1")


def test_policy_method_is_an_unimplemented_seam() -> None:
    with pytest.raises(NotImplementedError):
        TokenizationPolicy().columns_for_source(source_id="manual_csv_upload")
