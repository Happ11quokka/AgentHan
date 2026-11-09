import { describe, expect, it } from 'vitest'
import {
  type OrderCard,
  type OwnerAction,
  type OwnerState,
  canAccept,
  initialOwnerState,
  ownerReducer,
} from './ownerState.ts'

function run(...actions: OwnerAction[]): OwnerState {
  return actions.reduce(ownerReducer, initialOwnerState)
}

function card(id: string, status: OrderCard['status'] = 'new'): OrderCard {
  return {
    id,
    status,
    received_at: '2026-11-08T16:02:00+09:00',
    pickup_time: '2026-11-08T18:30:00+09:00',
    pickup_label: '18:30',
    lines: [{ menu_id: 'm01', name_ko: '메도빅', quantity: 1, options: [], request: '', subtotal: 7500 }],
    total: 7500,
    total_label: '7,500₩',
  }
}

const snapshot = (...orders: OrderCard[]): OwnerAction => ({ type: 'snapshot', data: { orders } })
const update = (order: OrderCard): OwnerAction => ({ type: 'order.update', data: order })
const ids = (s: OwnerState) => s.orders.map((o) => o.id)

describe('owner screen state (ticket 01)', () => {
  it('waits for the owner to open the store before connecting', () => {
    expect(initialOwnerState.connection).toBe('closed')
    expect(run({ type: 'open' }).connection).toBe('connecting')
    expect(run({ type: 'open' }, snapshot()).connection).toBe('live')
  })

  it('replaces every card with a snapshot', () => {
    const s = run(snapshot(card('a'), card('b')), snapshot(card('c')))
    expect(ids(s)).toEqual(['c'])
  })

  it('puts a new order below the earlier ones', () => {
    const s = run(snapshot(card('a')), update(card('b')), update(card('c')))
    expect(ids(s)).toEqual(['a', 'b', 'c'])
  })

  it('overwrites a card in place when the same order changes', () => {
    const s = run(snapshot(card('a'), card('b')), update(card('a', 'accepted')))
    expect(ids(s)).toEqual(['a', 'b'])
    expect(s.orders[0].status).toBe('accepted')
  })

  it('offers the accept button only on a new order', () => {
    const s = run(snapshot(card('a'), card('b', 'accepted')))
    expect(s.orders.map(canAccept)).toEqual([true, false])
  })

  it('shows a dropped stream as reconnecting until the next snapshot', () => {
    const s = run({ type: 'open' }, snapshot(card('a')), { type: 'lost' })
    expect(s.connection).toBe('connecting')
    expect(ids(s)).toEqual(['a'])
    expect(ownerReducer(s, snapshot(card('a'))).connection).toBe('live')
  })

  it('stops on a refused stream (wrong key) until the owner opens again', () => {
    const s = run({ type: 'open' }, { type: 'failed' })
    expect(s.connection).toBe('failed')
    expect(ownerReducer(s, { type: 'open' }).connection).toBe('connecting')
  })
})
