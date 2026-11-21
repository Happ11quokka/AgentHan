// 손님 취소 (전화 메뉴·주문 현황·쿠폰 티켓 07): 손님이 주문 현황에서 취소하면 서버(`hub.py` `cancel_by_customer`)가 같은
// id의 카드를 처리 상태 customer_cancelled로 덮어쓴다. 카드는 스스로 "손님 취소"가 되고 수락·거절 버튼과 카운트다운이
// 사라진다. 늦춘 수락 뒤 취소도 같다. 통화 중 취소(withdrawn)·늦춘 시각 거절(cancelled)과는 다른 값이다.
import { describe, expect, it } from 'vitest'
import {
  type OrderCard,
  type OwnerAction,
  type OwnerState,
  approvalsWaiting,
  canAccept,
  initialOwnerState,
  orderOutcome,
  ownerReducer,
} from './ownerState.ts'
import { OWNER_TEXT } from './ownerText.ts'

const REQUESTED = '2026-11-08T16:02:00+09:00'

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
    stage: 'after_call',
    requested_at: REQUESTED,
    deadline_at: '2026-11-08T16:07:00+09:00',
    decided_at: '',
    delay_min: 0,
    auto_accepted: false,
    withdrawn: '',
    ...over,
  }
}

const update = (order: OrderCard): OwnerAction => ({ type: 'order.update', data: order })
const snapshot: OwnerAction = { type: 'snapshot', data: { orders: [], calls: [], now: REQUESTED }, receivedAt: 0 }

function run(...actions: OwnerAction[]): OwnerState {
  return actions.reduce(ownerReducer, initialOwnerState)
}

describe('customer cancel on the owner card (ticket 07)', () => {
  it('a waiting card turns into "손님 취소" by itself, without buttons or countdown', () => {
    const waiting = run(snapshot, update(card()))
    expect(approvalsWaiting(waiting)).toBe(true)

    const s = ownerReducer(
      waiting,
      update(card({ status: 'customer_cancelled', decided_at: '2026-11-08T16:03:00+09:00' })),
    )
    const [cancelled] = s.orders
    expect(orderOutcome(cancelled)).toBe('customer_cancelled')
    expect(canAccept(cancelled)).toBe(false) // 수락·거절 버튼 없음
    expect(approvalsWaiting(s)).toBe(false) // 카운트다운을 멈춘다
  })

  it('a later-pickup card cancelled by the customer is "손님 취소" too', () => {
    const cancelled = card({
      status: 'customer_cancelled',
      delay_min: 15,
      owner_pickup_time: '2026-11-08T18:45:00+09:00',
      owner_pickup_label: '18:45',
    })
    expect(orderOutcome(cancelled)).toBe('customer_cancelled')
  })

  it('is different from a cancel during the call and from declining a later pickup', () => {
    expect(orderOutcome(card({ withdrawn: 'cancelled' }))).toBe('cancelled')
    expect(orderOutcome(card({ status: 'cancelled', delay_min: 15 }))).toBe('declined')
  })

  it('has Korean and Russian copy', () => {
    expect(OWNER_TEXT.ko.customerCancelled).toBe('손님 취소')
    expect(OWNER_TEXT.ru.customerCancelled).toBe('Отменено клиентом')
  })
})
