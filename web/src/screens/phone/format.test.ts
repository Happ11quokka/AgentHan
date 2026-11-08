import { afterEach, describe, expect, it, vi } from 'vitest'
import { pickupLabel } from './format.ts'

afterEach(() => {
  vi.unstubAllEnvs()
})

describe('pickupLabel', () => {
  it('shows the pickup time in the store time zone (KST) whatever the device zone is', () => {
    vi.stubEnv('TZ', 'UTC') // 기기가 UTC여도
    expect(new Date('2026-11-08T18:30:00+09:00').getHours()).toBe(9) // 기기 시각은 9시
    expect(pickupLabel('2026-11-08T18:30:00+09:00')).toBe('오후 6:30')
  })
})
