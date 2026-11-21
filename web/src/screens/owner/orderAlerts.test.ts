import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { OrderAlerts } from './orderAlerts.ts'

const EVERY = 5_000

function order(id: string, status: 'new' | 'accepted' = 'new') {
  return { id, status }
}

function setup(repeatEveryMs = EVERY) {
  let rings = 0
  const repeats: string[][] = []
  const sound = { on: true }
  const alerts = new OrderAlerts({
    ring: () => (rings += 1),
    soundOn: () => sound.on,
    onRepeat: (ids) => repeats.push(ids),
    repeatEveryMs,
  })
  return { alerts, rings: () => rings, repeats, sound }
}

describe('new order alert and repeat while approvals wait (order approval ticket 08)', () => {
  beforeEach(() => vi.useFakeTimers())
  afterEach(() => vi.useRealTimers())

  it('rings on arrival and keeps ringing every few seconds while a card waits', () => {
    const { alerts, rings, repeats } = setup()
    alerts.sync([order('a')])
    expect(rings()).toBe(1)
    vi.advanceTimersByTime(EVERY - 1)
    expect(rings()).toBe(1)
    vi.advanceTimersByTime(1)
    expect(rings()).toBe(2)
    vi.advanceTimersByTime(3 * EVERY)
    expect(rings()).toBe(5)
    expect(repeats).toEqual([['a'], ['a'], ['a'], ['a']])
  })

  it('stops when the owner decides the last waiting card', () => {
    const { alerts, rings } = setup()
    alerts.sync([order('a'), order('b')])
    vi.advanceTimersByTime(EVERY)
    alerts.sync([order('a', 'accepted'), order('b')])
    vi.advanceTimersByTime(EVERY)
    expect(rings()).toBe(3)
    alerts.sync([order('a', 'accepted'), { id: 'b', status: 'rejected' }])
    vi.advanceTimersByTime(10 * EVERY)
    expect(rings()).toBe(3)
  })

  it('rings at once when the customer changed the order and a new request lands on the same card (review 6)', () => {
    const { alerts, rings } = setup()
    alerts.sync([{ id: 'a', status: 'new', request: 1 }])
    alerts.sync([{ id: 'a', status: 'new', request: 1, withdrawn: 'changed' }]) // 손님이 고치는 중: 반복 멈춤
    expect(rings()).toBe(1)
    alerts.sync([{ id: 'a', status: 'new', request: 2 }]) // 같은 카드에 새 요청
    expect(rings()).toBe(2)
    alerts.sync([{ id: 'a', status: 'new', request: 2 }]) // 다시 받은 snapshot은 또 울리지 않는다
    expect(rings()).toBe(2)
  })

  it('stops when the card goes away or expires', () => {
    const { alerts, rings } = setup()
    alerts.sync([order('a')])
    alerts.sync([{ id: 'a', status: 'expired' }])
    alerts.sync([])
    vi.advanceTimersByTime(10 * EVERY)
    expect(rings()).toBe(1)
  })

  it('stops while the customer changes or cancels the order', () => {
    const { alerts, rings } = setup()
    alerts.sync([order('a')])
    alerts.sync([{ id: 'a', status: 'new', withdrawn: 'changed' }])
    vi.advanceTimersByTime(10 * EVERY)
    expect(rings()).toBe(1)
  })

  it('starts again when a new card arrives after everything was handled', () => {
    const { alerts, rings, repeats } = setup()
    alerts.sync([order('a')])
    alerts.sync([order('a', 'accepted')])
    vi.advanceTimersByTime(10 * EVERY)
    alerts.sync([order('a', 'accepted'), order('b')])
    expect(rings()).toBe(2)
    vi.advanceTimersByTime(EVERY)
    expect(rings()).toBe(3)
    expect(repeats).toEqual([['b']])
  })

  it('rings once per repeat however many cards wait', () => {
    const { alerts, rings, repeats } = setup()
    alerts.sync([order('a'), order('b')])
    vi.advanceTimersByTime(EVERY / 2)
    alerts.sync([order('a'), order('b'), order('c')])
    expect(rings()).toBe(2)
    vi.advanceTimersByTime(EVERY / 2)
    expect(rings()).toBe(3)
    expect(repeats).toEqual([['a', 'b', 'c']])
  })

  it('does not ring again on arrival for an order it has already seen (reconnect snapshot)', () => {
    const { alerts, rings } = setup()
    alerts.sync([order('a')])
    alerts.sync([order('a')])
    alerts.sync([order('a')])
    expect(rings()).toBe(1)
  })

  it('stays quiet for an order that is already accepted when first seen', () => {
    const { alerts, rings } = setup()
    alerts.sync([order('a', 'accepted')])
    vi.advanceTimersByTime(10 * EVERY)
    expect(rings()).toBe(0)
  })

  it('rings once for an auto-accepted order without repeating (order approval ticket 02)', () => {
    const { alerts, rings } = setup()
    alerts.sync([{ id: 'a', status: 'accepted', auto_accepted: true }])
    alerts.sync([{ id: 'a', status: 'accepted', auto_accepted: true }])
    vi.advanceTimersByTime(10 * EVERY)
    expect(rings()).toBe(1)
  })

  it('does not ring with the sound off but keeps the repeat for the blink', () => {
    const { alerts, rings, repeats, sound } = setup()
    sound.on = false
    alerts.sync([order('a')])
    vi.advanceTimersByTime(2 * EVERY)
    expect(rings()).toBe(0)
    expect(repeats).toEqual([['a'], ['a']])
    sound.on = true
    vi.advanceTimersByTime(EVERY)
    expect(rings()).toBe(1)
  })

  it('uses the configured repeat interval', () => {
    const { alerts, rings } = setup(2_000)
    alerts.sync([order('a')])
    vi.advanceTimersByTime(2_000)
    expect(rings()).toBe(2)
  })

  it('stops repeating on close', () => {
    const { alerts, rings } = setup()
    alerts.sync([order('a'), order('b')])
    alerts.close()
    vi.advanceTimersByTime(10 * EVERY)
    expect(rings()).toBe(1)
  })
})
