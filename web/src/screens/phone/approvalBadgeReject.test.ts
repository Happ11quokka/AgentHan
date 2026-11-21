// 주문 승인 티켓 05: 손님 통화 화면이 사장님 거절을 보인다. 통화 중 거절은 `state.approval`
// rejected + 사유, 통화 뒤 거절은 거절 문자(kind rejected)로 온다.
import { describe, expect, it } from 'vitest'
import { approvalBadge, awaitingText, cancelledByStore } from './approvalBadge.ts'
import type { ApprovalJson, CustomerText } from './callState.ts'

const view = (status: ApprovalJson['status'], extra: Partial<ApprovalJson> = {}): ApprovalJson => ({
  status,
  number: 1,
  delay_min: 0,
  reason: '',
  ...extra,
})

const text = (kind: CustomerText['kind']): CustomerText => ({
  id: 'c1-s1',
  call_id: 'c1',
  kind,
  text: '죄송해요, 가게 사정으로 주문을 받을 수 없어 주문이 취소됐어요.',
  sent_at: '2026-11-21T18:02:00+09:00',
  sent_label: '18:02',
  question_id: '',
  translate_failed: false,
})

describe('rejection on the customer screen (주문 승인 티켓 05)', () => {
  it('shows an in-call rejection with its reason', () => {
    const s = { approval: view('rejected', { reason: 'busy' }), texts: [] }
    expect(approvalBadge(s)).toEqual({
      tone: 'stop',
      label: '가게가 주문을 받지 못했어요',
      detail: '가게가 바빠서 지금은 주문을 받기 어렵대요.',
    })
    expect(cancelledByStore(s)).toBe(true)
  })

  it('asks to drop or swap while an in-call out-of-stock rejection is on the line (review spec 3)', () => {
    const live = { approval: view('rejected', { reason: 'out_of_stock' }), texts: [], status: 'live' }
    expect(approvalBadge(live)).toEqual({
      tone: 'wait',
      label: '일부 메뉴가 품절이에요',
      detail: '빼거나 다른 메뉴로 바꿔 주세요.',
    })
    expect(cancelledByStore(live)).toBe(false)

    const ended = { ...live, status: 'ended' } // 빼지도 바꾸지도 않고 끝났다: 취소
    expect(approvalBadge(ended)).toEqual({
      tone: 'stop',
      label: '가게가 주문을 받지 못했어요',
      detail: '재료가 떨어진 메뉴가 있어요.',
    })
    expect(cancelledByStore(ended)).toBe(true)
  })

  it('does not point a written reason to a received-text area the screen no longer has (전화 주문 현황 티켓 05)', () => {
    expect(approvalBadge({ approval: view('rejected', { reason: 'free' }), texts: [] })?.detail).toBe(
      '가게 사정으로 지금은 주문을 받을 수 없대요.',
    )
  })

  it('stops waiting for a text once a rejection or an auto-cancel text arrives after the call', () => {
    for (const kind of ['rejected', 'expired'] as const) {
      const s = { approval: view('timeout'), texts: [text(kind)] }
      expect(awaitingText(s)).toBe(false)
      expect(cancelledByStore(s)).toBe(true)
      expect(approvalBadge(s)).toMatchObject({ tone: 'stop', label: '가게가 주문을 받지 못했어요' })
    }
  })

  it('still counts an accepted order as confirmed', () => {
    const s = { approval: view('timeout'), texts: [text('order')] }
    expect(cancelledByStore(s)).toBe(false)
    expect(approvalBadge(s)).toMatchObject({ tone: 'ok' })
  })
})
