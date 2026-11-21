// 주문 승인 티켓 05: 거절 사유 시트와 거절 카드 (서버 `hub.py` `reject`, `POST /orders/{id}/reject`).
// 시트가 보낼 본문·막힌 사유는 순수 함수(`rejectSheet.ts`), 카드 결과 줄은 사전(`ownerText.ts`)이 정한다.
import { afterEach, describe, expect, it, vi } from 'vitest'
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
import { rejectOrder } from './ownerStream.ts'
import { OWNER_TEXT, rejectedView, sentTextView } from './ownerText.ts'
import { REJECT_REASONS, reasonOpen, rejectBody } from './rejectSheet.ts'

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
    reject_reason: '',
    reject_menu_ids: [],
    reject_text: '',
    reject_text_ko: '',
    ...over,
  }
}

afterEach(() => vi.unstubAllGlobals())

describe('reject reason sheet (order approval ticket 05)', () => {
  it('lists the four reasons in the spec order, out of stock held back until ticket 06', () => {
    expect(REJECT_REASONS).toEqual(['out_of_stock', 'busy', 'closed', 'free'])
    expect(REJECT_REASONS.filter(reasonOpen)).toEqual(['busy', 'closed', 'free'])
  })

  it('sends nothing until a reason is picked', () => {
    expect(rejectBody(null, '')).toBeNull()
    expect(rejectBody('out_of_stock', '')).toBeNull()
  })

  it('sends a fixed reason at once and ignores any draft text', () => {
    expect(rejectBody('busy', '')).toEqual({ reason: 'busy' })
    expect(rejectBody('closed', 'забыли')).toEqual({ reason: 'closed' })
  })

  it('needs written text for the free reason and trims it', () => {
    expect(rejectBody('free', '   ')).toBeNull()
    expect(rejectBody('free', '  Сегодня закрываемся раньше ')).toEqual({
      reason: 'free',
      text: 'Сегодня закрываемся раньше',
    })
    expect(rejectBody('free', 'x'.repeat(501))).toBeNull()
  })

  it('posts the reason to the reject path and only says whether it went through', async () => {
    const calls: { url: string; init: RequestInit }[] = []
    vi.stubGlobal('fetch', async (url: string, init: RequestInit) => {
      calls.push({ url, init })
      return new Response('{}', { status: calls.length === 1 ? 200 : 409 })
    })
    await expect(rejectOrder('k', 'call-1', { reason: 'busy' })).resolves.toBe(true)
    await expect(rejectOrder('k', 'call-1', { reason: 'closed' })).resolves.toBe(false)
    expect(calls[0].url).toBe('/api/owner/orders/call-1/reject?key=k')
    expect(JSON.parse(String(calls[0].init.body))).toEqual({ reason: 'busy' })
  })
})

describe('rejected card (order approval ticket 05)', () => {
  it('turns gray with the reason and stops waiting', () => {
    const rejected = card({ status: 'rejected', reject_reason: 'busy', decided_at: '2026-11-08T16:02:07+09:00' })
    const s = run({ type: 'snapshot', data: { orders: [card()], calls: [] } }, { type: 'order.update', data: rejected })
    expect(s.orders).toHaveLength(1)
    expect(orderOutcome(s.orders[0])).toBe('rejected')
    expect(canAccept(s.orders[0])).toBe(false)
    expect(approvalsWaiting(s)).toBe(false)
    expect(rejectedView(s.orders[0], 'ko')).toEqual({ label: '거절 · 바빠서 못 맞춤', written: '', writtenKo: '' })
  })

  it('keeps the written reason and the Korean that went to the customer', () => {
    const written = card({
      status: 'rejected',
      reject_reason: 'free',
      reject_text: 'Сегодня закрываемся раньше',
      reject_text_ko: '오늘 일찍 문을 닫아요',
    })
    expect(rejectedView(written, 'ru')).toEqual({
      label: 'Отказано · Написать причину',
      written: 'Сегодня закрываемся раньше',
      writtenKo: '오늘 일찍 문을 닫아요',
    })
  })

  it('shows the reject text kind in the sent texts', () => {
    const view = sentTextView(
      {
        id: 'call-1-s1',
        call_id: 'call-1',
        kind: 'rejected',
        text: '죄송해요, 가게 사정으로 주문을 받을 수 없어 주문이 취소됐어요.',
        sent_at: REQUESTED,
        sent_label: '16:02',
        question_id: '',
        translate_failed: false,
      },
      'ko',
    )
    expect(view.label).toBe('손님에게 보낸 문자 · 16:02 · 거절 안내')
  })

  it('has every reject label in both dictionaries (ru is a draft for a human check)', () => {
    for (const lang of ['ru', 'ko'] as const) {
      const text = OWNER_TEXT[lang]
      expect(Object.keys(text.rejectReason)).toEqual([...REJECT_REASONS])
      expect(text.reject && text.rejectTitle && text.rejectSend && text.rejectBack && text.rejectSoon).toBeTruthy()
      expect(text.smsKind.rejected).toBeTruthy()
    }
  })
})
