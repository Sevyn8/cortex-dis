"""V4 signed-URL issuance (AC6 part a): deterministic, offline, well-formed.

A well-formed URL with the correct expiry — NOT proof that real GCS accepts the
signature (that is unverified until a real-GCS slice; first use Slice 8). Signing uses
a throwaway test service-account credential generated in-process; never a real SA.
"""

from __future__ import annotations

from typing import TYPE_CHECKING
from urllib.parse import parse_qs, unquote, urlparse

import pytest

from dis_core.errors import StorageError
from dis_storage.signed_urls import generate_upload_url

if TYPE_CHECKING:  # runtime import stays lazy inside the fixture
    from google.oauth2 import service_account

_BUCKET = "ithina-bronze-raw"
_OBJECT = "tenant/t-abc/source/manual_csv_upload/yyyy=2026/mm=06/dd=03/trace-xyz.csv"


@pytest.fixture(scope="module")
def throwaway_signer() -> service_account.Credentials:
    """A throwaway service-account credential with a freshly generated RSA key.

    Local-only: it has a signer (private key) so V4 signing works offline, but the key
    is generated per test run and is never a real Google service account.
    """
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric import rsa
    from google.oauth2 import service_account

    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    pem = key.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.NoEncryption(),
    ).decode()
    info = {
        "type": "service_account",
        "project_id": "dis-test",
        "private_key_id": "test-key-id",
        "private_key": pem,
        "client_email": "throwaway@dis-test.iam.gserviceaccount.com",
        "client_id": "0",
        "token_uri": "https://oauth2.googleapis.com/token",
    }
    creds: service_account.Credentials = service_account.Credentials.from_service_account_info(info)  # type: ignore[no-untyped-call]  # google.oauth2 ships this untyped
    return creds


def test_issues_well_formed_v4_url_with_correct_expiry(
    throwaway_signer: service_account.Credentials, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Pin the real-GCS issuance shape: with STORAGE_EMULATOR_HOST set (local stack),
    # the client emits an http://emulator URL instead — correct, but here we assert the
    # production (https / storage.googleapis.com) form deterministically.
    monkeypatch.delenv("STORAGE_EMULATOR_HOST", raising=False)
    url = generate_upload_url(_OBJECT, bucket=_BUCKET, expires_seconds=900, credentials=throwaway_signer)
    parsed = urlparse(url)
    qs = parse_qs(parsed.query)

    assert parsed.scheme == "https"
    # V4 signing markers.
    assert qs["X-Goog-Algorithm"] == ["GOOG4-RSA-SHA256"]
    assert qs["X-Goog-Expires"] == ["900"]  # the configured 15 minutes, exactly
    assert "X-Goog-Signature" in qs
    assert "X-Goog-Credential" in qs
    # Scoped to exactly the issued object path (bucket + key present, not a wildcard).
    path = unquote(parsed.path)
    assert _BUCKET in path
    assert _OBJECT in path


@pytest.mark.parametrize("bad", [0, -1, 7 * 24 * 60 * 60 + 1])
def test_rejects_out_of_range_expiry(throwaway_signer: service_account.Credentials, bad: int) -> None:
    with pytest.raises(StorageError):
        generate_upload_url(_OBJECT, bucket=_BUCKET, expires_seconds=bad, credentials=throwaway_signer)


def test_rejects_empty_object_path(throwaway_signer: service_account.Credentials) -> None:
    with pytest.raises(StorageError):
        generate_upload_url("", bucket=_BUCKET, expires_seconds=900, credentials=throwaway_signer)
