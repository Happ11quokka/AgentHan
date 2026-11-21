// 손님 취소 (전화 메뉴·주문 현황·쿠폰 티켓 07, spec "주문 현황: 통화 중 대기를 없앤다" 손님 취소).
// 손님은 주문 현황에서 두 때만 [취소하기]를 본다: 가게가 아직 확인 중일 때, 늦춘 수락으로 확정됐을 때(서버가 정한
// `can_cancel`). 제시간 수락·자동 확정은 가게가 이미 만들기 시작했을 수 있어 버튼 없이 "가게로 전화" 안내만 보인다.
// 취소 API(`POST /api/phone/orders/{id}/cancel`, 서버 `voice/owner/http.py`)는 사장님 결정과 "한 주문에 결정 한
// 번" 규칙을 같이 따른다. 늦게 누른 쪽은 409로 거부되고, 응답에 실린 지금 현황(이긴 결과)과 한 줄을 보인다.
import { type OrderStatusJson, orderStatusUrl } from './orderStatus.ts'

/** 거부 사유(서버 `order_status.CancelRefusal`과 같은 닫힌 목록). 지금 결과이거나 in_call(통화가 손님 말을 받는 중) */
export type CancelRefusal =
  'accepted' | 'auto' | 'rejected' | 'out_of_stock' | 'expired' | 'customer_cancelled' | 'in_call'

/**
 * 취소 API 결과. cancelled: 취소됨(바뀐 현황) · refused: 거부(사유·손님 화면 한 줄·지금 현황) · missing: 그런 주문이
 * 없음(404) · failed: 닿지 못함(회선·서버, 다시 누를 수 있다)
 */
export type CancelResult =
  | { type: 'cancelled'; order: OrderStatusJson }
  | { type: 'refused'; code: CancelRefusal; message: string; order: OrderStatusJson }
  | { type: 'missing' }
  | { type: 'failed' }

/** 서버에 닿지 못했을 때 화면 한 줄 */
export const CANCEL_FAILED = '취소하지 못했어요. 잠시 뒤 다시 눌러 주세요.'

export function orderCancelUrl(orderId: string): string {
  return `${orderStatusUrl(orderId)}/cancel`
}

/** 취소 API를 한 번 부른다. 시험은 가짜 `post`를 넣는다 */
export async function cancelOrder(
  orderId: string,
  post: (url: string, init?: RequestInit) => Promise<Response> = (url, init) => fetch(url, init),
): Promise<CancelResult> {
  try {
    const res = await post(orderCancelUrl(orderId), { method: 'POST' })
    if (res.ok) return { type: 'cancelled', order: (await res.json()) as OrderStatusJson }
    if (res.status === 404) return { type: 'missing' }
    if (res.status === 409) {
      const { detail } = (await res.json()) as {
        detail: { code: CancelRefusal; message: string; order: OrderStatusJson }
      }
      return { type: 'refused', code: detail.code, message: detail.message, order: detail.order }
    }
    return { type: 'failed' }
  } catch {
    return { type: 'failed' }
  }
}

/** 확정됐지만 화면에서 취소할 수 없는 주문(제시간 수락·자동 확정)의 안내. 그 밖에는 null */
export function cancelHint(order: OrderStatusJson): string | null {
  return order.stage === 'confirmed' && !order.can_cancel ? '취소하려면 가게로 전화 주세요.' : null
}
