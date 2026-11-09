import { afterEach, describe, expect, it, vi } from 'vitest'
import { endReasonLabel, pickupLabel } from './format.ts'

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

describe('endReasonLabel', () => {
  it('says why the server ended the call (call.ended reason, review S13)', () => {
    expect(endReasonLabel('confirmed')).toBe('주문이 확정됐어요')
    expect(endReasonLabel('handoff')).toBe('사장님께 넘겼어요')
    expect(endReasonLabel('cancelled')).toBe('주문을 취소했어요')
    expect(endReasonLabel('no_input')).toBe('말씀이 없어 끊었어요')
    expect(endReasonLabel('done')).toBeNull() // 모르는 이유는 따로 적지 않는다
  })
})
