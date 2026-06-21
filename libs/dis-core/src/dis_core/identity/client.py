"""Identity Service client interface and HTTP implementation.

``IdentityClient`` is the Protocol every consumer programs against. The Slice 2
fake and the Slice 13 real service both sit behind ``HttpIdentityClient`` —
"drop-in" means swapping the ``base_url`` (``IDENTITY_SERVICE_URL``), nothing in
caller code changes.

The client's error types (``IdentityClientError`` and subclasses) live in the
shared ``dis_core.errors`` hierarchy (consolidated there in Slice 3). They are
imported here and re-exported by ``dis_core.identity`` so existing imports
(``from dis_core.identity import IdentityNotFoundError``) keep working.
"""

from __future__ import annotations

from typing import Protocol, runtime_checkable
from uuid import UUID

import httpx

from dis_core.errors import (
    IdentityClientError,
    IdentityNotFoundError,
    IdentityServiceUnavailableError,
)
from dis_core.identity.models import (
    Error,
    Identity,
    ResolveFromEndpointRequest,
    ResolveFromTokenRequest,
    ResolveFromUploadRequest,
    ValidateRequest,
    ValidateResponse,
)

__all__ = [
    "HttpIdentityClient",
    "IdentityClient",
    "IdentityClientError",
    "IdentityNotFoundError",
    "IdentityServiceUnavailableError",
]


@runtime_checkable
class IdentityClient(Protocol):
    """The four Identity Service methods (architecture §4.2 / OpenAPI v1).

    Async because every DIS consumer (receivers, streaming-consumer, dis-ui-server)
    is async FastAPI. The real Slice 13 service is reached the same way.
    """

    async def resolve_from_token(self, jwt: str) -> Identity: ...

    async def resolve_from_upload(self, upload_session_id: str) -> Identity: ...

    async def resolve_from_endpoint(self, endpoint_config_id: str) -> Identity: ...

    async def validate(self, tenant_id: UUID, store_id: UUID) -> ValidateResponse: ...


class HttpIdentityClient:
    """HTTP implementation of :class:`IdentityClient` (talks the OpenAPI contract).

    Works against any server honoring the contract — the Slice 2 fake or the real
    Slice 13 service. Pass ``base_url`` from ``IDENTITY_SERVICE_URL``. An
    ``httpx.AsyncClient`` may be injected (e.g. an ASGI transport pointing at the
    in-process fake) for tests; otherwise one is created and owned by this client.
    """

    def __init__(
        self,
        base_url: str,
        *,
        service_token: str | None = None,
        timeout: float = 5.0,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        self._base_url = base_url.rstrip("/")
        self._service_token = service_token
        self._owns_client = client is None
        headers = {"Authorization": f"Bearer {service_token}"} if service_token else {}
        self._client = client or httpx.AsyncClient(base_url=self._base_url, timeout=timeout, headers=headers)

    async def __aenter__(self) -> HttpIdentityClient:
        return self

    async def __aexit__(self, *exc: object) -> None:
        await self.aclose()

    async def aclose(self) -> None:
        if self._owns_client:
            await self._client.aclose()

    # -- the four methods ----------------------------------------------------

    async def resolve_from_token(self, jwt: str) -> Identity:
        body = await self._post("/v1/resolve_from_token", ResolveFromTokenRequest(jwt=jwt))
        return Identity.model_validate(body)

    async def resolve_from_upload(self, upload_session_id: str) -> Identity:
        body = await self._post(
            "/v1/resolve_from_upload",
            ResolveFromUploadRequest(upload_session_id=upload_session_id),
        )
        return Identity.model_validate(body)

    async def resolve_from_endpoint(self, endpoint_config_id: str) -> Identity:
        body = await self._post(
            "/v1/resolve_from_endpoint",
            ResolveFromEndpointRequest(endpoint_config_id=endpoint_config_id),
        )
        return Identity.model_validate(body)

    async def validate(self, tenant_id: UUID, store_id: UUID) -> ValidateResponse:
        body = await self._post("/v1/validate", ValidateRequest(tenant_id=tenant_id, store_id=store_id))
        return ValidateResponse.model_validate(body)

    # -- transport -----------------------------------------------------------

    async def _post(self, path: str, payload: object) -> object:
        # Both construction paths (injected client and self-created client) set
        # base_url, so posting the relative path joins correctly in either case.
        response = await self._client.post(path, json=_dump(payload))
        if response.is_success:
            return response.json()
        raise self._to_error(response)

    def _to_error(self, response: httpx.Response) -> IdentityClientError:
        error_code: str | None = None
        message = f"Identity Service returned HTTP {response.status_code}"
        trace_id: str | None = None
        try:
            err = Error.model_validate(response.json())
            error_code, message, trace_id = err.error_code, err.message, err.trace_id
        except (ValueError, httpx.DecodingError):
            pass  # non-JSON / non-conforming body; fall back to the generic message

        if response.status_code == 404 or error_code == "identity_not_found":
            return IdentityNotFoundError(
                message, status_code=response.status_code, error_code=error_code, trace_id=trace_id
            )
        if response.status_code == 503 or error_code == "circuit_open":
            retry_after = response.headers.get("Retry-After")
            return IdentityServiceUnavailableError(
                message,
                status_code=response.status_code,
                error_code=error_code,
                trace_id=trace_id,
                retry_after=int(retry_after) if retry_after and retry_after.isdigit() else None,
            )
        return IdentityClientError(
            message, status_code=response.status_code, error_code=error_code, trace_id=trace_id
        )


def _dump(payload: object) -> dict[str, object]:
    # mode="json" because ValidateRequest carries UUID fields (Slice 9a, D37):
    # the httpx json= encoder cannot serialise a raw uuid.UUID.
    if hasattr(payload, "model_dump"):
        return payload.model_dump(mode="json")  # type: ignore[no-any-return]
    raise TypeError(f"unexpected payload type: {type(payload)!r}")
