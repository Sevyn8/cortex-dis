import type { AuthSnapshot } from '../../auth/AuthSnapshot'
import {
  __resetNotificationsFixture,
  getNotifications,
  getUnreadCount,
  markAllNotificationsRead,
  markNotificationRead,
} from './notifications'

const tenant: AuthSnapshot = {
  userId: 'u_acmeuser0001',
  tenantId: 't_acme9k2l1mn4',
  storeId: 's_acme0001a4b7',
  roles: ['dis:read'],
}
const otherTenant: AuthSnapshot = { ...tenant, tenantId: 't_nofixtures01' }

describe('notifications fixtures (fixture mode)', () => {
  beforeEach(() => {
    __resetNotificationsFixture()
  })

  it('filters: all -> 4, unread -> 3, errors -> 1 (the error one)', async () => {
    expect((await getNotifications(tenant, 'all')).length).toBe(4)
    expect((await getNotifications(tenant, 'unread')).length).toBe(3)
    const errors = await getNotifications(tenant, 'errors')
    expect(errors.length).toBe(1)
    expect(errors[0].severity).toBe('error')
  })

  it('reports the unread count', async () => {
    expect(await getUnreadCount(tenant)).toEqual({ unread: 3 })
  })

  it('mark-read lowers the unread count by one', async () => {
    await markNotificationRead(tenant, 'ntf_0001')
    expect(await getUnreadCount(tenant)).toEqual({ unread: 2 })
  })

  it('mark-all-read zeroes the unread count', async () => {
    await markAllNotificationsRead(tenant)
    expect(await getUnreadCount(tenant)).toEqual({ unread: 0 })
    expect((await getNotifications(tenant, 'unread')).length).toBe(0)
  })

  it('is tenant-scoped: another tenant sees nothing', async () => {
    expect(await getNotifications(otherTenant, 'all')).toEqual([])
    expect(await getUnreadCount(otherTenant)).toEqual({ unread: 0 })
  })
})
