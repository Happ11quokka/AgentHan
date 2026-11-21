// 손님 통화 화면의 "사장님 확인 중" 배지와 결과 (주문 승인 티켓 09, spec "손님 통화 화면").
// 서버 `state.approval`(통화 안 마지막 확인 요청 상태)과 받은 문자로 화면 글을 정한다. 통화 중에
// 답이 없어 문자로 넘긴 주문은 확정 문자(kind order)가 와야 확정으로 보인다.
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

/** 확인 요청이 문자로 넘어갔고(시간 초과) 아직 확정 문자가 오지 않았다 */
export function awaitingText(s: Shown): boolean {
  return s.approval?.status === 'timeout' && !s.texts.some((t) => t.kind === 'order')
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
    case 'timeout':
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
