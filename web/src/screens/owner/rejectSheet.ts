// 거절 사유 시트 (주문 승인 티켓 05, 서버 `POST /api/owner/orders/{id}/reject`).
// [거절]을 누르면 한 단계 아래 사유를 고른다(실수로 거절하지 않게). 바쁨·가게 사정은 고르면 바로
// 보낼 수 있고, 직접 쓰기는 글이 있어야 한다(손님에게는 번역 문자로만 간다). 재료 소진은 티켓 06이
// 주문 메뉴 다중 선택과 함께 연다. 그때까지 버튼은 보이되 막혀 있다.
import { type RejectReason, writtenAnswer } from './ownerState.ts'

/** 시트 버튼 순서 (spec: 재료 소진 / 바빠서 못 맞춤 / 영업·가게 사정 / 직접 쓰기) */
export const REJECT_REASONS: readonly RejectReason[] = ['out_of_stock', 'busy', 'closed', 'free']

/** 지금 고를 수 있는 사유. 재료 소진은 아직 막는다(티켓 06이 메뉴 고르기와 함께 연다) */
export function reasonOpen(reason: RejectReason): boolean {
  return reason !== 'out_of_stock'
}

/** 거절 POST 본문 (서버 `RejectBody`) */
export interface RejectBody {
  reason: RejectReason
  /** 재료 소진의 떨어진 메뉴 id들 (티켓 06) */
  menu_ids?: string[]
  /** 직접 쓰기 글(사장님 언어, 앞뒤 공백 뺌) */
  text?: string
}

/**
 * 시트에서 보낼 본문. 아직 보낼 수 없으면 null(사유를 안 골랐음, 막힌 사유, 글이 없거나 너무 긴
 * 직접 쓰기). 직접 쓰기가 아니면 써 둔 글은 보내지 않는다
 */
export function rejectBody(reason: RejectReason | null, draft: string): RejectBody | null {
  if (reason === null || !reasonOpen(reason)) return null
  if (reason !== 'free') return { reason }
  const text = writtenAnswer(draft)
  return text === null ? null : { reason, text }
}
