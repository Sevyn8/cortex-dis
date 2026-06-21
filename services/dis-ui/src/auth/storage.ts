// Dev-mode token storage. The stub JWT is kept in localStorage so a reload keeps
// the session; logout clears it. Real-mode token storage (httpOnly cookie vs.
// in-memory) is a Customer Master integration decision deferred to a later slice.

const TOKEN_KEY = 'dis-ui.dev.authToken'

export function readToken(): string | null {
  return localStorage.getItem(TOKEN_KEY)
}

export function writeToken(token: string): void {
  localStorage.setItem(TOKEN_KEY, token)
}

export function clearToken(): void {
  localStorage.removeItem(TOKEN_KEY)
}
