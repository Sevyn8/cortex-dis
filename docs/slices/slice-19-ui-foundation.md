# Slice 19: DIS UI foundation

**Status:** TODO.

**Phase:** 1 (parallel service implementation, UI track per build-guide §2.2).

**Owners:** UI track. Aligned with Sanjeev on foundation scope.

---

## Goal

Initialize `services/dis-ui/` as a real frontend codebase: scaffolding, auth boundary that consumes Customer Master tokens, and a hello-world page that calls a `dis-ui-server` endpoint and renders the response.

This slice establishes the foundation only. Building the 11 actual DIS UI screens (per `docs/ui-engineer-demand-list.md` companion artifact) belongs to slice 20 and beyond.

## Hard constraints

1. **Stack is fixed for this slice.** Vite + React + TypeScript + Tailwind. pnpm package manager. TanStack Query for data fetching. react-router for routing. Vitest + React Testing Library for tests. No deviation in this slice; revisit only via a follow-up slice if a real architectural gap surfaces.

2. **No real `dis-ui-server` calls.** `dis-ui-server` has zero Python files. The hello-world page calls a stubbed client that returns a hardcoded `GET /me` response shaped per the demand list. Real wiring lands when slice 13 (`dis-ui-server` foundation) is implemented.

3. **No real Customer Master integration.** Auth scaffolding accepts a stub JWT (mirror of `admin-frontend` persona-JWT stub pattern). Real Customer Master OIDC integration is deferred per D25 ("RBAC claim vocabulary still open").

4. **No screen implementations beyond hello-world.** Routes for the 11 screens (per `dis-ui-surface-map.md`) are NOT scaffolded as part of this slice. Slice 20 owns that work.

5. **No production deploy.** Local dev (`pnpm dev`) and `pnpm build` clean is the bar. Dockerfile, Cloud Run config, CI pipeline are out of scope for this slice.

6. **No backend modifications.** This slice does not edit `services/dis-ui-server/`, `services/identity-service/`, or any backend code. Pure UI scaffolding.

7. **No commits to root `CLAUDE.md`** unless a new project-wide invariant is discovered. Service-specific rules go in `services/dis-ui/CLAUDE.md`.

## Acceptance criteria

1. `services/dis-ui/` contains a working Vite + React + TypeScript + Tailwind project.
2. `pnpm install` succeeds from a fresh clone.
3. `pnpm dev` starts the dev server; visiting `http://localhost:5173` (or chosen port) loads without console errors.
4. `pnpm build` succeeds (production bundle).
5. `pnpm test` passes (at least one smoke test exists and passes).
6. `pnpm lint` passes (ESLint config + Prettier formatting in place).
7. `pnpm tsc --noEmit` passes (TypeScript strict mode).
8. An `AuthBoundary` component validates a stub JWT, populates an in-memory `AuthSnapshot`, and gates rendering.
9. A `/dev/login` page issues stub JWTs for known personas (at minimum: one TENANT persona, one PLATFORM persona) mirroring `admin-frontend`'s pattern.
10. Visiting `/` while authenticated shows a hello-world page that calls a stubbed `disUiServer.getMe()` client and renders `Hello, {user.email}` (or equivalent).
11. The stubbed client returns a hardcoded response shaped per `docs/ui-engineer-demand-list.md` §1.1 (`GET /me`).
12. `services/dis-ui/CLAUDE.md` is rewritten from placeholder to actual service-specific rules (replacing current 6-line stub).
13. `services/dis-ui/README.md` is rewritten from placeholder to EPE-style block mirroring other services' READMEs.
14. `services/dis-ui/docs/dis-ui-server-contract.md` exists, capturing every dis-ui-server endpoint the UI will consume (initially seeded from `docs/ui-engineer-demand-list.md`).
15. Commit history shows the slice landing via one or a few coherent commits on `main` (or feature branch per repo convention).

## Failure-mode categories

Categories Claude Code must consider during plan mode. Specific failure handling shapes come from plan mode, not from this doc.

**FM1: Stack-setup failures.** Wrong Node version, dependency resolution conflicts, peer-dep warnings escalating to errors, Vite plugin incompatibilities, Tailwind PostCSS pipeline misconfiguration. Plan must specify the exact dependency versions and pin them.

**FM2: TypeScript strict-mode friction.** Strict mode interacts with React patterns, third-party libs, and inferred types. Plan must specify tsconfig settings explicitly and call out any escape hatches.

**FM3: Auth boundary edge cases.** Missing JWT, expired stub JWT, malformed JWT, JWT for unknown persona, token refresh attempts (deferred to later slice but boundary must not crash on absence). Plan must enumerate which of these the boundary handles in slice 19 and which are deferred.

**FM4: Stub-client realism.** Stubbed client must return data shaped exactly as the demand-list `GET /me` describes (`user_id`, `email`, `user_type`, `tenant_id`, `tenant_name`, `permissions`). Plan must specify what happens when a future real `dis-ui-server` returns a different shape (graceful degradation, error toast, etc.) without implementing that fallback in slice 19.

**FM5: Build-target portability.** Per build-guide §5, the same code runs locally and in cloud. Slice 19 only ships local-dev capability, but the codebase must not bake in `localhost` URLs or assume local-only env. Plan must specify how the `dis-ui-server` base URL is configured (env var, runtime config, etc.).

**FM6: Cross-cutting CLAUDE.md hygiene.** Per build-guide §6.4, `services/dis-ui/CLAUDE.md` < 100 lines. Plan must specify what goes in service CLAUDE.md vs README vs the future ui-side dev docs.

## Plan-mode prompts per checkpoint

The slice is sub-divided into 4 checkpoints. Each gets its own plan-mode invocation. Per build-guide §6.1 step 4-7, plan mode returns the file list, libraries used, exact configurations, and test layout; the operator reviews; then execute.

### Checkpoint 1: Scaffold + tooling

**Plan-mode prompt:**

> "Read `services/dis-ui/README.md` and `services/dis-ui/CLAUDE.md` (the current placeholders). Read `docs/slices/slice-19-ui-foundation.md` (this doc). Produce a plan to scaffold `services/dis-ui/` with:
> - Vite + React + TypeScript via `pnpm create vite`
> - Tailwind CSS configured with PostCSS
> - ESLint + Prettier with shared base rules
> - Vitest + React Testing Library wired
> - TypeScript strict mode enabled
> - `package.json` scripts: `dev`, `build`, `lint`, `test`, `tsc`
> - One smoke test that imports `App` and asserts it renders
>
> Return:
> 1. Exact dependency list with pinned versions
> 2. `tsconfig.json` configuration
> 3. `vite.config.ts` configuration
> 4. `tailwind.config.js` configuration
> 5. `eslintrc` configuration
> 6. Directory structure under `services/dis-ui/`
> 7. The smoke test file
> 8. Test commands that prove acceptance criteria 2-7 pass
>
> Do NOT scaffold AuthBoundary, routes, or stub clients in this checkpoint. Foundation only."

### Checkpoint 2: Routing + AuthBoundary + stub JWT

**Plan-mode prompt:**

> "Building on checkpoint 1. Read `docs/ui-engineer-demand-list.md` §1.1 for the `GET /me` shape. Read `admin-frontend`'s `AuthBoundary` if available for reference pattern, otherwise improvise per repo conventions.
>
> Produce a plan to add:
> - react-router setup (route registry, layout shell, route guards)
> - `/dev/login` page with persona picker (at minimum: 1 TENANT persona, 1 PLATFORM persona)
> - Stub JWT generation client-side (jose library; HMAC sign with a hardcoded dev secret; never used in production)
> - `AuthSnapshot` type (user_id, email, user_type, tenant_id, permissions)
> - `AuthBoundary` component that validates JWT, populates AuthSnapshot via React context, redirects unauthenticated users to `/dev/login`
> - JWT storage strategy (localStorage in dev; clear on logout)
> - 3+ tests covering: valid JWT path, missing JWT path, persona-switch path
>
> Return:
> 1. File list with paths under `services/dis-ui/src/`
> 2. JWT payload shape exactly matching `GET /me` demand-list §1.1
> 3. Stub JWT signing approach + the dev secret handling
> 4. Test scenarios
> 5. How AuthBoundary handles each FM3 failure mode
>
> Do NOT call any real backend. Do NOT scaffold dis-ui-server client beyond what AuthBoundary needs."

### Checkpoint 3: Hello-world page + stubbed dis-ui-server client

**Plan-mode prompt:**

> "Building on checkpoints 1-2. Read `docs/ui-engineer-demand-list.md` §1.1 (`GET /me`) and §8-9 (slice 19 minimum endpoint set).
>
> Produce a plan to add:
> - `src/lib/dis-ui-server/client.ts` — typed client for dis-ui-server endpoints
> - `src/lib/dis-ui-server/me.ts` — `getMe()` function that calls `GET /me`
> - `src/lib/dis-ui-server/fixtures.ts` — fixture data shaped per demand-list (slice 19 returns this; real calls in later slice)
> - `src/lib/dis-ui-server/mode.ts` — runtime switch between fixture mode and real-call mode (env var `VITE_DIS_UI_SERVER_MODE=fixture|real`)
> - `src/routes/Home.tsx` — hello-world page using TanStack Query against `getMe()`; renders 'Hello, {user.email}' on success, loading state, error state
> - Wire `/` → Home (gated by AuthBoundary)
> - 3+ tests: fixture-mode renders email correctly; loading state visible; error state visible
>
> Return:
> 1. Exact file list under `src/lib/dis-ui-server/` and `src/routes/`
> 2. Fixture shape + at least 2 fixture personas (1 TENANT, 1 PLATFORM) matching `/dev/login` personas from checkpoint 2
> 3. How the env var switch works (Vite-native)
> 4. Test scenarios
> 5. How the client handles FM4 (real-call mode returning different shape) — graceful behavior, not implementation
>
> Default to fixture mode for slice 19. Real-call mode is wired but untested against any backend."

### Checkpoint 4: CLAUDE.md + README + contract doc rewrites

**Plan-mode prompt:**

> "Building on checkpoints 1-3. Read root `CLAUDE.md`, other `services/*/CLAUDE.md` files for tone and structure, and the existing `services/dis-ui/CLAUDE.md` + `README.md` placeholders.
>
> Produce a plan to rewrite:
>
> 1. `services/dis-ui/CLAUDE.md` (< 100 lines per build-guide §6.4) — service-specific rules covering:
>    - What this service is (frontend; Vite + React + TS; talks only to dis-ui-server)
>    - Hard rules: API calls through `lib/dis-ui-server/*` only, no direct fetch; all routes wrapped in AuthBoundary; no fixtures except those shaped to dis-ui-server-contract.md
>    - Code-quality rules: strict TS, no `any`, tests with code, ESLint clean
>    - Conventions: import order, error patterns, test discipline
>    - References to root CLAUDE.md, architecture.md, decisions.md, slice doc
>    - When uncertain: ask
>
> 2. `services/dis-ui/README.md` — EPE-style overview mirroring `services/dis-ui-server/README.md`:
>    - Purpose (single UI surface for DIS)
>    - Entry/Process/Exit (HTTP request → AuthBoundary → routed page → static SPA assets)
>    - Stack details
>    - Quick start (`pnpm dev`, `pnpm test`, `pnpm build`)
>    - Directory structure
>
> 3. `services/dis-ui/docs/dis-ui-server-contract.md` (NEW) — per-endpoint expected shape per `docs/ui-engineer-demand-list.md`. Initially copies from the demand list; mutates over time as the UI consumes real dis-ui-server.
>
> Return:
> 1. Draft text of all three documents
> 2. Cross-reference list (what each doc links to)
> 3. Confirmation that no rule contradicts root `CLAUDE.md`"

## Out of scope

- All 11 DIS UI screens (those are slice 20)
- Real `dis-ui-server` integration (depends on slice 13)
- Real Customer Master OIDC (per D25, still open contract)
- Production Dockerfile + Cloud Run deploy (later slice)
- CI workflow for `services/dis-ui/` (later slice)
- Playwright e2e (later slice)
- Sidebar persona-aware navigation (later slice; just placeholder shell here)
- Component library choices beyond Tailwind primitives (later slice)
- Internationalization (defer to v2)
- Mobile responsiveness (defer per Surface Map §6.3)

## Companion artifacts

- `docs/ui-engineer-demand-list.md` — full endpoint inventory per UI screen (input for slices 15-17 dis-ui-server work)
- `dis-ui-surface-map.md` (separate artifact, not in repo yet) — 11-screen surface inventory + user journeys (input for slice 20 UI core work)

## References

- `docs/build-guide.md` §6.1 (10-step build loop)
- `docs/build-guide.md` §6.4 (CLAUDE.md hygiene)
- `docs/architecture.md` §4.13 (DIS UI module description)
- `docs/decisions.md` D25 (Customer Master as external dependency)
- `docs/decisions.md` D26 (dis-api as BFF, since renamed to dis-ui-server per commit 4c6a044)
- `services/dis-ui-server/README.md` (BFF EPE block; the backend this UI consumes)
- `CLAUDE.md` (project-wide invariants — same discipline applies to UI work)

## Notes for future slices

- Slice 20 (DIS UI core) will build the 11 screens against this foundation. Phase 1 MVP per Surface Map §7: Auth + Sample Upload + Mapping Review + Mapping Versions (read-only) + Quarantine (tenant) + Audit by trace_id.
- When `dis-ui-server` lands (slice 13), the fixture-mode switch flips to real-call mode; the contract doc is the bridge.
- Doc-nit: build-guide §3 still references `ui/` initialization in slice 19's text. Actual location per latest commit is `services/dis-ui/`. Future doc-cleanup slice should reconcile.
