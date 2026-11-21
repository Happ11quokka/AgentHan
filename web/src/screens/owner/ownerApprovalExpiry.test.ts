// 주문 승인 티켓 03: 통화 뒤 대기 카운트다운과 자동 취소 카드 (서버 `hub.py` `_end_call`·`expire_due`).
// 리듀서는 카드를 덮어쓰기만 하고, 결과 줄·버튼·남은 시간은 순수 함수가 정한다.
import { describe, expect, it } from 'vitest'
import type { CustomerText } from '../customerText.ts'
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
import { OWNER_TEXT, approvalLeft, sentTextView } from './ownerText.ts'

const REQUESTED = '2026-11-08T16:02:00+09:00'
const EXPIRED_TEXT = '가게에서 확인하지 못해 주문이 취소됐어요. 다시 전화 주세요.'

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

function text(over: Partial<CustomerText> = {}): CustomerText {
  return {
    id: 'call-1-s1',
    call_id: 'call-1',
    kind: 'expired',
    text: EXPIRED_TEXT,
    sent_at: '2026-11-08T16:07:00+09:00',
    sent_label: '16:07',
    question_id: '',
    translate_failed: false,
    ...over,
  }
}

const update = (order: OrderCard): OwnerAction => ({ type: 'order.update', data: order })

describe('after-call wait and auto-cancel (order approval ticket 03)', () => {
  it('counts the after-call wait down in minutes on the hub clock, from the request', () => {
    const s = run(
      { type: 'snapshot', data: { orders: [], calls: [], now: '2026-11-08T16:02:21+09:00' }, receivedAt: 0 },
      update(card()),
    )
    const [waiting] = s.orders
    expect(orderOutcome(waiting)).toBe('waiting')
    expect(canAccept(waiting)).toBe(true) // 통화가 끝나도 마감 전이면 수락할 수 있다
    const left = approvalClock(waiting, storeNow(s, 0))
    expect(left).toBe(279)
    expect(approvalLeft(279, 'ko')).toBe('4:39')
    expect(approvalLeft(300, 'ru')).toBe('5:00')
    expect(approvalLeft(20, 'ko')).toBe('20초') // 통화 중 대기는 초로
    expect(OWNER_TEXT.ko.approvalStage.after_call).toBe('통화 뒤 확인 (문자로 알림)')
  })

  it('greys out an expired card without buttons and stops waiting', () => {
    const expired = card({ status: 'expired', decided_at: '2026-11-08T16:07:00+09:00', sms: [text()] })
    const s = run({ type: 'snapshot', data: { orders: [card()], calls: [] } }, update(expired))
    expect(s.orders).toHaveLength(1)
    expect(orderOutcome(s.orders[0])).toBe('expired')
    expect(canAccept(s.orders[0])).toBe(false)
    expect(approvalsWaiting(s)).toBe(false)
    expect(OWNER_TEXT.ko.expired).toBe('자동 취소 · 시간 안에 확인 못 함')
    expect(OWNER_TEXT.ru.expired).not.toBe('')
  })

  it('labels the auto-cancel text the customer got', () => {
    expect(sentTextView(text(), 'ko')).toEqual({
      label: '손님에게 보낸 문자 · 16:07 · 자동 취소 안내',
      text: EXPIRED_TEXT,
      failed: '',
    })
    expect(sentTextView(text(), 'ru').label).toContain('Автоотмена')
  })
})
