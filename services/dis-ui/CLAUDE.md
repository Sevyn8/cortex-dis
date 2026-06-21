# services/dis-ui - Claude Code Context

Loaded when Claude Code works in `services/dis-ui/`. Service-specific rules; root `CLAUDE.md` applies first.

## What this service is

The DIS UI: the single frontend SPA for DIS, hosting tenant and Ithina ops surfaces. Vite + React + TypeScript. It talks only to `dis-ui-server`; it never calls other backends (Identity, Streaming Consumer, etc.) directly.

For the EPE block, stack, and directory layout, see `README.md`. For the backend contract the UI consumes, see `docs/dis-ui-server-contract.md`. For the current build slice, see `docs/slices/`.

**Status:** v1.0 (foundation; slice 19).

## Rules specific to this service

- Backend access goes only through `src/lib/dis-ui-server/*`. No `fetch` or HTTP client anywhere else, and no calls to data-plane services. The UI calls one backend: dis-ui-server (decisions.md D17/D26).
- Every route except `/dev/login` is wrapped in `AuthBoundary`. `/dev/login` is the only public route.
- Auth in slice 19 is an HMAC-signed stub JWT; real Customer Master OIDC is deferred (decisions.md D25). `src/auth/verifyToken.ts` is the single swap seam to JWKS verification. Keep token logic there; do not scatter it.
- The data source is selected by `VITE_DIS_UI_SERVER_MODE` (default `fixture`; `real` is wired but not implemented this slice). Fixtures live in `src/lib/dis-ui-server/fixtures.ts` and must be shaped to `docs/dis-ui-server-contract.md`. All shapes are PROVISIONAL pending the demand list and D25.
- `src/test/setup.ts` is load-bearing: it realigns jsdom globals (Uint8Array/ArrayBuffer/TextEncoder) and crypto.subtle so jose works under jsdom. Do not remove or weaken it. Tests that use query hooks wrap in a `QueryClientProvider`.

## Code quality

- Strict TypeScript; no `any`. Tests land in the same commit as the code they cover.
- `pnpm lint` (ESLint) and Prettier are clean. No em-dashes in any repo file.
- Precise naming: "DIS" is the system, "DIS UI" is this frontend, "dis-ui-server" is the BFF.

## Conventions

- Import order: third-party first, then first-party (`../auth`, `../lib`), one blank line between groups. No wildcard imports.
- Errors carry context; no swallowed exceptions. Prefer loud failures over silent fallbacks (real mode throws when its base URL is missing, rather than defaulting).
- One concern per module; co-locate `*.test.ts(x)` with the code under test.

## Git discipline

- Claude Code does NOT push. The operator reviews and pushes. A `pre-push` hook enforces this; the operator authorizes one push with `touch "$(git rev-parse --git-dir)/ALLOW_PUSH"`.
- No `Co-Authored-By` trailers. Commits are coherent and checkpoint-scoped.

## When uncertain

Ask. Surface the gap; do not guess, especially on auth, RBAC, and contract shapes. Consult the slice doc first, then architecture, then this file.

## References

- Root `CLAUDE.md` - project-wide invariants.
- `docs/architecture.md` section 4.13 (DIS UI), section 4.17 (dis-ui-server).
- `docs/decisions.md` D25 (Customer Master as external dependency), D26 (BFF), D36 (CSV upload split).
- `docs/slices/slice-19-ui-foundation.md` - the current slice.
- `services/dis-ui-server/README.md` - the backend this UI consumes.
- `README.md` and `docs/dis-ui-server-contract.md` - this service's EPE block and the backend contract.
