import { cn } from './utils'

describe('cn', () => {
  it('dedupes conflicting tailwind utilities (last wins)', () => {
    expect(cn('p-2', 'p-4')).toBe('p-4')
  })

  it('drops falsy values and joins the rest', () => {
    const disabled: boolean = false
    expect(cn('a', disabled && 'b', undefined, 'c')).toBe('a c')
  })
})
