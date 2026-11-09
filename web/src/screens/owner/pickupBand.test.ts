import { describe, expect, it } from 'vitest'
import { pickupBand } from './pickupBand.ts'

const PICKUP = '2026-11-08T18:30:00+09:00'
const at = (hhmmss: string) => Date.parse(`2026-11-08T${hhmmss}+09:00`)
const TEN_MIN = 10 * 60_000

describe('card color by time left until pickup (ticket 08)', () => {
  it('is relaxed while more than the soon window is left', () => {
    expect(pickupBand({ pickup_time: PICKUP }, at('18:00:00'), TEN_MIN)).toBe('relaxed')
    expect(pickupBand({ pickup_time: PICKUP }, at('18:19:59'), TEN_MIN)).toBe('relaxed')
  })

  it('turns soon once the soon window starts', () => {
    expect(pickupBand({ pickup_time: PICKUP }, at('18:20:00'), TEN_MIN)).toBe('soon')
    expect(pickupBand({ pickup_time: PICKUP }, at('18:29:59'), TEN_MIN)).toBe('soon')
  })

  it('turns late at the pickup time and after', () => {
    expect(pickupBand({ pickup_time: PICKUP }, at('18:30:00'), TEN_MIN)).toBe('late')
    expect(pickupBand({ pickup_time: PICKUP }, at('19:10:00'), TEN_MIN)).toBe('late')
  })

  it('uses the configured window', () => {
    expect(pickupBand({ pickup_time: PICKUP }, at('18:20:00'), 5 * 60_000)).toBe('relaxed')
    expect(pickupBand({ pickup_time: PICKUP }, at('18:25:00'), 5 * 60_000)).toBe('soon')
  })

  it('keeps the default color for a card without a pickup time (handoff etc.)', () => {
    expect(pickupBand({}, at('18:00:00'), TEN_MIN)).toBe('none')
    expect(pickupBand({ pickup_time: '' }, at('18:00:00'), TEN_MIN)).toBe('none')
    expect(pickupBand({ pickup_time: null }, at('18:00:00'), TEN_MIN)).toBe('none')
    expect(pickupBand({ pickup_time: 'not a time' }, at('18:00:00'), TEN_MIN)).toBe('none')
  })
})
