// 거절 사유 시트 (주문 승인 티켓 05·06, 서버 `POST /api/owner/orders/{id}/reject`).
// [거절]을 누르면 한 단계 아래 사유를 고른다(실수로 거절하지 않게). 바쁨·가게 사정은 고르면 바로
// 보낼 수 있고, 직접 쓰기는 글이 있어야 한다(손님에게는 번역 문자로만 간다). 재료 소진은 주문에 든
// 메뉴 중 떨어진 것을 하나 이상 골라야 한다(여러 개 가능, 티켓 06). 고른 메뉴는 오늘 품절이 된다.
import { type OrderCardLine, type RejectReason, writtenAnswer } from './ownerState.ts'

/** 시트 버튼 순서 (spec: 재료 소진 / 바빠서 못 맞춤 / 영업·가게 사정 / 직접 쓰기) */
export const REJECT_REASONS: readonly RejectReason[] = ['out_of_stock', 'busy', 'closed', 'free']

/** 거절 POST 본문 (서버 `RejectBody`) */
export interface RejectBody {
  reason: RejectReason
  /** 재료 소진의 떨어진 메뉴 id들 (티켓 06) */
  menu_ids?: string[]
  /** 직접 쓰기 글(사장님 언어, 앞뒤 공백 뺌) */
  text?: string
}

/**
 * 시트에서 보낼 본문. 아직 보낼 수 없으면 null(사유를 안 골랐음, 메뉴를 안 고른 재료 소진, 글이
 * 없거나 너무 긴 직접 쓰기). 그 사유가 아니면 써 둔 글·고른 메뉴는 보내지 않는다
 */
export function rejectBody(
  reason: RejectReason | null,
  draft: string,
  picked: readonly string[] = [],
): RejectBody | null {
  if (reason === null) return null
  if (reason === 'out_of_stock') return picked.length > 0 ? { reason, menu_ids: [...picked] } : null
  if (reason !== 'free') return { reason }
  const text = writtenAnswer(draft)
  return text === null ? null : { reason, text }
}

/** 재료 소진에서 고를 메뉴: 주문 줄의 메뉴(같은 메뉴는 한 번, 주문 순서) */
export function stockOutChoices(lines: readonly OrderCardLine[]): OrderCardLine[] {
  const seen = new Set<string>()
  return lines.filter((line) => {
    if (seen.has(line.menu_id)) return false
    seen.add(line.menu_id)
    return true
  })
}

/** 메뉴 고르기를 켜고 끈다(주문 줄 순서를 지킨다) */
export function togglePicked(
  picked: readonly string[],
  menuId: string,
  lines: readonly OrderCardLine[],
): string[] {
  const next = new Set(picked)
  if (next.has(menuId)) next.delete(menuId)
  else next.add(menuId)
  return stockOutChoices(lines)
    .map((line) => line.menu_id)
    .filter((menu) => next.has(menu))
}
