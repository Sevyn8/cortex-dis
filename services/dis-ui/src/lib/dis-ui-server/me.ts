import { useQuery } from '@tanstack/react-query'

import type { AuthSnapshot } from '../../auth/AuthSnapshot'
import { ME_FIXTURES } from './fixtures'
import { SERVER_MODE } from './mode'
import type { MeResponse } from './types'

// Returns the signed-in user's display profile.
//
// Fixture mode (default): returns the inlined fixture for the current user (keyed
// by the token sub / userId) and makes no network call.
//
// Real mode: OPEN. getMe models a future dis-ui-server -> Customer Master profile
// call (email / name / tenant_name are not token claims; see types.ts MeResponse).
// Architecture 4.17 lists no GET /me handler; whether dis-ui-server exposes one is
// pending Sanjeev and slice 13. Nothing here depends on the answer, so the real
// branch throws loudly rather than guessing.
export async function getMe(snapshot: AuthSnapshot): Promise<MeResponse> {
  if (SERVER_MODE === 'real') {
    throw new Error(
      'real-mode getMe() is not implemented in slice 19/20; the GET /me profile call ' +
        'is an open question for slice 13',
    )
  }
  const fixture = ME_FIXTURES[snapshot.userId]
  if (fixture === undefined) {
    throw new Error(`no fixture MeResponse for userId ${snapshot.userId}`)
  }
  return fixture
}

export function useMe(snapshot: AuthSnapshot | null) {
  return useQuery({
    queryKey: ['dis-ui-server', 'me', snapshot?.userId ?? 'anonymous'],
    queryFn: () => getMe(snapshot as AuthSnapshot),
    enabled: snapshot !== null,
    staleTime: Infinity,
    retry: false,
  })
}
