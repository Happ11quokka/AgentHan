// 손님 통화 화면의 "사장님 확인 중" 배지와 결과 (주문 승인 티켓 09, spec "손님 통화 화면").
// 서버 `state.approval`(통화 안 마지막 확인 요청 상태)과 받은 문자로 화면 글을 정한다. 통화 중에
// 답이 없어 문자로 넘긴 주문은 확정 문자(kind order)가 와야 확정으로 보인다. 사장님 거절(통화 중이면
// state.approval rejected + 사유, 통화 뒤면 거절 문자, 티켓 05)과 자동 취소 문자는 "주문 취소"다.
import type { ApprovalJson, CustomerText } from './callState.ts'

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
}

/** 문자로 온 가게 결정: 확정(order) · 거절(rejected, 티켓 05) · 자동 취소(expired, 티켓 03) */
const OUTCOME_TEXTS: ReadonlySet<CustomerText['kind']> = new Set(['order', 'rejected', 'expired'])

/** 확인 요청이 문자로 넘어갔고(시간 초과) 아직 결정 문자(확정·거절·자동 취소)가 오지 않았다 */
export function awaitingText(s: Shown): boolean {
  return s.approval?.status === 'timeout' && !s.texts.some((t) => OUTCOME_TEXTS.has(t.kind))
}

/** 가게가 주문을 받지 않았다: 통화 중 거절, 또는 통화 뒤 거절·자동 취소 문자 (티켓 05) */
export function cancelledByStore(s: Shown): boolean {
  return (
    s.approval?.status === 'rejected' ||
    (s.approval?.status === 'timeout' && s.texts.some((t) => t.kind === 'rejected' || t.kind === 'expired'))
  )
}

/** 통화 중 거절 사유별 한 줄 (엔진 사과 대사와 같은 뜻). 재료 소진은 티켓 06 */
const REJECT_DETAIL: Record<string, string> = {
  busy: '가게가 바빠서 지금은 주문을 받기 어렵대요.',
  closed: '가게 사정으로 지금은 주문을 받을 수 없대요.',
  free: '사장님이 남긴 사정을 받은 문자로 확인해 주세요.',
  out_of_stock: '재료가 떨어진 메뉴가 있어요.',
}

/** 배지. 확인 요청이 없었거나 건너뛰었거나(바로 확정) 모르는 상태면 null */
export function approvalBadge(s: Shown): ApprovalBadge | null {
  const a = s.approval
  if (!a) return null
  switch (a.status) {
    case 'waiting':
      return { tone: 'wait', label: '사장님 확인 중', detail: '가게에서 주문을 확인하고 있어요. 잠시만 기다려 주세요.' }
    case 'accepted':
      return a.delay_min > 0
        ? { tone: 'ok', label: `사장님이 ${a.delay_min}분 늦춰 수락했어요`, detail: null }
        : { tone: 'ok', label: '사장님이 수락했어요', detail: null }
    case 'rejected':
      return { tone: 'stop', label: '가게가 주문을 받지 못했어요', detail: REJECT_DETAIL[a.reason] ?? null }
    case 'timeout':
      if (cancelledByStore(s)) {
        return { tone: 'stop', label: '가게가 주문을 받지 못했어요', detail: '받은 문자를 확인해 주세요.' }
      }
      return awaitingText(s)
        ? { tone: 'wait', label: '가게 확인 중', detail: '확인되는 대로 문자로 알려 드려요. 확정 문자를 받으시면 주문 완료예요.' }
        : { tone: 'ok', label: '가게가 주문을 확정했어요', detail: '받은 문자를 확인해 주세요.' }
    case 'withdrawn':
      return a.reason === 'cancelled'
        ? { tone: 'stop', label: '주문을 취소했어요', detail: null }
        : { tone: 'wait', label: '주문을 고치는 중', detail: '다시 확인하면 사장님께 다시 여쭤요.' }
    default:
      return null
  }
}
