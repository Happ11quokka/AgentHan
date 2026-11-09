import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { OrderAlerts } from './orderAlerts.ts'

const MIN = 60_000

function order(id: string, status: 'new' | 'accepted' = 'new') {
  return { id, status }
}

function setup(remindAfterMs = MIN) {
  const arrived: string[][] = []
  const reminded: string[] = []
  const alerts = new OrderAlerts({
    onArrive: (ids) => arrived.push(ids),
    onRemind: (id) => reminded.push(id),
    remindAfterMs,
  })
  return { alerts, arrived, reminded }
}

describe('new order alerts and the one-minute reminder (ticket 08)', () => {
  beforeEach(() => vi.useFakeTimers())
  afterEach(() => vi.useRealTimers())

  it('rings once on a new order and once more if it is still not accepted after a minute', () => {
    const { alerts, arrived, reminded } = setup()
    alerts.sync([order('a')])
    expect(arrived).toEqual([['a']])
    vi.advanceTimersByTime(MIN - 1)
    expect(reminded).toEqual([])
    vi.advanceTimersByTime(1)
    expect(reminded).toEqual(['a'])
    vi.advanceTimersByTime(10 * MIN)
    expect(reminded).toEqual(['a'])
  })

  it('cancels the reminder when the owner accepts the order', () => {
    const { alerts, reminded } = setup()
    alerts.sync([order('a')])
    vi.advanceTimersByTime(30_000)
    alerts.sync([order('a', 'accepted')])
    vi.advanceTimersByTime(10 * MIN)
    expect(reminded).toEqual([])
  })

  it('cancels the reminder when the card goes away', () => {
    const { alerts, reminded } = setup()
    alerts.sync([order('a')])
    alerts.sync([])
    vi.advanceTimersByTime(10 * MIN)
    expect(reminded).toEqual([])
  })

  it('does not ring again for an order it has already seen (reconnect snapshot)', () => {
    const { alerts, arrived, reminded } = setup()
    alerts.sync([order('a')])
    vi.advanceTimersByTime(20_000)
    alerts.sync([order('a')])
    alerts.sync([order('a')])
    expect(arrived).toEqual([['a']])
    vi.advanceTimersByTime(40_000)
    expect(reminded).toEqual(['a'])
  })

  it('rings once for several orders arriving together and reminds each on its own clock', () => {
    const { alerts, arrived, reminded } = setup()
    alerts.sync([order('a'), order('b')])
    vi.advanceTimersByTime(30_000)
    alerts.sync([order('a'), order('b'), order('c')])
    expect(arrived).toEqual([['a', 'b'], ['c']])
    vi.advanceTimersByTime(30_000)
    expect(reminded).toEqual(['a', 'b'])
    vi.advanceTimersByTime(30_000)
    expect(reminded).toEqual(['a', 'b', 'c'])
  })

  it('stays quiet for an order that is already accepted when first seen', () => {
    const { alerts, arrived, reminded } = setup()
    alerts.sync([order('a', 'accepted')])
    vi.advanceTimersByTime(10 * MIN)
    expect(arrived).toEqual([])
    expect(reminded).toEqual([])
  })

  it('uses the configured reminder delay', () => {
    const { alerts, reminded } = setup(30_000)
    alerts.sync([order('a')])
    vi.advanceTimersByTime(30_000)
    expect(reminded).toEqual(['a'])
  })

  it('cancels every pending reminder on close', () => {
    const { alerts, reminded } = setup()
    alerts.sync([order('a'), order('b')])
    alerts.close()
    vi.advanceTimersByTime(10 * MIN)
    expect(reminded).toEqual([])
  })
})
