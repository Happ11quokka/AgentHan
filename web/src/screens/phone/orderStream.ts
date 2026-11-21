// 주문 현황 실시간 갱신 (전화 메뉴·주문 현황·쿠폰 티켓 05, 서버 `voice/owner/http.py` customer_orders_router).
// 주문 번호로 SSE(`/api/phone/orders/{번호}/stream`)를 연다. 서버는 먼저 `order.status`(조회 API와 같은 모양),
// 그 뒤 사장님 결정·자동 취소로 현황이 달라질 때마다 `order.status`, 창이 지나면 `order.end`를 보낸다. 토큰은
// 없다(주문 번호가 열쇠다). 지금의 "받은 문자" 스트림과 같은 방식이다.
//
// 끊기면(`onerror`) 그 사이의 결정을 놓치지 않게 `onLost`로 알린다. 부른 쪽이 조회 API로 다시 읽는다.
// 브라우저가 스스로 다시 붙으면 서버가 지금 현황부터 다시 보낸다. 서버가 거절해(허브에 없는 주문 404·서버 오류)
// 브라우저가 다시 붙지 않으면 `retryMs` 뒤에 새로 연다. 부른 쪽은 결정이 나면 돌려받은 함수로 닫는다.
import type { OrderStatusJson } from './orderStatus.ts'

/** 서버가 스트림을 거절했을 때 다시 열기까지 */
export const ORDER_STREAM_RETRY_MS = 5_000

/** `EventSource.CLOSED` (시험 환경에는 EventSource가 없다) */
const SOURCE_CLOSED = 2

export function orderStreamUrl(orderId: string): string {
  return `/api/phone/orders/${encodeURIComponent(orderId)}/stream`
}

/** EventSource에서 쓰는 것만. 시험은 가짜를 넣는다 */
export interface OrderSource {
  readyState: number
  onerror: ((ev: Event) => unknown) | null
  addEventListener(name: string, listener: (e: { data: string }) => void): void
  close(): void
}

export interface OrderStreamOptions {
  retryMs?: number
  open?: (url: string) => OrderSource
}

/** 스트림을 연다. 현황은 `onStatus`로, 끊김은 `onLost`로. 돌려준 함수로 닫는다(다시 열기도 멈춘다) */
export function openOrderStream(
  orderId: string,
  onStatus: (order: OrderStatusJson) => void,
  onLost: () => void,
  { retryMs = ORDER_STREAM_RETRY_MS, open = (url) => new EventSource(url) }: OrderStreamOptions = {},
): () => void {
  let source: OrderSource | null = null
  let retry: ReturnType<typeof setTimeout> | null = null
  let closed = false

  const stop = () => {
    source?.close()
    source = null
  }
  const connect = () => {
    retry = null
    const current = open(orderStreamUrl(orderId))
    source = current
    current.addEventListener('order.status', (e) => onStatus(JSON.parse(e.data) as OrderStatusJson))
    // 서버 창이 끝났다. 브라우저가 다시 붙지 않게 닫는다
    current.addEventListener('order.end', stop)
    current.onerror = () => {
      if (closed || source !== current) return
      onLost()
      if (current.readyState === SOURCE_CLOSED) {
        stop()
        retry = setTimeout(connect, retryMs)
      }
    }
  }

  connect()
  return () => {
    closed = true
    if (retry !== null) clearTimeout(retry)
    retry = null
    stop()
  }
}
