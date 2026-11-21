// 손님 통화 화면의 "사장님 확인 중" 배지와 결과 (주문 승인 티켓 09, spec "손님 통화 화면").
// 서버 `state.approval`(통화 안 마지막 확인 요청 상태)과 받은 문자, 통화가 끝났는지로 화면 글을 정한다.
// 통화 중에 답이 없어 문자로 넘긴 주문은 확정 문자(kind order, 늦춘 수락이면 delayed)가 와야 확정으로
// 보인다. 결정 전(대기 중·늦춘 시각을 묻는 중)에 통화가 끝나도 결과는 문자로 온다(주문 승인 리뷰 spec 5).
// 사장님 거절(통화 중이면 state.approval rejected + 사유, 통화 뒤면 거절 문자, 티켓 05)과 자동 취소
// 문자는 "주문 취소"다. 통화 중 재료 소진 거절은 손님이 빼거나 바꿀 수 있어 아직 기다림이다(spec 3).
import type { ApprovalJson, ApprovalRejectReason, CustomerText } from './callState.ts'

/** wait: 기다리는 중 · ok: 확정 · stop: 주문이 성립하지 않음 */
export type BadgeTone = 'wait' | 'ok' | 'stop'

export interface ApprovalBadge {
  tone: BadgeTone
  label: string
  /** 한 줄 덧붙임. 없으면 null */
  detail: string | null
}

interface Shown {
  approval: ApprovalJson | null
  texts: CustomerText[]
  /** 통화 상태(`CallState.status`). ended·failed면 통화가 끝났다. 생략하면 통화 중으로 본다 */
  status?: string
}

/**
 * 문자로 온 가게 결정: 확정(order) · 늦춘 수락(delayed, 바뀐 픽업 시각, 티켓 04) · 거절(rejected,
 * 티켓 05) · 자동 취소(expired, 티켓 03)
 */
const OUTCOME_TEXTS: ReadonlySet<CustomerText['kind']> = new Set(['order', 'delayed', 'rejected', 'expired'])

function callEnded(s: Shown): boolean {
  return s.status === 'ended' || s.status === 'failed'
}

/**
 * 결과가 문자로 온다: 통화 중 대기 시간이 지났거나(timeout), 결정 전(대기 중·늦춘 시각을 묻는 중)에 통화가
 * 끝났다. 허브가 통화 뒤 결정·자동 취소를 문자로 보낸다
 */
function handedToTexts(s: Shown): boolean {
  const status = s.approval?.status
  return status === 'timeout' || ((status === 'waiting' || status === 'delayed') && callEnded(s))
}

/** 통화 중 재료 소진 거절: 손님이 빼거나 바꾸는 중이다(통화가 끝나면 취소, 티켓 06) */
function stockOutOnLine(s: Shown): boolean {
  return s.approval?.status === 'rejected' && s.approval.reason === 'out_of_stock' && !callEnded(s)
}

/** 확인 요청이 문자로 넘어갔고 아직 결정 문자(확정·늦춘 수락·거절·자동 취소)가 오지 않았다 */
export function awaitingText(s: Shown): boolean {
  return handedToTexts(s) && !s.texts.some((t) => OUTCOME_TEXTS.has(t.kind))
}

/** 가게가 주문을 받지 않았다: 통화 중 거절(재료 소진은 통화가 끝난 뒤), 또는 문자로 넘긴 뒤 거절·자동 취소 문자 */
export function cancelledByStore(s: Shown): boolean {
  return (
    (s.approval?.status === 'rejected' && !stockOutOnLine(s)) ||
    (handedToTexts(s) && s.texts.some((t) => t.kind === 'rejected' || t.kind === 'expired'))
  )
}

/** 통화 중 거절 사유별 한 줄 (엔진 사과 대사와 같은 뜻) */
const REJECT_DETAIL: Record<ApprovalRejectReason, string> = {
  busy: '가게가 바빠서 지금은 주문을 받기 어렵대요.',
  closed: '가게 사정으로 지금은 주문을 받을 수 없대요.',
  free: '가게 사정으로 지금은 주문을 받을 수 없대요.', // "받은 문자" 미리보기는 없앴다(전화 주문 현황 티켓 05)
  out_of_stock: '재료가 떨어진 메뉴가 있어요.',
}

const STOPPED = '가게가 주문을 받지 못했어요'

/** 배지. 확인 요청이 없었거나 건너뛰었으면(바로 확정) null */
export function approvalBadge(s: Shown): ApprovalBadge | null {
  const a = s.approval
  if (!a) return null
  if (handedToTexts(s)) {
    if (cancelledByStore(s)) return { tone: 'stop', label: STOPPED, detail: null }
    if (awaitingText(s)) {
      return { tone: 'wait', label: '가게 확인 중', detail: '확인되는 대로 문자로 알려 드려요. 확정 문자를 받으시면 주문 완료예요.' }
    }
    return s.texts.some((t) => t.kind === 'delayed')
      ? { tone: 'ok', label: '가게가 시간을 늦춰 확정했어요', detail: null }
      : { tone: 'ok', label: '가게가 주문을 확정했어요', detail: null }
  }
  switch (a.status) {
    case 'waiting':
      return { tone: 'wait', label: '사장님 확인 중', detail: '가게에서 주문을 확인하고 있어요. 잠시만 기다려 주세요.' }
    case 'after_call':
      // 통화 중 대기 0: 주문을 넣고 끊는다. 통화가 끝나면 화면이 주문 현황으로 바뀐다 (전화 메뉴·주문 현황 티켓 04)
      return { tone: 'wait', label: '주문을 넣었어요', detail: '통화가 끝나면 이 화면에서 가게 확인 결과를 보여 드려요.' }
    case 'delayed':
      // 사장님이 늦춰 수락했고 손님이 통화에서 바뀐 시각에 답하는 중 (티켓 04)
      return {
        tone: 'wait',
        label: `사장님이 ${a.delay_min}분 늦게 준비 가능하대요`,
        detail: '바뀐 시간이 괜찮으시면 "네"라고 말씀해 주세요.',
      }
    case 'accepted':
      return a.delay_min > 0
        ? { tone: 'ok', label: `사장님이 ${a.delay_min}분 늦춰 수락했어요`, detail: null }
        : { tone: 'ok', label: '사장님이 수락했어요', detail: null }
    case 'rejected':
      if (stockOutOnLine(s)) return { tone: 'wait', label: '일부 메뉴가 품절이에요', detail: '빼거나 다른 메뉴로 바꿔 주세요.' }
      return { tone: 'stop', label: STOPPED, detail: a.reason ? REJECT_DETAIL[a.reason as ApprovalRejectReason] : null }
    case 'withdrawn':
      return a.reason === 'cancelled'
        ? { tone: 'stop', label: '주문을 취소했어요', detail: null }
        : { tone: 'wait', label: '주문을 고치는 중', detail: '다시 확인하면 사장님께 다시 여쭤요.' }
    default: // skipped(바로 확정)·timeout(위 문자 길)·낡은 서버의 모르는 값
      return null
  }
}
