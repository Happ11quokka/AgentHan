// 주문 승인 티켓 02: 확인 요청 카드 (서버 `hub.py` `open_approval`·`accept`·자동 확정).
// 리듀서는 카드를 덮어쓰기만 하고, 버튼·결과 줄·카운트다운은 순수 함수가 정한다.
import { describe, expect, it } from 'vitest'
import {
  type OrderCard,
  type OwnerAction,
  type OwnerState,
  approvalClock,
  approvalsWaiting,
  canAccept,
  initialOwnerState,
  orderOutcome,
  ownerReducer,
  storeNow,
} from './ownerState.ts'
import { OWNER_TEXT } from './ownerText.ts'

const REQUESTED = '2026-11-08T16:02:00+09:00'

function run(...actions: OwnerAction[]): OwnerState {
  return actions.reduce(ownerReducer, initialOwnerState)
}

function card(over: Partial<OrderCard> = {}): OrderCard {
  return {
    id: 'call-1',
    status: 'new',
    received_at: REQUESTED,
    pickup_time: '2026-11-08T18:30:00+09:00',
    pickup_label: '18:30',
    lines: [],
    total: 7500,
    total_label: '7,500₩',
    request: 1,
    stage: 'in_call',
    requested_at: REQUESTED,
    deadline_at: '2026-11-08T16:02:20+09:00',
    decided_at: '',
    delay_min: 0,
    auto_accepted: false,
    withdrawn: '',
    ...over,
  }
}

const update = (order: OrderCard): OwnerAction => ({ type: 'order.update', data: order })

describe('order approval card (order approval ticket 02)', () => {
  it('shows a waiting card with the accept button and counts down on the hub clock', () => {
    const s = run({ type: 'snapshot', data: { orders: [], calls: [], now: REQUESTED }, receivedAt: 0 }, update(card()))
    const [waiting] = s.orders
    expect(orderOutcome(waiting)).toBe('waiting')
    expect(canAccept(waiting)).toBe(true)
    expect(approvalsWaiting(s)).toBe(true)
    expect(approvalClock(waiting, storeNow(s, 0))).toBe(20)
    expect(approvalClock(waiting, storeNow(s, 7_400))).toBe(13)
    expect(approvalClock(waiting, storeNow(s, 20_000))).toBe('late')
  })

  it('turns green with the pickup time once accepted and stops waiting', () => {
    const accepted = card({ status: 'accepted', decided_at: '2026-11-08T16:02:09+09:00' })
    const s = run({ type: 'snapshot', data: { orders: [card()], calls: [] } }, update(accepted))
    expect(s.orders).toHaveLength(1)
    expect(orderOutcome(s.orders[0])).toBe('accepted')
    expect(canAccept(s.orders[0])).toBe(false)
    expect(approvalsWaiting(s)).toBe(false)
    expect(OWNER_TEXT.ko.acceptedAt(s.orders[0].pickup_label)).toBe('수락 · 18:30')
  })

  it('marks an order confirmed without a request as auto-accepted', () => {
    const auto = card({ status: 'accepted', auto_accepted: true, request: 0, stage: '', deadline_at: '' })
    expect(orderOutcome(auto)).toBe('auto')
    expect(canAccept(auto)).toBe(false)
    expect(OWNER_TEXT.ko.autoAccepted).toBe('자동 확정(사장님 화면 꺼짐)')
  })

  it('hides the button while the customer changes or after the customer cancels', () => {
    expect(orderOutcome(card({ withdrawn: 'changed' }))).toBe('changing')
    expect(orderOutcome(card({ withdrawn: 'cancelled' }))).toBe('cancelled')
    expect(canAccept(card({ withdrawn: 'changed' }))).toBe(false)
    const s = run({ type: 'snapshot', data: { orders: [card({ withdrawn: 'changed' })], calls: [] } })
    expect(approvalsWaiting(s)).toBe(false)
  })

  it('keeps cards from a server before approval working (no approval fields)', () => {
    const { request: _r, stage: _s, deadline_at: _d, auto_accepted: _a, withdrawn: _w, ...old } = card()
    expect(orderOutcome(old)).toBe('waiting')
    expect(approvalClock(old, 0)).toBe('late')
    expect(orderOutcome({ ...old, status: 'accepted' })).toBe('accepted')
  })

  it('has every approval label in both dictionaries', () => {
    expect(OWNER_TEXT.ko.accept).toBe('수락')
    expect(Object.keys(OWNER_TEXT.ru.approvalStage)).toEqual(['in_call', 'after_call'])
    expect(OWNER_TEXT.ru.acceptedAt('18:30')).toContain('18:30')
  })
})
