import { describe, expect, it } from 'vitest'

import { resolveAuthMode } from './authMode'

// The dev-stub guard: dev-stub is selectable ONLY when BOTH a dev build AND the
// explicit opt-in are present. Every other combination resolves to auth0, so a
// production build (isDev false) can never select the stub regardless of env.
describe('resolveAuthMode', () => {
  it('selects dev-stub only in a dev build with the explicit opt-in', () => {
    expect(resolveAuthMode({ mode: 'dev-stub', isDev: true })).toBe('dev-stub')
  })

  it('refuses dev-stub in a production build even when requested', () => {
    expect(resolveAuthMode({ mode: 'dev-stub', isDev: false })).toBe('auth0')
  })

  it('defaults to auth0 when no mode is set', () => {
    expect(resolveAuthMode({ mode: undefined, isDev: true })).toBe('auth0')
    expect(resolveAuthMode({ mode: undefined, isDev: false })).toBe('auth0')
  })

  it('treats an explicit auth0 / unknown value as auth0', () => {
    expect(resolveAuthMode({ mode: 'auth0', isDev: true })).toBe('auth0')
    expect(resolveAuthMode({ mode: 'something-else', isDev: true })).toBe('auth0')
  })
})
