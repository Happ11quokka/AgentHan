import { describe, expect, it } from 'vitest'
import { pickupLabel } from './format.ts'

describe('pickupLabel', () => {
  it('shows the pickup time in the store time zone (KST) whatever the device zone is', () => {
    const before = process.env.TZ
    process.env.TZ = 'UTC' // 기기가 UTC여도
    try {
      expect(pickupLabel('2026-11-08T18:30:00+09:00')).toBe('오후 6:30')
    } finally {
      process.env.TZ = before
    }
  })
})
