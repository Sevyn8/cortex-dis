# Slice 2: Identity Service fake, Customer Master fake, and test fixture seeder

## Depends on

- Slice 1 for the `identity_mirror.tenants`, `identity_mirror.stores`, and
  `config.source_mappings` tables the seeder writes into.
- Phase 0 frozen contracts: the authoritative Identity Service OpenAPI, the
  `identity.changed` Pub/Sub schema, and the Pub/Sub emulator + topics already
  in docker-compose.
- Forward dependency on `libs/dis-core` (Slice 3, not yet built) for UUIDv7 and
  error types. How to handle that ordering is an open question below, not a
  settled prerequisite.

## Goal

After this slice, every later slice can test against local fakes instead of a
running Customer Master. Tests can obtain a signed JWT, resolve identity through
the four Identity Service methods, and find seeded tenants, stores, and a
default source mapping already present in the DIS database, all without any real
Customer Master, real authentication, or real identity resolution running.

## Task

Build three pieces of test infrastructure:

1. A Customer Master fake (FastAPI app in docker-compose) that issues signed
   test JWTs, publishes a JWKS endpoint for signature verification, serves
   upload sessions, and emits `identity.changed` Pub/Sub events when tenants or
   stores are seeded or changed.
2. An Identity Service fake (FastAPI app in docker-compose) that answers all
   four methods (`resolve_from_token`, `resolve_from_upload`,
   `resolve_from_endpoint`, `validate`) with canned data conforming to the
   authoritative OpenAPI contract.
3. A test fixture seeder that writes test tenants and stores into
   `identity_mirror` plus a default test `config.source_mappings` row, so FK and
   RLS behavior can be exercised in later tests. The seeder bypasses the
   Slice 7 Customer-Master-DB sync entirely.

## Acceptance criteria

1. `make run-local` brings both fakes up as healthy docker-compose services.
2. A JWT issued by the Customer Master fake verifies successfully against the
   JWKS the fake publishes (signature and claims), using the same verification
   path consuming code will use.
3. The Customer Master fake serves an upload session and emits an
   `identity.changed` event on seed/change; the emitted message validates
   against the frozen `identity.changed` Pub/Sub schema.
4. The Identity Service fake answers all four methods, and each response
   validates against the authoritative OpenAPI contract.
5. The seeder writes the default test set (tenants, stores, one
   `config.source_mappings` row) into the DIS database; re-running it is
   idempotent (no duplicates, no error).
6. Seeded tenant and store IDs are consistent with the Identity Service fake's
   canned answers, so a test that resolves identity then queries
   `identity_mirror` or canonical gets matching IDs.
7. Harness smoke test: a single test obtains a JWT from the CM fake, resolves it
   via the Identity Service fake, and reads the corresponding seeded tenant from
   `identity_mirror`, end to end, against the local stack.
8. Fakes are consumed through a client interface that the real Identity Service
   (Slice 13) can later satisfy as a drop-in, so test code does not change when
   the real implementation lands.

## Scope boundary

In scope:
- Two FastAPI fakes and one seeder, sufficient for later slices to test against.
- Signed JWT issuance + JWKS publication adequate for real verification.
- Canned Identity Service responses matching the OpenAPI contract.
- Seeding `identity_mirror.tenants`, `identity_mirror.stores`, and one default
  `config.source_mappings` row.

Out of scope (the fakes must not drift into real behavior):
- Real authentication or authorization. The CM fake signs tokens; it does not
  validate credentials or enforce access.
- Real identity resolution. The Identity Service fake returns canned data; no
  Customer Master lookup, no cache, no stale-while-error fallback. That is
  Slice 13.
- The Mirror Sync DB-pull (Slice 7). The seeder is a test shortcut around it and
  must not implement or partially implement sync logic.
- Runtime population of `identity_mirror` or runtime source-mapping creation
  (Slice 7 / Slice 14). The seeder is tests-only and never a runtime path.
- Any consuming service (dis-ui-server, receivers, streaming consumer). They are
  built later and test against these fakes.
- Dependence on a real Customer Master at 5432. The fake replaces it for tests.
- The Pub/Sub-driven incremental identity consumer (deferred).
- Authoring or changing any frozen contract. If a contract is insufficient to
  build a fake, surface it in plan mode rather than inventing the shape.

## Constraints

- Both fakes are FastAPI apps wired into docker-compose, started by
  `make run-local`.
- The Identity Service fake's responses conform to the authoritative OpenAPI
  contract; the proto is reference only.
- The CM fake's `identity.changed` emissions validate against the frozen
  Pub/Sub schema; do not change the schema.
- The seeder is test infrastructure only. It must be impossible to mistake it
  for a runtime path; runtime `identity_mirror` population is Slice 7.
- The seeder writes a default `config.source_mappings` row so
  `mapping_version_id` FKs resolve in later slices' tests.
- All generated IDs use UUIDv7 (CLAUDE.md hard rule 3); never `uuid.uuid4`.
- Fakes and seeder live in the directories the repo structure already reserves
  for them; confirm exact placement in plan mode rather than inventing new dirs.

## Open questions (for plan mode to resolve)

1. JWT mechanics. Confirm the signing algorithm and key type the JWKS pattern
   implies (asymmetric, e.g. RS256), how the fake generates and holds its
   signing key, and the exact claim set, all against the frozen Identity Service
   / Customer Master contract and what the consuming verification path expects.
2. JWKS discovery. What URL or well-known path the fake publishes, and how
   verifiers are pointed at it (env var, config). Confirm it matches how real
   verification will be configured.
3. Customer Master contract sufficiency. Phase 0 lists the Customer Master
   contract as still TODO (not yet signed off by the CM team). Determine whether
   enough of the JWT shape, JWKS, upload-session lifecycle, and
   `identity.changed` event shape is pinned to build the fake faithfully. If
   not, decide what the fake implements provisionally and what gets flagged for
   revision when the CM contract is signed off. Do not invent contract surface
   silently.
4. Identity Service canned data. Derive the four methods' exact request and
   response shapes from the authoritative OpenAPI. Decide how canned data is
   configured and how it is kept consistent with what the seeder writes (shared
   ID set, single source of fixture truth).
5. Seeder interface and default set. CLI command, pytest fixture, or both; where
   it lives; the default seed quantities; the shape of the default
   `config.source_mappings` row; and the idempotency mechanism. Confirm which DB
   role the seeder writes as, and that the target tables' RLS posture (per Slice
   1: `identity_mirror.tenants/stores` and `config.source_mappings` are not
   RLS-protected) means no tenant context is needed for the write.
6. dis-core dependency direction. The fakes and seeder need UUIDv7 generation
   and possibly error types that formally belong to `libs/dis-core` (Slice 3,
   not yet built). Decide: take a minimal early dependency on dis-core, use
   `uuid-utils` directly for now, or another approach, without building a helper
   that Slice 3 will duplicate.
7. Client interface for drop-in replacement. Determine what client abstraction
   Slice 2 must define so the real Identity Service (Slice 13) is a drop-in and
   test code does not change. Confirm whether that interface is defined here or
   already exists in the repo.
