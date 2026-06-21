"""The Slice 30b stable failure vocabulary: membership, the registry, the fallback.

``failure_code`` has NO live CHECK (a free varchar(64)), so — like ``Stage`` —
closure is dis-audit's type-level guarantee and these unit pins are the guard.
The superset rule: every pre-30b emitted value maps to a member with its detail
preserved (the mapping table lives in ``failure_codes.py``'s member comments).
"""

from __future__ import annotations

import pytest

from dis_audit.failure_codes import FailureCode, failure_code_for
from dis_core.errors import (
    DisError,
    EventContractError,
    EventPathMismatchError,
    HotPositionMissingError,
    MappingConfigError,
    PiiBackendNotConfiguredError,
    SuiteDefinitionError,
)


def test_members_fit_the_live_column_width() -> None:
    # failure_code is varchar(64) live; every member must INSERT cleanly.
    assert all(len(code.value) <= 64 for code in FailureCode)


def test_members_are_unique_and_upper_snake() -> None:
    values = [code.value for code in FailureCode]
    assert len(values) == len(set(values))
    assert all(v.replace("_", "").isalnum() and v == v.upper() for v in values)


@pytest.mark.parametrize(
    ("exc", "expected"),
    [
        # The registry, pair by pair — subclass precedence included
        # (EventPathMismatchError is a CsvIngestError sibling of EventContractError
        # and must map to PATH_MISMATCH, not the contract bucket).
        (EventPathMismatchError("x"), FailureCode.PATH_MISMATCH),
        (EventContractError("x"), FailureCode.CONTRACT_VIOLATION),
        (MappingConfigError("x"), FailureCode.MAPPING_CONFIG_INVALID),
        (SuiteDefinitionError("x"), FailureCode.SUITE_REF_UNSUPPORTED),
        (HotPositionMissingError("x"), FailureCode.HOT_POSITION_MISSING),
        (PiiBackendNotConfiguredError("x"), FailureCode.PII_BACKEND_NOT_CONFIGURED),
    ],
)
def test_registry_maps_each_known_error_type(exc: Exception, expected: FailureCode) -> None:
    assert failure_code_for(exc) is expected


def test_unmapped_exception_falls_back_to_infra_failure() -> None:
    # The no-information-loss rule: the EMITTER preserves type(exc).__name__ in
    # event_data["exception_class"]; the registry just routes to the bucket.
    assert failure_code_for(RuntimeError("boom")) is FailureCode.INFRA_FAILURE
    assert failure_code_for(DisError("bare domain error")) is FailureCode.INFRA_FAILURE


def test_codes_are_plain_strings_for_the_emit_seams() -> None:
    # The service audit wrappers type failure_code as `str | None`; the StrEnum
    # member must BE the wire string (no .value plumbing at emit sites).
    assert isinstance(FailureCode.MAPPING_CONFIG_INVALID, str)
    assert f"{FailureCode.PATH_MISMATCH}" == "PATH_MISMATCH"
