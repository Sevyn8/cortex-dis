import { readToken } from '../../auth/storage'

// The in-memory access-token seam for the dis-ui-server request layer. Every
// authed call reads the bearer here via getAccessToken(); the auth integration
// registers HOW to obtain it via setAccessTokenGetter at app init (mirrors the
// backend's set_verifier injection seam).
//
// In Auth0 mode the registered getter is getAccessTokenSilently (the SDK holds
// the token IN MEMORY; nothing is written to localStorage). The Auth0 bridge
// registers it DURING RENDER, before any child mounts, so the getter is always
// present before the first authed request fires.
//
// The storage fallback (readToken) is reached ONLY when no getter is registered:
// dev-stub mode (the stub token lives in localStorage) and direct-call unit
// tests. In Auth0 mode the getter is always set, so the fallback never runs and
// the in-memory invariant holds.

type AccessTokenGetter = () => Promise<string>

let accessTokenGetter: AccessTokenGetter | null = null

export function setAccessTokenGetter(getter: AccessTokenGetter | null): void {
  accessTokenGetter = getter
}

export async function getAccessToken(): Promise<string> {
  if (accessTokenGetter !== null) {
    const token = await accessTokenGetter()
    if (token.length === 0) {
      throw new Error('no access token: cannot call dis-ui-server (sign in first)')
    }
    return token
  }
  // No getter wired: dev-stub / direct-call fallback to the localStorage token.
  const stored = readToken()
  if (stored === null || stored.length === 0) {
    throw new Error('no access token: cannot call dis-ui-server (sign in first)')
  }
  return stored
}
