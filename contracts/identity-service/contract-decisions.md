# DIS Identity Service Contract: Key Design Decisions

**Scope.** This document captures the design decisions behind the identity-service contracts frozen in Phase 0. Two contract files describe the same surface in different formats:

- `contracts/identity-service/identity_service.openapi.yaml` (HTTP/REST, authoritative)
- `contracts/identity-service/identity_service.proto` (gRPC, reference for future migration)

**Status.** Phase 0 freeze. Contract is at API version `v1` (URL prefix and gRPC package). Additive changes (new optional fields, new error codes) do not require an API version bump. Breaking changes increment to `v2` with overlap support during transition.

**Authoritative transport.** HTTP/REST. Reasons in §1 below. The gRPC contract is committed alongside as a parallel reference and a forward-compatible target if the platform's call volume later justifies migration. Only HTTP/REST clients and servers are generated and deployed at launch.

---

## 1. Transport: HTTP/REST over gRPC

**Decision.** HTTP/REST with JSON payloads is the transport. The OpenAPI file is authoritative; the proto file is committed for reference.

**Why HTTP/REST.**

- The team is more familiar with HTTP/REST than with gRPC. Familiarity reduces operational risk in a foundation phase.
- Debugging is trivial. Any HTTP-aware tool (curl, Postman, browser network panel, cloud load balancer logs) sees calls in plain text. gRPC binary payloads need specialized tooling.
- Tooling for OpenAPI + Python (FastAPI, pydantic, datamodel-code-generator) is mature and matches the rest of the DIS stack.
- Launch call volume is single-digit RPS. The latency difference between gRPC and HTTP/REST is in the noise.
- FastAPI's automatic OpenAPI generation can be cross-checked against the frozen contract; CI can catch drift between intent and implementation.

**Why gRPC is documented anyway.**

- If call volume grows to thousands of RPS, the wire-format efficiency and HTTP/2 multiplexing become meaningful.
- Having the proto committed now means a future migration is a known transformation, not a redesign.
- The proto serves as a typed reference for the OpenAPI schema: if the two ever drift, the gRPC version surfaces it.

**Decision authority.** Switching the authoritative transport from HTTP/REST to gRPC (or adding gRPC as a parallel runtime) requires an ADR. Until then, gRPC is reference-only.

---

## 2. The four methods

The contract surfaces exactly four operations, matching architecture §5.2:

| Method | Purpose | Caller(s) |
|---|---|---|
| `resolve_from_token` | Identity from Customer Master JWT | receiver-api, receiver-webhook (deferred in v1.0) |
| `resolve_from_upload` | Identity from upload session ID | csv-ingest-worker (v1.0), receiver-csv-erp (deferred) |
| `resolve_from_endpoint` | Identity from endpoint config ID | receiver-reverse-api (deferred), receiver-csv-erp (deferred) |
| `validate` | Existence + active check for (tenant, store) | streaming-consumer (v1.0) |

**Why three resolve methods, not one.**

Different callers carry different auth artifacts. A receiver-api gets a JWT; a csv-upload receiver gets an upload session; a reverse-API puller has an endpoint config. The "translate this artifact to identity" logic differs per artifact. One union method (`resolve(artifact: oneOf[...])`) would be ambiguous on the caller side and harder to type-check.

**Why `validate` is separate from the resolve methods.**

Different shape, different semantics. Resolve answers "who is this caller?"; validate answers "does this (tenant_id, store_id) still exist and is it active?" Validate is cheaper (smaller cache key, smaller response), can fall back to identity_mirror when Customer Master is unreachable, and returns `exists: false` as a normal answer rather than an error.

**Why all four methods even though v1.0 ships only two callers.**

Receivers other than csv-upload (api, csv-erp, reverse-api) are deferred to a later release. Their identity-resolution methods are defined in the contract now so the contract doesn't need a breaking change when those receivers are built. Phase 0 cost: one yaml file with three more endpoints; Phase 0 benefit: the identity-service implementation can be built once with all four methods.

---

## 3. The `Identity` response object

All three resolve methods return the same `Identity` shape.

**Why one shape, not three.**

The downstream fields a caller needs are roughly the same regardless of how identity was resolved: `tenant_id`, `store_id`, `is_active`, optional metadata. Different shapes per method would force callers to thread three nearly-identical types through their pipelines.

**Why `metadata` is open (additionalProperties / Struct).**

Customer Master may carry tenant- or store-specific fields that DIS doesn't enumerate in advance: PII policy version, source config, region, billing tier. Receivers may read these; the streaming consumer typically does not. Pinning the metadata shape would force a contract bump every time Customer Master adds a field DIS wants to read.

**Why `source` is on every response.**

Callers can decide whether to trust the answer for security-sensitive paths. A receiver writing to canonical might refuse a `cache_stale` response and force a fresh fetch; the streaming consumer might accept stale during a Customer Master outage. The field is informational; consumers project what they care about.

**`source` enum values:**

- `cache_fresh`: within cache TTL, freshness guaranteed.
- `cache_stale`: served stale during a Customer Master outage. Up to 5 minutes per architecture §4.28.
- `customer_master`: cache miss, freshly fetched from Customer Master.
- `identity_mirror_fallback`: only on `validate` responses. Local mirror used when Customer Master is unreachable and no cache entry exists.

---

## 4. The `validate` method's distinct shape

`validate` is the only method that doesn't return `Identity`. It returns `ValidateResponse` with `exists`, `is_active`, and `source`.

**Why a separate response shape.**

The streaming consumer's FK pre-check needs two booleans, not a full identity. A full `Identity` would force the consumer to ignore most of the response. Smaller response = less bandwidth, less serialization, less cache pressure.

**Why `exists: false` instead of 404.**

For this method, "does not exist" is the question, not an error. A 404 would force callers into exception-handling for a normal-flow answer. `exists: false` is a successful, expected response. Reserved for the case where the tenant/store was never created; deactivation is `is_active: false`.

**Why validate can fall back to identity_mirror but resolve methods cannot.**

Resolve methods translate auth artifacts (JWT, session, endpoint) into identity; that translation requires Customer Master's logic and is not replicated in identity_mirror. Validate just asks "does this (tenant, store) pair exist and is it active?", which the mirror can answer directly. Stale-while-error is wider for validate than for resolve.

---

## 5. Error model

**Five error categories** mapped to HTTP status codes / gRPC status codes:

| Condition | HTTP | gRPC | Retry posture |
|---|---|---|---|
| Bad service token | 401 | UNAUTHENTICATED | No retry; reissue token |
| Tenant/store not found (resolve) | 404 | NOT_FOUND | No retry; hard failure |
| Customer Master down + stale window exceeded | 503 | UNAVAILABLE | Retry with backoff (resolve); fall back to identity_mirror (validate) |
| Unexpected server error | 500 | INTERNAL | Retry with backoff |
| Malformed request | 400 | INVALID_ARGUMENT | No retry; fix the request |

**Why these five.**

These cover every distinct caller action. A new caller, given the error code, knows exactly what to do. Subtler distinctions (which Customer Master endpoint failed, which cache layer missed) are server-side concerns; clients don't branch on them.

**Why 503 carries `Retry-After`.**

Standard HTTP semantics. Lets the server tell clients how long the circuit breaker expects to stay open. Cost: clients honor the header (most HTTP clients do automatically).

**Why every error response carries `trace_id`.**

Server-side correlation. When a client logs "identity-service returned 503 at 14:32:00", ops needs to find the matching server log. The server's `trace_id` (distinct from any client-side `trace_id`) is the join key.

**Why `error_code` is a separate field, not just the status code.**

Status codes are coarse (401 covers many auth failures). The `error_code` enum lets callers branch on specific failure modes (token expired vs token signature invalid vs token from wrong issuer) without parsing the human-readable `message` string.

---

## 6. Auth posture for the service itself

**Decision.** Every call to identity-service requires an internal service-to-service token. The token is verified server-side; the schema declares the requirement; clients attach it as `Authorization: Bearer ...`.

**Why not open / unauthenticated.**

identity-service holds Customer Master credentials and the cache of resolved identities. An open service would let any code in the cluster (or any cluster-reachable host) bypass Customer Master's RBAC. Auth on identity-service is a defense-in-depth boundary.

**Why bearer JWT, not mTLS.**

mTLS adds operational complexity (cert rotation, CA setup) not justified at v0 scale. Service-account-issued JWTs verified against a JWKS endpoint are simpler and sufficient. Moving to mTLS later is additive.

**Why probes (`/healthz`, `/readyz`) opt out of auth.**

Kubernetes / Cloud Run liveness and readiness probes call from the orchestrator, not from another service. Requiring them to carry a service token complicates orchestrator config. The probes return only "yes/no, ready/not ready"; no information leaks.

---

## 7. Versioning

**URL versioning (HTTP/REST) and package versioning (gRPC).** Both express the same intent: v1 is the current contract; v2 lives at a different path / in a different package.

**What counts as additive (no version bump):**
- New optional fields on requests or responses.
- New values in open enums (e.g., a new `source` value beyond the four current ones).
- New methods.
- New error codes.

**What counts as breaking (v2 required):**
- Removed required fields.
- Changed field types or formats.
- Renamed fields.
- Removed methods or error codes.

**Migration during a breaking change:**
- The v2 contract ships alongside v1 in the same `contracts/identity-service/` directory.
- The service implementation supports both v1 and v2 simultaneously.
- Callers migrate one at a time.
- After all callers migrate, v1 is retired (file archived, not deleted, for historical context).

**Decision authority.** Additive changes need only the lib owner's review. Breaking changes need an ADR.

---

## 8. Why no streaming methods

**Decision.** All four methods are unary (single request, single response). No server-streaming, no bidirectional streaming.

**Why.**

- The current scope has no use case for streaming. Receivers and the streaming consumer make one identity call per ingress event; there is no "stream of identities" pattern.
- Unary methods are simpler to test, monitor, and migrate across transports.
- Streaming can be added in v1.1+ if a use case emerges (e.g., bulk validation of N tenant+store pairs in one call). Additive.

**Alternative considered.** A `BatchValidate(repeated ValidateRequest) returns (repeated ValidateResponse)` for the streaming consumer to validate N rows in one call. Rejected for v1.0 because per-row validation is cache-hot and per-call overhead is negligible at v1.0 RPS. May add in v1.1 if profiling shows otherwise.

---

## 9. What this contract intentionally does not cover

- **The implementation of the cache.** The contract describes the surface; cache backend (in-memory LRU vs Redis), TTL values, and eviction policy are implementation choices documented in the service's CLAUDE.md, not pinned in the contract.
- **How identity-service talks to Customer Master.** That is a separate contract (`contracts/customer-master/`), owned by the Customer Master team. identity-service is the caller; the Customer Master contract describes that surface.
- **Audit emission.** The service emits audit events for cache misses, Customer Master calls, and circuit-breaker transitions. Audit event shape lives in `libs/dis-audit` and is documented there.
- **Configuration.** Cache TTLs, circuit-breaker thresholds, Customer Master URL, JWKS URL are environment variables read by the service's `config.py`. Pinned in `infra/env/` per environment, not in the contract.

---

## 10. Files

```
contracts/identity-service/
├── identity_service.openapi.yaml     # Authoritative (HTTP/REST)
└── identity_service.proto            # Reference (gRPC, future migration target)
```

Both are committed. The OpenAPI file is the one CI validates implementations against; the proto file is reviewed for parity but not actively wired into code generation at launch.
