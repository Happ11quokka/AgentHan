// 주문 승인 티켓 07: 손님이 기다리는 중에 주문을 고치거나 취소할 때의 확인 요청 카드.
// 서버(`hub.py` `withdraw_approval`)는 같은 id의 카드를 덮어쓴다: 고치는 중(changed) → 다시 복창한 뒤 새
// 요청 번호로 확인 대기, 또는 취소(cancelled). 리듀서는 덮어쓰기만 하고 결과 줄은 `orderOutcome`이 정한다.
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
const snapshot: OwnerAction = { type: 'snapshot', data: { orders: [], calls: [], now: REQUESTED }, receivedAt: 0 }

function run(...actions: OwnerAction[]): OwnerState {
  return actions.reduce(ownerReducer, initialOwnerState)
}

describe('approval card while the customer changes or cancels (order approval ticket 07)', () => {
  it('hides the button while changing and waits again with the new request after the readback', () => {
    const changing = run(snapshot, update(card()), update(card({ withdrawn: 'changed' })))
    expect(changing.orders).toHaveLength(1)
    expect(orderOutcome(changing.orders[0])).toBe('changing')
    expect(canAccept(changing.orders[0])).toBe(false)
    expect(approvalsWaiting(changing)).toBe(false) // 카운트다운을 멈춘다

    const again = ownerReducer(
      changing,
      update(card({ request: 2, requested_at: '2026-11-08T16:02:12+09:00', deadline_at: '2026-11-08T16:02:32+09:00' })),
    )
    const [waiting] = again.orders
    expect(orderOutcome(waiting)).toBe('waiting')
    expect(canAccept(waiting)).toBe(true)
    expect(approvalClock(waiting, storeNow(again, 12_000))).toBe(20) // 새 요청의 마감부터 다시 센다
  })

  it('turns the card into a cancelled one without buttons when the customer cancels', () => {
    const s = run(snapshot, update(card()), update(card({ withdrawn: 'changed' })), update(card({ withdrawn: 'cancelled' })))
    const [cancelled] = s.orders
    expect(orderOutcome(cancelled)).toBe('cancelled')
    expect(canAccept(cancelled)).toBe(false)
    expect(approvalsWaiting(s)).toBe(false)
    expect(OWNER_TEXT.ko.withdrawn.cancelled).toBe('손님이 주문 취소')
    expect(OWNER_TEXT.ko.withdrawn.changed).toBe('손님이 주문을 고치는 중')
  })
})
