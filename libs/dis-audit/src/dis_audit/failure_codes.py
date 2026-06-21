"""The stable ``failure_code`` vocabulary ``dis-audit`` owns (Slice 30b).

Before this slice ``failure_code`` was an unstable mix: exception class names
(the consumer catch-all), per-site strings (``path_mismatch``,
``gcs_write_failed``), raw reason codes (``not_csv``), stage names, and pandera
check names. That defeats "all X failures" queries. :class:`FailureCode` is the
closed enumerated replacement, one member per (service, failure path).

Like :class:`~dis_audit.stages.Stage`, closure is a TYPE-LEVEL guarantee: the
live ``failure_code`` column is a free ``varchar(64)`` with no CHECK, so the
lib is the vocabulary's owner and no DDL is involved.

Information-loss guarantee (the superset rule): every pre-30b value maps to a
member; variable detail that cannot be enumerated moves to ``event_data``
(``check`` for pandera check names, ``exception_class`` for unmapped exception
types under :attr:`FailureCode.INFRA_FAILURE`, ``reason`` for tier-0) or stays
in ``failure_message``.

:func:`failure_code_for` is the exception-type registry the consumer catch-all
uses. It maps dis-core error types only (dis-audit depends on dis-core alone);
service-local codes (the dis-ui 4xx family, worker preflight reasons) are
mapped at their emit sites, where the path is known.
"""

from __future__ import annotations

from enum import StrEnum

from dis_core.errors import (
    EventContractError,
    EventPathMismatchError,
    HotPositionMissingError,
    MappingConfigError,
    PiiBackendNotConfiguredError,
    SuiteDefinitionError,
)


class FailureCode(StrEnum):
    """One member per failure path. Closed, owned vocabulary (no DB CHECK).

    Grouped by emitting service; the member is stable even if the underlying
    exception class or message changes.
    """

    # -- dis-ui-server (the upload receiver; stage RECEIVED) -------------------
    UPLOAD_REQUEST_MALFORMED = "UPLOAD_REQUEST_MALFORMED"  # 400 multipart shape
    UPLOAD_TOO_LARGE = "UPLOAD_TOO_LARGE"  # 413 mid-stream / content-length
    UPLOAD_EMPTY_FILE = "UPLOAD_EMPTY_FILE"  # 422 tier-0
    UPLOAD_NOT_UTF8 = "UPLOAD_NOT_UTF8"  # 422 tier-0
    UPLOAD_NOT_CSV = "UPLOAD_NOT_CSV"  # 422 tier-0
    UPLOAD_BELOW_MIN_ROWS = "UPLOAD_BELOW_MIN_ROWS"  # 422 tier-0
    TEMPLATE_NOT_FOUND = "TEMPLATE_NOT_FOUND"  # 404 (unknown / cross-tenant)
    TEMPLATE_NOT_ACTIVE = "TEMPLATE_NOT_ACTIVE"  # 409 lifecycle gate
    STORE_NOT_FOUND = "STORE_NOT_FOUND"  # 404 (unknown / cross-tenant)
    STORE_NOT_ACTIVE = "STORE_NOT_ACTIVE"  # 409 lifecycle gate
    GCS_WRITE_FAILED = "GCS_WRITE_FAILED"  # was "gcs_write_failed"
    PUBLISH_FAILED = "PUBLISH_FAILED"  # was "publish_failed"

    # -- csv-ingest-worker ------------------------------------------------------
    PATH_MISMATCH = "PATH_MISMATCH"  # was "path_mismatch"
    # The DuckDB preflight's closed reason set, prefixed so the tier-0 and
    # preflight "not CSV" verdicts stay distinguishable in one column.
    PREFLIGHT_NOT_CSV = "PREFLIGHT_NOT_CSV"  # was reason "not_csv"
    PREFLIGHT_NO_COLUMNS = "PREFLIGHT_NO_COLUMNS"  # was reason "no_columns"
    PREFLIGHT_NO_HEADER = "PREFLIGHT_NO_HEADER"  # was reason "no_header"
    PREFLIGHT_NO_DATA_ROWS = "PREFLIGHT_NO_DATA_ROWS"  # was reason "no_data_rows"
    PII_BACKEND_NOT_CONFIGURED = "PII_BACKEND_NOT_CONFIGURED"  # was lowercase

    # -- streaming-consumer ------------------------------------------------------
    CONTRACT_VIOLATION = "CONTRACT_VIOLATION"  # EventContractError post-parse
    MAPPING_CONFIG_INVALID = "MAPPING_CONFIG_INVALID"  # MappingConfigError
    SUITE_REF_UNSUPPORTED = "SUITE_REF_UNSUPPORTED"  # SuiteDefinitionError
    PRE_VALIDATION_FAILED = "PRE_VALIDATION_FAILED"  # gate summary (was stage name)
    MAPPING_EXECUTION_FAILED = "MAPPING_EXECUTION_FAILED"  # gate summary
    POST_VALIDATION_FAILED = "POST_VALIDATION_FAILED"  # gate summary
    VALIDATION_ROW_FAILED = "VALIDATION_ROW_FAILED"  # ROW scope; check -> event_data
    HOT_POSITION_MISSING = "HOT_POSITION_MISSING"  # the REVISED-D63 hot-merge miss

    # -- the fallback ------------------------------------------------------------
    # Any exception type the registry does not map. The class name is preserved
    # in event_data["exception_class"] by the emitting catch-all, so promoting a
    # recurring INFRA_FAILURE to its own member later loses nothing.
    INFRA_FAILURE = "INFRA_FAILURE"


# The exception-type registry for catch-all emit sites. Ordered: isinstance()
# walks this in declaration order, so subclasses must precede their bases
# (EventPathMismatchError before EventContractError's sibling check is moot —
# both are CsvIngestError children — but PATH_MISMATCH must win over the
# CONTRACT_VIOLATION fallback for its own type).
_REGISTRY: tuple[tuple[type[Exception], FailureCode], ...] = (
    (EventPathMismatchError, FailureCode.PATH_MISMATCH),
    (EventContractError, FailureCode.CONTRACT_VIOLATION),
    (MappingConfigError, FailureCode.MAPPING_CONFIG_INVALID),
    (SuiteDefinitionError, FailureCode.SUITE_REF_UNSUPPORTED),
    (HotPositionMissingError, FailureCode.HOT_POSITION_MISSING),
    (PiiBackendNotConfiguredError, FailureCode.PII_BACKEND_NOT_CONFIGURED),
)


def failure_code_for(exc: Exception) -> FailureCode:
    """The stable code for an exception, or :attr:`FailureCode.INFRA_FAILURE`.

    Callers emitting ``INFRA_FAILURE`` must preserve the class name in
    ``event_data["exception_class"]`` (the no-information-loss rule).
    """
    for exc_type, code in _REGISTRY:
        if isinstance(exc, exc_type):
            return code
    return FailureCode.INFRA_FAILURE
