// Which auth path the app runs. Auth0 is the default and the ONLY option a
// production build can ever select; the dev-stub flow is selectable only in a
// dev build AND when explicitly requested via VITE_AUTH_MODE=dev-stub.
//
// The guard is deliberately two-factor (build kind AND explicit opt-in) so a
// stray env value can never enable the local stub in a deployed bundle: the
// stub mints/verifies tokens client-side against a hardcoded secret and must
// never be reachable outside local dev.

export type AuthMode = 'auth0' | 'dev-stub'

// Pure resolver (testable without touching import.meta.env): dev-stub requires
// BOTH a dev build and an explicit opt-in; everything else is auth0.
export function resolveAuthMode(env: { mode: string | undefined; isDev: boolean }): AuthMode {
  if (env.isDev && env.mode === 'dev-stub') {
    return 'dev-stub'
  }
  return 'auth0'
}

// Lazy read (mirrors mode.ts): reads import.meta.env at CALL time so tests can
// flip VITE_AUTH_MODE via vi.stubEnv. Vite still inlines the value per build.
export function getAuthMode(): AuthMode {
  return resolveAuthMode({
    mode: import.meta.env.VITE_AUTH_MODE,
    isDev: import.meta.env.DEV,
  })
}
