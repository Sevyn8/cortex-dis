# Slice 14c: CORS for browser-served dis-ui (dis-ui-server)

Small slice. Add config-driven CORS to dis-ui-server so the browser-served `dis-ui` SPA (its
own dev origin) can call the API. The 14b endpoints work from curl already (curl ignores
CORS); this unblocks the browser path only. Server-side only; `services/dis-ui` is READ-ONLY.

## Depends on

- Slice 13a (the FastAPI app, config, the `/api/v1` mount) and Slice 14b (the five endpoints
  the SPA calls). Both built and pushed.

## Goal

After this slice, `dis-ui` running on its dev origin can make `GET`/`POST`/`PATCH` calls to
dis-ui-server in a browser without the browser blocking them on same-origin policy. The
allowed origin is an explicit config value, not a wildcard, and is environment-driven so a
permissive dev setting cannot leak into a deployed environment.

## Task

Add FastAPI `CORSMiddleware` to the app, driven by a new config field. Decompose in plan
mode and show:

0. **Plan-mode grounding (ERROR, not skip).**
   - Confirm the `dis-ui` dev origin live from `services/dis-ui`: the dev-server host and port
     (do not assume 5173) and the env var the SPA uses for the API base URL / the
     fixture-vs-live mode flag. State the exact origin string the middleware must allow.
   - Confirm whether the auth flow uses the `Authorization: Bearer` header only (no cookies),
     since that decides `allow_credentials`.

1. **Config.** Add a field (e.g. `cors_allowed_origins`, a list) to the service config,
   defaulting to the confirmed `dis-ui` dev origin, populated from the environment the same
   way every other config field is. No wildcard default.

2. **Middleware.** Register `CORSMiddleware` with: the configured origin(s); the methods the
   five endpoints use (`GET`, `POST`, `PATCH`, and `OPTIONS` for preflight); the headers the
   SPA sends (`Authorization`, `Content-Type`); `allow_credentials` per the Task 0 finding
   (likely `False`, since auth is a Bearer header, not cookies). Never combine
   `allow_origins=["*"]` with `allow_credentials=True` (invalid per spec and insecure).

3. **Tests (same commit).** A preflight `OPTIONS` from the allowed origin returns the
   `Access-Control-Allow-Origin` header for that origin; a request from a disallowed origin
   does not receive it. The middleware does not alter the existing endpoints' responses or the
   health probes.

## What this slice does NOT do

No change to `services/dis-ui` (READ-ONLY, absolute; all frontend change is Amit's). No new
endpoints, no auth change, no DDL. No production-origin configuration beyond making the field
environment-driven (the deployed origins are set per environment, not hardcoded here).

## Open questions for plan mode

1. The exact `dis-ui` dev origin (Task 0).
2. `allow_credentials` true/false, decided by the auth-header-vs-cookie finding.
3. Whether more than one origin needs allowing in dev (e.g. a separate preview port); a list
   field accommodates it either way.

## Acceptance criteria

- `dis-ui` on its confirmed dev origin can call the five endpoints in a browser; preflight
  succeeds for the allowed origin (test-proven).
- The allowed origin is a config field, environment-driven, with no wildcard default.
- A disallowed origin is not granted CORS headers (test-proven).
- Health probes and the existing endpoint responses are unchanged.
- `services/dis-ui` is unmodified.
- `make check` / lint / mypy clean; tests in the same commit.
