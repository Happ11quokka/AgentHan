import { describe, expect, it } from 'vitest'
import {
  type CallCard,
  type OrderCard,
  type OwnerAction,
  type OwnerState,
  canAccept,
  canDismiss,
  followUpCalls,
  initialOwnerState,
  liveCalls,
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
    lines: [
      {
        menu_id: 'm01',
        name_ko: '메도빅',
        name_owner: 'Медовик',
        quantity: 1,
        options: [],
        options_owner: [],
        request: '',
        request_owner: '',
        badges: [],
        subtotal: 7500,
      },
    ],
    total: 7500,
    total_label: '7,500₩',
  }
}

const snapshot = (...orders: OrderCard[]): OwnerAction => ({
  type: 'snapshot',
  data: { orders, calls: [] },
})
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

function call(id: string, status: CallCard['status'] = 'live', reason: CallCard['reason'] = ''): CallCard {
  return {
    id,
    status,
    started_at: '2026-11-08T16:02:00+09:00',
    started_label: '16:02',
    reason,
    lines: [],
    total: 0,
    total_label: '0₩',
    heard: [],
    heard_owner: [],
  }
}

const callUpdate = (c: CallCard): OwnerAction => ({ type: 'call.update', data: c })
const withCalls = (...calls: CallCard[]): OwnerAction => ({ type: 'snapshot', data: { orders: [], calls } })
const callIds = (cards: CallCard[]) => cards.map((c) => c.id)

describe('owner screen call cards (ticket 02)', () => {
  it('puts a call in progress on the top row and follows its cart in place', () => {
    const one = { ...call('a'), lines: card('a').lines, total: 7500, total_label: '7,500₩' }
    const s = run(withCalls(), callUpdate(call('a')), callUpdate(call('b')), callUpdate(one))
    expect(callIds(liveCalls(s))).toEqual(['a', 'b'])
    expect(liveCalls(s)[0].total_label).toBe('7,500₩')
    expect(followUpCalls(s)).toEqual([])
  })

  it('drops the call card when the call became an order card', () => {
    const s = run(withCalls(call('a')), callUpdate(call('a', 'ordered')), update(card('a')))
    expect(liveCalls(s)).toEqual([])
    expect(ids(s)).toEqual(['a'])
  })

  it('keeps a handoff and a grey missed card until the owner closes them', () => {
    const s = run(
      withCalls(call('a'), call('b')),
      callUpdate(call('a', 'handoff', 'handoff')),
      callUpdate(call('b', 'missed', 'hangup')),
    )
    expect(liveCalls(s)).toEqual([])
    expect(followUpCalls(s).map((c) => [c.id, c.status, c.reason])).toEqual([
      ['a', 'handoff', 'handoff'],
      ['b', 'missed', 'hangup'],
    ])
    expect(followUpCalls(s).map(canDismiss)).toEqual([true, true])
    const closed = ownerReducer(s, callUpdate(call('a', 'closed', 'handoff')))
    expect(callIds(followUpCalls(closed))).toEqual(['b'])
  })

  it('offers the close button only on handoff and missed cards', () => {
    expect(canDismiss(call('a'))).toBe(false)
    expect(canDismiss(call('a', 'missed', 'error'))).toBe(true)
  })

  it('replaces the call cards with a snapshot', () => {
    const s = run(withCalls(call('a')), withCalls(call('b', 'missed', 'no_input')))
    expect(callIds(liveCalls(s))).toEqual([])
    expect(callIds(followUpCalls(s))).toEqual(['b'])
  })
})

describe('owner screen translations (ticket 03)', () => {
  it('shows the translation when the same order card comes again translated', () => {
    const korean = card('a')
    korean.lines = [{ ...korean.lines[0], request: '고수는 빼 주세요', badges: ['remove'] }]
    const translated = {
      ...korean,
      lines: [{ ...korean.lines[0], request_owner: 'кинза Уберите, пожалуйста.' }],
    }
    const s = run(snapshot(korean, card('b')), update(translated))
    expect(ids(s)).toEqual(['a', 'b'])
    expect(s.orders[0].lines[0]).toMatchObject({
      request: '고수는 빼 주세요',
      request_owner: 'кинза Уберите, пожалуйста.',
      badges: ['remove'],
    })
  })

  it('shows the translated last customer lines on the handoff card', () => {
    const handoff = { ...call('a', 'handoff', 'handoff'), heard: ['사장님이랑 통화할래요'], heard_owner: [''] }
    const translated = { ...handoff, heard_owner: ['Я хочу поговорить с хозяином.'] }
    const s = run(withCalls(handoff), callUpdate(translated))
    expect(followUpCalls(s).map((c) => [c.heard, c.heard_owner])).toEqual([
      [['사장님이랑 통화할래요'], ['Я хочу поговорить с хозяином.']],
    ])
  })
})
