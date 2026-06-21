"""DisError → HTTP status + the §2.3 error envelope.

Business logic raises dis-core domain errors, never ``HTTPException`` (root
CLAUDE.md convention); these handlers are the single place a DIS error becomes
an HTTP response. Envelope (every error response, §2.3):

    {"error": {"code", "message", "trace_id", "details"}}

``code`` is the snake_case error-class name minus the ``Error`` suffix (the
contract's ``mapping_state_conflict`` pattern); ``message`` is human-readable
and PII-free (domain errors never carry payloads or tokens by construction);
``trace_id`` is present when one is bound on the request context (13a mints
none — minting happens only at the two ingress-starting endpoints, later
slices); ``details`` carries the error's load-bearing context attributes
(code-quality rule 5). Body-shape validation failures (FastAPI 422) use the
same envelope, with the offending input values STRIPPED — a request body can
contain anything, including PII, and must never echo into an error response.

The handlers live in this service, not dis-core: dis-core is deliberately
FastAPI-free (dependency-light, its CLAUDE.md).
"""

from __future__ import annotations

import re
from typing import Any

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

from dis_core.errors import (
    AuthTokenError,
    DisError,
    EventPublishError,
    InvalidTemplateTypeError,
    MappingConfigError,
    MappingStateConflictError,
    MappingTemplateNameConflictError,
    OpsRoleRequiredError,
    PayloadTooLargeError,
    ResourceNotFoundError,
    RlsContextError,
    StorageError,
    StoreStateConflictError,
    TenantScopeError,
    UploadRequestError,
    UploadStructureError,
)
from dis_core.logging import get_logger
from dis_core.trace_id import TraceIdNotSetError, get_trace_id
from dis_ui_server.config import SERVICE_NAME

_log = get_logger(SERVICE_NAME)

# Explicit, reviewable mapping (contract §2.3). Resolution walks the MRO so a
# future leaf subclass inherits its family's status; an unmapped DisError is a
# plain 500 — fail visible, never invent a status.
_STATUS_BY_ERROR: dict[type[DisError], int] = {
    AuthTokenError: 401,
    TenantScopeError: 403,
    OpsRoleRequiredError: 403,
    # Slice 14b data endpoints (contract §2.3 + §7).
    MappingConfigError: 400,  # rules fail the D49 shape or the semantic gate
    InvalidTemplateTypeError: 400,  # template_type outside the in-code vocabulary (14d)
    ResourceNotFoundError: 404,  # throw-style lookups (template detail / PATCH)
    MappingTemplateNameConflictError: 409,  # EXCLUDE ex_csm_template_name_per_source
    MappingStateConflictError: 409,  # deprecated lineage / concurrent-edit race / non-ACTIVE upload target
    RlsContextError: 500,
    # Slice 8 csv-uploads (contract §8).
    UploadRequestError: 400,  # malformed multipart: missing/repeated part, bad template_id form
    PayloadTooLargeError: 413,  # the mid-stream ceiling (and the Content-Length early check)
    UploadStructureError: 422,  # tier-0 structural gate (D51): empty/not-utf8/not-csv/min-rows
    StoreStateConflictError: 409,  # resolved-but-not-ACTIVE store (after the 404 resolve — no oracle)
    # Retryable dependency failures: the request was valid; GCS or Pub/Sub was
    # not reachable. 503 (the IdentityServiceUnavailableError precedent), never
    # a misleading 4xx. Within this service StorageError surfaces only on the
    # upload's GCS write path (path construction inputs are validated upstream).
    StorageError: 503,
    EventPublishError: 503,  # object already written: the accepted-orphan posture
}

_FALLBACK_STATUS = 500

_CAMEL_BOUNDARY = re.compile(r"(?<!^)(?=[A-Z])")


def _status_for(exc: DisError) -> int:
    for klass in type(exc).__mro__:
        if klass in _STATUS_BY_ERROR:
            return _STATUS_BY_ERROR[klass]
    return _FALLBACK_STATUS


def _code_for(exc: DisError) -> str:
    snake = _CAMEL_BOUNDARY.sub("_", type(exc).__name__).lower()
    return snake.removesuffix("_error")


def _trace_id_or_none() -> str | None:
    try:
        return str(get_trace_id())
    except TraceIdNotSetError:
        return None


def _details_for(exc: DisError) -> dict[str, Any]:
    """The error's keyword-only context attributes, minus the message itself."""
    return {key: value for key, value in vars(exc).items() if key != "message" and value is not None}


def _envelope(*, code: str, message: str, details: dict[str, Any]) -> dict[str, Any]:
    return {
        "error": {
            "code": code,
            "message": message,
            "trace_id": _trace_id_or_none(),
            "details": details,
        }
    }


async def _handle_dis_error(request: Request, exc: DisError) -> JSONResponse:
    status = _status_for(exc)
    code = _code_for(exc)
    details = _details_for(exc)
    log = _log.bind(
        stage="http_error",
        tenant_id=details.get("tenant_id"),
        trace_id=_trace_id_or_none(),
    )
    # 4xx is expected traffic (bad/expired tokens, missing scope); 5xx is ours.
    if status >= 500:
        log.error("request failed: %s (%s %s)", code, request.method, request.url.path)
    else:
        log.warning("request rejected: %s (%s %s)", code, request.method, request.url.path)
    return JSONResponse(
        status_code=status,
        content=_envelope(code=code, message=str(exc), details=details),
    )


async def _handle_validation_error(request: Request, exc: RequestValidationError) -> JSONResponse:
    # Same envelope; the offending values are stripped (only locations and
    # messages survive) so request payloads never echo into error responses.
    errors = [
        {"loc": list(map(str, err.get("loc", ()))), "msg": err.get("msg"), "type": err.get("type")}
        for err in exc.errors()
    ]
    return JSONResponse(
        status_code=422,
        content=_envelope(
            code="request_validation",
            message="request body or parameters failed validation",
            details={"errors": errors},
        ),
    )


def register_error_handlers(app: FastAPI) -> None:
    """Install the envelope handlers; called once by the app factory."""
    app.add_exception_handler(DisError, _handle_dis_error)  # type: ignore[arg-type]
    app.add_exception_handler(RequestValidationError, _handle_validation_error)  # type: ignore[arg-type]
