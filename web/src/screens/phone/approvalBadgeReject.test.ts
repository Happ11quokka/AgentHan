// 주문 승인 티켓 05: 손님 통화 화면이 사장님 거절을 보인다. 통화 중 거절은 `state.approval`
// rejected + 사유, 통화 뒤 거절은 거절 문자(kind rejected)로 온다.
import { describe, expect, it } from 'vitest'
import { approvalBadge, awaitingText, cancelledByStore } from './approvalBadge.ts'
import type { ApprovalJson, CustomerText } from './callState.ts'

const view = (status: string, extra: Partial<ApprovalJson> = {}): ApprovalJson => ({
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

  it('points a written reason to the texts', () => {
    expect(approvalBadge({ approval: view('rejected', { reason: 'free' }), texts: [] })?.detail).toBe(
      '사장님이 남긴 사정을 받은 문자로 확인해 주세요.',
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
