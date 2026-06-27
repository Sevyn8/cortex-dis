import { afterEach, beforeEach, describe, expect, it } from 'vitest'

import { clearToken, writeToken } from '../../auth/storage'
import { getAccessToken, setAccessTokenGetter } from './accessToken'

// The in-memory access-token seam. A registered getter (Auth0 mode wires it to
// getAccessTokenSilently) is preferred and takes precedence over any stored token,
// so Auth0 never reads localStorage. With no getter (dev-stub / direct-call tests)
// it falls back to the localStorage stub. Neither present is a loud error.
describe('access-token seam', () => {
  beforeEach(() => {
    setAccessTokenGetter(null)
    clearToken()
  })
  afterEach(() => {
    setAccessTokenGetter(null)
    clearToken()
  })

  it('uses the registered getter (the SDK token in Auth0 mode)', async () => {
    setAccessTokenGetter(() => Promise.resolve('sdk-token'))
    expect(await getAccessToken()).toBe('sdk-token')
  })

  it('prefers the getter over any stored token (Auth0 never reads localStorage)', async () => {
    writeToken('stored-stub-token')
    setAccessTokenGetter(() => Promise.resolve('sdk-token'))
    expect(await getAccessToken()).toBe('sdk-token')
  })

  it('falls back to the stored token when no getter is registered (dev-stub)', async () => {
    writeToken('stored-stub-token')
    expect(await getAccessToken()).toBe('stored-stub-token')
  })

  it('throws when neither a getter nor a stored token is present', async () => {
    await expect(getAccessToken()).rejects.toThrow(/no access token/i)
  })

  it('throws when the getter yields an empty token', async () => {
    setAccessTokenGetter(() => Promise.resolve(''))
    await expect(getAccessToken()).rejects.toThrow(/no access token/i)
  })
})
