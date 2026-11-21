// 주문 승인 티켓 04: 늦춘 수락이 손님 통화 화면 배지에 보인다 (서버 `state.approval`·받은 문자).
import { describe, expect, it } from 'vitest'
import { approvalBadge, awaitingText } from './approvalBadge.ts'
import type { ApprovalJson, CustomerText } from './callState.ts'

const view = (status: ApprovalJson['status'], extra: Partial<ApprovalJson> = {}): ApprovalJson => ({
  status,
  number: 1,
  delay_min: 15,
  reason: '',
  ...extra,
})

const delayedText: CustomerText = {
  id: 'c1-s1',
  call_id: 'c1',
  kind: 'delayed',
  text: '주문이 확정됐어요. 메도빅 1개, 합계 7,500원. 18:45에 준비돼요. 어려우시면 전화 주세요.',
  sent_at: '2026-11-21T18:04:00+09:00',
  sent_label: '18:04',
  question_id: '',
  translate_failed: false,
}

describe('delayed accept on the customer screen (주문 승인 티켓 04)', () => {
  it('asks the customer to answer while the new time is on the line', () => {
    expect(approvalBadge({ approval: view('delayed'), texts: [] })).toMatchObject({
      tone: 'wait',
      label: '사장님이 15분 늦게 준비 가능하대요',
    })
  })

  it('shows the delayed accept after a yes, and a cancel after a no', () => {
    expect(approvalBadge({ approval: view('accepted'), texts: [delayedText] })?.label).toBe(
      '사장님이 15분 늦춰 수락했어요',
    )
    expect(approvalBadge({ approval: view('withdrawn', { reason: 'cancelled' }), texts: [] })?.tone).toBe('stop')
  })

  it('counts a delayed-accept text as the confirmation after a timed-out call', () => {
    const shown = { approval: view('timeout', { delay_min: 0 }), texts: [delayedText] }
    expect(awaitingText(shown)).toBe(false)
    expect(approvalBadge(shown)).toMatchObject({ tone: 'ok', label: '가게가 시간을 늦춰 확정했어요' })
  })
})
