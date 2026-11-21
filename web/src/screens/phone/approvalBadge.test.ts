import { describe, expect, it } from 'vitest'
import { approvalBadge, awaitingText } from './approvalBadge.ts'
import type { ApprovalJson, CustomerText } from './callState.ts'

const view = (status: ApprovalJson['status'], extra: Partial<ApprovalJson> = {}): ApprovalJson => ({
  status,
  number: 1,
  delay_min: 0,
  reason: '',
  ...extra,
})

const orderText: CustomerText = {
  id: 'c1-s1',
  call_id: 'c1',
  kind: 'order',
  text: '주문이 확정됐어요. 메도빅 1개, 합계 7,500원. 18:30에 준비해 둘게요.',
  sent_at: '2026-11-21T18:02:00+09:00',
  sent_label: '18:02',
  question_id: '',
  translate_failed: false,
}

describe('approval badge on the customer screen (주문 승인 티켓 09)', () => {
  it('says the order was placed when the call ends without waiting (전화 메뉴·주문 현황 티켓 04)', () => {
    const badge = approvalBadge({ approval: view('after_call'), texts: [] })
    expect(badge).toMatchObject({ tone: 'wait', label: '주문을 넣었어요' })
    expect(JSON.stringify(badge)).not.toMatch(/접수|문자/)
    expect(awaitingText({ approval: view('after_call'), texts: [], status: 'ended' })).toBe(false)
  })

  it('shows "사장님 확인 중" while the owner decides', () => {
    expect(approvalBadge({ approval: view('waiting'), texts: [] })).toMatchObject({
      tone: 'wait',
      label: '사장님 확인 중',
    })
  })

  it('shows the accept, with the delay when the owner pushed the time back', () => {
    expect(approvalBadge({ approval: view('accepted'), texts: [] })).toMatchObject({
      tone: 'ok',
      label: '사장님이 수락했어요',
    })
    expect(approvalBadge({ approval: view('accepted', { delay_min: 15 }), texts: [] })?.label).toBe(
      '사장님이 15분 늦춰 수락했어요',
    )
  })

  it('hands a timed-out request over to texts until the confirmation text arrives', () => {
    const waitingText = { approval: view('timeout'), texts: [] }
    expect(awaitingText(waitingText)).toBe(true)
    expect(approvalBadge(waitingText)).toMatchObject({ tone: 'wait', label: '가게 확인 중' })

    const confirmed = { approval: view('timeout'), texts: [orderText] }
    expect(awaitingText(confirmed)).toBe(false)
    expect(approvalBadge(confirmed)).toMatchObject({ tone: 'ok', label: '가게가 주문을 확정했어요' })
  })

  it('treats a call that ended while waiting like a timeout: the result comes by text (review spec 5)', () => {
    const hungUp = { approval: view('waiting'), texts: [], status: 'ended' }
    expect(awaitingText(hungUp)).toBe(true)
    expect(approvalBadge(hungUp)).toMatchObject({ tone: 'wait', label: '가게 확인 중' })
    expect(approvalBadge({ ...hungUp, texts: [orderText] })).toMatchObject({
      tone: 'ok',
      label: '가게가 주문을 확정했어요',
    })
    expect(approvalBadge({ ...hungUp, texts: [{ ...orderText, kind: 'expired' }] })).toMatchObject({
      tone: 'stop',
      label: '가게가 주문을 받지 못했어요',
    })
    // 통화 중에는 그대로 "사장님 확인 중"
    expect(awaitingText({ ...hungUp, status: 'live' })).toBe(false)
  })

  it('tells a change from a cancel when the request is withdrawn', () => {
    expect(approvalBadge({ approval: view('withdrawn', { reason: 'changed' }), texts: [] })?.label).toBe(
      '주문을 고치는 중',
    )
    expect(approvalBadge({ approval: view('withdrawn', { reason: 'cancelled' }), texts: [] })).toMatchObject({
      tone: 'stop',
      label: '주문을 취소했어요',
    })
  })

  it('shows no badge without a request, for a skipped approval or for a status it does not know', () => {
    expect(approvalBadge({ approval: null, texts: [] })).toBeNull()
    expect(approvalBadge({ approval: view('skipped', { number: 0 }), texts: [] })).toBeNull()
    // 낡은 서버·새 서버가 모르는 값을 보내도 배지 없이 둔다
    expect(approvalBadge({ approval: view('something_new' as ApprovalJson['status']), texts: [] })).toBeNull()
    expect(awaitingText({ approval: view('accepted'), texts: [] })).toBe(false)
  })
})
