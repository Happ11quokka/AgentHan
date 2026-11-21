// 손님 주문 현황 (전화 메뉴·주문 현황·쿠폰 티켓 04, spec "주문 현황: 통화 중 대기를 없앤다").
// 통화가 확정 주문으로 끝나면 같은 화면이 주문 현황으로 바뀐다. 서버 조회 API(`GET /api/phone/orders/{id}`,
// `voice/owner/order_status.py`)의 응답을 화면 상태로 들고, 단계 표시(주문 넣음 → 가게 확인 중 → 확정(또는
// 취소) → 픽업 시각)와 문구를 정한다. 토큰은 없다(주문 번호 = 추측할 수 없는 통화 id). 남은 시간(5분)은
// 손님에게 보이지 않는다. 화면은 주문 번호별 스트림(`orderStream.ts`)으로 스스로 바뀌고, 끊기면 조회 API로 다시
// 읽는다(티켓 05). 제목 밑 한 줄(`detail`)과 거절 사유(`reason.text`)는 서버가 손님 문자 문장(`voice/owner/sms.py`)으로
// 준다. 여기서는 문장을 다시 쓰지 않는다(PR #44 리뷰). 제목(단계·결과별 한 줄)만 이 화면 말이다.
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

/**
 * 결과(서버 `order_status.OrderOutcome`과 같은 닫힌 목록, 티켓 05). 확인 중이면 null. 확정: accepted(제시간
 * 수락) · delayed(늦춘 수락) · auto(자동 확정). 취소: rejected(거절) · out_of_stock(재료 소진) · expired(5분
 * 무응답 자동 취소) · customer_cancelled(손님 취소)
 */
export type OrderOutcome =
  'accepted' | 'delayed' | 'auto' | 'rejected' | 'out_of_stock' | 'expired' | 'customer_cancelled'

/** 거절 사유. code: busy · closed · free(직접 쓰기) · out_of_stock. text: 한 줄(직접 쓰기는 사장님 글의 한국어, 번역 중이면 '') */
export interface OrderReason {
  code: string
  text: string
}

/** 조회 API 응답(스트림 `order.status`도 같은 모양) */
export interface OrderStatusJson {
  order_id: string
  stage: OrderStage
  /** 주문서를 만든 가게 시각 ISO */
  placed_at: string
  lines: OrderStatusLine[]
  /** subtotal: 할인 전 금액 · total: 낼 금액. 쿠폰을 쓴 주문만 discount·coupon_name·coupon_label (쿠폰 티켓 09) */
  amount: { subtotal: number; total: number; discount?: number; coupon_name?: string; coupon_label?: string }
  /** 픽업 시각. 늦춰 확정됐으면 `original_*`이 원래 시각(아니면 '') */
  pickup: {
    time: string
    label: string
    original_time: string
    original_label: string
  }
  outcome: OrderOutcome | null
  /** 거절일 때만 */
  reason: OrderReason | null
  /** 제목 밑 한 줄(서버가 손님 문자 문장으로 준다). 없으면 null */
  detail: string | null
  /** 재료 소진 거절의 떨어진 메뉴 이름들 */
  sold_out: string[]
  /** 손님이 이 화면에서 취소할 수 있다: 가게 확인 중 · 늦춘 수락으로 확정 (티켓 07, `orderCancel.ts`) */
  can_cancel: boolean
}

/** loading: 읽는 중 · shown: 받음 · missing: 그런 주문이 없음(404) · failed: 읽지 못함(회선·서버) */
export type OrderStatusPhase = 'loading' | 'shown' | 'missing' | 'failed'

export interface OrderStatusState {
  orderId: string
  phase: OrderStatusPhase
  /** 마지막으로 받은 현황. 다시 읽는 중이거나 실패해도 남겨 둔다(화면이 비지 않게) */
  order: OrderStatusJson | null
}

/**
 * load·loaded·missing·failed: 조회 API. streamed: 스트림(`order.status`)이 보낸 현황(티켓 05). 둘이 엇갈려 늦게 온
 * 조회 응답이 이미 보인 결정을 "확인 중"으로 되돌리지 않게 단계는 앞으로만 간다(`stageRank`)
 */
export type OrderStatusAction =
  | { type: 'load' }
  | { type: 'loaded'; order: OrderStatusJson }
  | { type: 'streamed'; order: OrderStatusJson }
  | { type: 'missing' }
  | { type: 'failed' }

/** 단계 순서. 확인 중 → 확정 → 취소(늦춘 수락 뒤 손님 취소, 티켓 07). 거꾸로 가지 않는다 */
const STAGE_RANK: Record<OrderStage, number> = { checking: 0, confirmed: 1, cancelled: 2 }

function shown(state: OrderStatusState, order: OrderStatusJson): OrderStatusState {
  if (order.order_id !== state.orderId) return state // 다른 주문의 늦은 응답은 버린다
  if (state.order && STAGE_RANK[order.stage] < STAGE_RANK[state.order.stage]) {
    return { ...state, phase: 'shown' } // 이미 보인 결정보다 앞선 현황(엇갈린 늦은 응답)
  }
  return { ...state, phase: 'shown', order }
}

export function initialOrderStatus(orderId: string): OrderStatusState {
  return { orderId, phase: 'loading', order: null }
}

export function orderStatusReducer(state: OrderStatusState, action: OrderStatusAction): OrderStatusState {
  switch (action.type) {
    case 'load':
      return { ...state, phase: 'loading' }
    case 'loaded':
    case 'streamed':
      return shown(state, action.order)
    case 'missing':
      return { ...state, phase: 'missing', order: null }
    case 'failed':
      return { ...state, phase: 'failed' }
  }
}

/** 결정이 나서 더 바뀌지 않는 현황. 스트림을 닫는다(손님 취소는 손님 자신의 요청 응답으로 바뀐다, 티켓 07) */
export function orderSettled(order: OrderStatusJson | null): boolean {
  return order !== null && order.stage !== 'checking'
}

/** 취소된 주문에는 [다시 주문하기]가 있다(통화 화면으로) */
export function canReorder(order: OrderStatusJson): boolean {
  return order.stage === 'cancelled'
}

/** 늦춘 수락이면 원래 픽업 시각과 바뀐 시각 */
export function pickupMoved(order: OrderStatusJson): { from: string; to: string } | null {
  const { label, original_label: original } = order.pickup
  return order.stage === 'confirmed' && original && original !== label ? { from: original, to: label } : null
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

/**
 * 주문 현황 맨 위 문구. tone은 통화 화면 배지와 같은 뜻(wait·ok·stop). quote: 직접 쓰기 거절의 사장님 글(한국어)
 */
export interface OrderHeadline {
  tone: 'wait' | 'ok' | 'stop'
  title: string
  detail: string | null
  quote: string | null
}

const CANCELLED = '주문이 취소됐어요'

function head(tone: OrderHeadline['tone'], title: string, detail: string | null = null, quote: string | null = null) {
  return { tone, title, detail, quote }
}

export function orderHeadline(order: OrderStatusJson): OrderHeadline {
  const detail = order.detail ?? null
  switch (order.stage) {
    case 'checking':
      return head('wait', '가게에서 주문을 확인하고 있어요', detail)
    case 'confirmed':
      return head('ok', '주문이 확정됐어요', detail)
    case 'cancelled':
      switch (order.outcome) {
        case 'rejected':
        case 'out_of_stock': // 직접 쓰기(free) 거절은 사장님 글(한국어)을 인용으로 따로
          return head('stop', CANCELLED, detail, order.reason?.code === 'free' ? order.reason.text || null : null)
        case 'customer_cancelled': // 손님 취소 (티켓 07): 주문 현황 [취소하기] · 통화에서 취소
          return head('stop', '주문을 취소했어요', detail)
        default:
          return head('stop', CANCELLED, detail)
      }
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
