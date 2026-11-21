// 손님 주문 현황 (전화 메뉴·주문 현황·쿠폰 티켓 04, spec "주문 현황: 통화 중 대기를 없앤다").
// 통화가 확정 주문으로 끝나면 같은 화면이 주문 현황으로 바뀐다. 서버 조회 API(`GET /api/phone/orders/{id}`,
// `voice/owner/order_status.py`)의 응답을 화면 상태로 들고, 단계 표시(주문 넣음 → 가게 확인 중 → 확정(또는
// 취소) → 픽업 시각)와 문구를 정한다. 토큰은 없다(주문 번호 = 추측할 수 없는 통화 id). 남은 시간(5분)은
// 손님에게 보이지 않는다. 이 티켓에서는 [새로 고침]으로 다시 읽는다(스스로 갱신은 다음 티켓).
import type { CallState } from './callState.ts'

/** 단계(서버 `order_status.OrderStage`와 같은 닫힌 목록). checking: 가게 확인 중 · confirmed: 확정 · cancelled: 주문 취소 */
export type OrderStage = 'checking' | 'confirmed' | 'cancelled'

export interface OrderStatusLine {
  menu_id: string
  name: string
  quantity: number
  options: string[]
  /** 손님 요청 사항. 없으면 '' */
  request: string
  subtotal: number
}

/** 조회 API 응답 */
export interface OrderStatusJson {
  order_id: string
  stage: OrderStage
  /** 주문서를 만든 가게 시각 ISO */
  placed_at: string
  lines: OrderStatusLine[]
  /** subtotal: 할인 전 금액 · total: 낼 금액. 쿠폰을 쓴 주문만 discount·coupon_name (쿠폰 티켓 09) */
  amount: { subtotal: number; total: number; discount?: number; coupon_name?: string }
  /** 픽업 시각. 늦춰 확정됐으면 `original_*`이 원래 시각(아니면 '') */
  pickup: {
    time: string
    label: string
    original_time: string
    original_label: string
  }
}

/** loading: 읽는 중 · shown: 받음 · missing: 그런 주문이 없음(404) · failed: 읽지 못함(회선·서버) */
export type OrderStatusPhase = 'loading' | 'shown' | 'missing' | 'failed'

export interface OrderStatusState {
  orderId: string
  phase: OrderStatusPhase
  /** 마지막으로 받은 현황. 다시 읽는 중이거나 실패해도 남겨 둔다(화면이 비지 않게) */
  order: OrderStatusJson | null
}

export type OrderStatusAction =
  { type: 'load' } | { type: 'loaded'; order: OrderStatusJson } | { type: 'missing' } | { type: 'failed' }

export function initialOrderStatus(orderId: string): OrderStatusState {
  return { orderId, phase: 'loading', order: null }
}

export function orderStatusReducer(state: OrderStatusState, action: OrderStatusAction): OrderStatusState {
  switch (action.type) {
    case 'load':
      return { ...state, phase: 'loading' }
    case 'loaded':
      // 다른 주문의 늦은 응답은 버린다
      return action.order.order_id === state.orderId ? { ...state, phase: 'shown', order: action.order } : state
    case 'missing':
      return { ...state, phase: 'missing', order: null }
    case 'failed':
      return { ...state, phase: 'failed' }
  }
}

export function orderStatusUrl(orderId: string): string {
  return `/api/phone/orders/${encodeURIComponent(orderId)}`
}

/** 조회 API를 한 번 부르고 결과를 리듀서 동작으로 돌려준다. 시험은 가짜 `get`을 넣는다 */
export async function fetchOrderStatus(
  orderId: string,
  get: (url: string) => Promise<Response> = (url) => fetch(url),
): Promise<OrderStatusAction> {
  try {
    const res = await get(orderStatusUrl(orderId))
    if (res.status === 404) return { type: 'missing' }
    if (!res.ok) return { type: 'failed' }
    return { type: 'loaded', order: (await res.json()) as OrderStatusJson }
  } catch {
    return { type: 'failed' }
  }
}

/** done: 지난 단계 · current: 지금 단계 · todo: 아직 */
export type StepState = 'done' | 'current' | 'todo'

export interface OrderStep {
  key: 'placed' | 'checking' | 'decided' | 'pickup'
  label: string
  state: StepState
}

/** 단계 표시: 주문 넣음 → 가게 확인 중 → 확정(또는 주문 취소) → 픽업 시각. 취소면 픽업 단계가 없다 */
export function orderSteps(order: OrderStatusJson): OrderStep[] {
  const pickup = order.pickup.label ? `픽업 ${order.pickup.label}` : '픽업'
  switch (order.stage) {
    case 'checking':
      return [
        { key: 'placed', label: '주문 넣음', state: 'done' },
        { key: 'checking', label: '가게 확인 중', state: 'current' },
        { key: 'decided', label: '확정', state: 'todo' },
        { key: 'pickup', label: pickup, state: 'todo' },
      ]
    case 'confirmed':
      return [
        { key: 'placed', label: '주문 넣음', state: 'done' },
        { key: 'checking', label: '가게 확인', state: 'done' },
        { key: 'decided', label: '확정', state: 'done' },
        { key: 'pickup', label: pickup, state: 'current' },
      ]
    case 'cancelled':
      return [
        { key: 'placed', label: '주문 넣음', state: 'done' },
        { key: 'checking', label: '가게 확인', state: 'done' },
        { key: 'decided', label: '주문 취소', state: 'current' },
      ]
  }
}

/** 주문 현황 맨 위 문구. tone은 통화 화면 배지와 같은 뜻(wait·ok·stop) */
export interface OrderHeadline {
  tone: 'wait' | 'ok' | 'stop'
  title: string
  detail: string | null
}

export function orderHeadline(order: OrderStatusJson): OrderHeadline {
  switch (order.stage) {
    case 'checking':
      return {
        tone: 'wait',
        title: '가게에서 주문을 확인하고 있어요',
        detail: '확인되면 이 화면에서 알려 드려요.',
      }
    case 'confirmed':
      return {
        tone: 'ok',
        title: '주문이 확정됐어요',
        detail: order.pickup.label ? `${order.pickup.label}에 찾으러 오세요.` : null,
      }
    case 'cancelled':
      return { tone: 'stop', title: '주문이 취소됐어요', detail: null }
  }
}

/**
 * 통화가 주문으로 끝났으면 그 주문 번호. 이때 화면이 주문 현황으로 바뀐다. 확정 주문서(`order` 이벤트, status
 * confirmed)가 온 채 통화가 끝났을 때만이다. 사장님께 넘긴 통화(handoff 주문서)·손님이 취소한 통화·무응답으로
 * 끊긴 통화·끊김 오류는 주문 현황으로 가지 않고 지금처럼 통화 결과를 보인다
 */
export function callOrderId(call: Pick<CallState, 'status' | 'order'>): string | null {
  if (call.status !== 'ended' || call.order?.order.status !== 'confirmed') return null
  return call.order.order.id || null
}
