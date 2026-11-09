// 사장님 화면 상태와 서버 이벤트 모양 (4단계 티켓 01, 서버 `voice/owner/hub.py`).
// 화면 상태는 순수 리듀서 하나로 정한다: 이전 상태 + 이벤트 → 다음 상태.
// SSE 이벤트는 카드 전체를 덮어쓴다. `snapshot`이 오면 카드 목록을 통째로 바꾼다.

/** 사장님 처리 상태 (Part A 어휘). 4단계 버튼은 접수 하나 */
export type OrderStatus = 'new' | 'accepted'

export interface OrderCardLine {
  menu_id: string
  name_ko: string
  quantity: number
  options: string[]
  request: string
  subtotal: number
}

/** 주문 카드. `id`는 통화 id(= 계약 `Order.id`) */
export interface OrderCard {
  id: string
  status: OrderStatus
  /** 허브가 받은 시각(ISO 8601, 가게 시간대) */
  received_at: string
  pickup_time: string
  /** 서버가 넣은 고정 표기 `18:30` (Intl을 쓰지 않는다) */
  pickup_label: string
  lines: OrderCardLine[]
  total: number
  /** 서버가 넣은 고정 표기 `25,000₩` */
  total_label: string
}

/** SSE 이벤트 이름(`event:`)과 `data:` JSON */
export type OwnerServerEvent =
  | { type: 'snapshot'; data: { orders: OrderCard[] } }
  | { type: 'order.update'; data: OrderCard }

export type OwnerAction =
  | OwnerServerEvent
  /** "가게 열기"를 눌러 SSE를 연다 */
  | { type: 'open' }
  /** SSE가 끊겼다. 브라우저가 스스로 다시 붙고 snapshot부터 받는다 */
  | { type: 'lost' }
  /** 서버가 스트림을 거절했다(토큰이 없거나 틀림). 브라우저가 다시 붙지 않는다 */
  | { type: 'failed' }

/** closed: 가게 열기 전. connecting: 연결 중·다시 붙는 중. live: snapshot을 받았다. failed: 거절됨 */
export type Connection = 'closed' | 'connecting' | 'live' | 'failed'

export interface OwnerState {
  connection: Connection
  /** 먼저 온 주문이 앞 (화면 위) */
  orders: OrderCard[]
}

export const initialOwnerState: OwnerState = { connection: 'closed', orders: [] }

export function ownerReducer(state: OwnerState, action: OwnerAction): OwnerState {
  switch (action.type) {
    case 'open':
      return { ...state, connection: 'connecting' }
    case 'lost':
      return state.connection === 'closed' ? state : { ...state, connection: 'connecting' }
    case 'failed':
      return { ...state, connection: 'failed' }
    case 'snapshot':
      return { connection: 'live', orders: action.data.orders }
    case 'order.update': {
      const card = action.data
      const at = state.orders.findIndex((o) => o.id === card.id)
      const orders = at < 0 ? [...state.orders, card] : state.orders.with(at, card)
      return { ...state, orders }
    }
  }
}

export function canAccept(card: OrderCard): boolean {
  return card.status === 'new'
}
