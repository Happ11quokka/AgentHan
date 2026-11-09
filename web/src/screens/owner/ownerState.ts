// 사장님 화면 상태와 서버 이벤트 모양 (4단계 티켓 01·02·05, 서버 `voice/owner/hub.py`).
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

/**
 * 통화 카드 상태 (티켓 02). live: 통화 중(장바구니가 실시간으로 바뀜). ordered: 확정돼 같은 id의
 * 주문 카드가 됐다(화면에서 뺀다). handoff: 사장님 연결 "손님 연락 필요". missed: 확정 없이 끝난
 * 회색 미완료 카드. closed: 사장님이 닫았다(화면에서 뺀다)
 */
export type CallStatus = 'live' | 'ordered' | 'handoff' | 'missed' | 'closed'

/**
 * 통화가 끝난 이유 (서버 `CallSession._end`). confirmed·handoff·cancelled·no_input은 대화가 끝난 것,
 * hangup은 손님이 끊음(소켓 끊김 포함), error는 오류·엔진 시간 초과·통화 길이 초과
 */
export type CallEndReason =
  | 'confirmed'
  | 'handoff'
  | 'cancelled'
  | 'no_input'
  | 'hangup'
  | 'error'

/** 통화 카드. `id`는 통화 id(확정되면 같은 id의 주문 카드가 생긴다) */
export interface CallCard {
  id: string
  status: CallStatus
  /** 통화가 걸려 온 시각(ISO 8601, 가게 시간대) */
  started_at: string
  /** 서버가 넣은 고정 표기 `16:02` */
  started_label: string
  /** 끝난 이유. 통화 중이면 빈 글 */
  reason: CallEndReason | ''
  lines: OrderCardLine[]
  total: number
  total_label: string
  /** 넘김 카드만: 마지막 손님 말 3줄(한국어 원문, 오래된 것부터). 나머지는 빈 목록 */
  heard: string[]
}

/** 사장님 버튼 답 (티켓 05): 돼요 · 안 돼요 · 모르겠어요 */
export type OwnerAnswer = 'yes' | 'no' | 'unsure'

/** 질문 카드 상태. open: 답 기다림. answered: 답함. 시간 초과·통화 끝은 티켓 06·07이 더한다 */
export type QuestionStatus = 'open' | 'answered'

/** 질문 카드 (티켓 05): 손님이 한 근거 없는 메뉴 질문. `id`는 `{통화 id}-q{번호}` */
export interface QuestionCard {
  id: string
  call_id: string
  /** 통화 안 질문 번호 (1부터) */
  number: number
  menu_id: string
  /** 한국어 메뉴명 (원어 이름은 티켓 03) */
  menu_name: string
  /** 손님 질문 원문 (한국어) */
  text: string
  /** 서버가 카드를 보낸 시각(ISO 8601). 카운트다운은 마감 시각까지 센다 */
  sent_at: string
  deadline_at: string
  status: QuestionStatus
  answer: OwnerAnswer | ''
}

/** SSE 이벤트 이름(`event:`)과 `data:` JSON */
export type OwnerServerEvent =
  | {
      type: 'snapshot'
      /** `questions`는 티켓 05부터 온다. 없으면 빈 목록으로 본다 */
      data: { orders: OrderCard[]; calls: CallCard[]; questions?: QuestionCard[] }
    }
  | { type: 'order.update'; data: OrderCard }
  | { type: 'call.update'; data: CallCard }
  | { type: 'owner.question'; data: QuestionCard }
  | { type: 'owner.question.update'; data: QuestionCard }

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
  /** 화면에 둘 통화 카드(live·handoff·missed). 먼저 걸려 온 통화가 앞 */
  calls: CallCard[]
  /** 질문 카드 전부(답한 것 포함). 먼저 온 질문이 앞 */
  questions: QuestionCard[]
}

export const initialOwnerState: OwnerState = {
  connection: 'closed',
  orders: [],
  calls: [],
  questions: [],
}

export function ownerReducer(state: OwnerState, action: OwnerAction): OwnerState {
  switch (action.type) {
    case 'open':
      return { ...state, connection: 'connecting' }
    case 'lost':
      return state.connection === 'closed' ? state : { ...state, connection: 'connecting' }
    case 'failed':
      return { ...state, connection: 'failed' }
    case 'snapshot':
      return {
        connection: 'live',
        orders: action.data.orders,
        calls: action.data.calls.filter(shown),
        questions: action.data.questions ?? [],
      }
    case 'order.update': {
      const card = action.data
      const at = state.orders.findIndex((o) => o.id === card.id)
      const orders = at < 0 ? [...state.orders, card] : state.orders.with(at, card)
      return { ...state, orders }
    }
    case 'call.update': {
      const card = action.data
      const rest = state.calls.filter((c) => c.id !== card.id)
      if (!shown(card)) return { ...state, calls: rest }
      const at = state.calls.findIndex((c) => c.id === card.id)
      return { ...state, calls: at < 0 ? [...state.calls, card] : state.calls.with(at, card) }
    }
    case 'owner.question':
    case 'owner.question.update': {
      const card = action.data
      const at = state.questions.findIndex((q) => q.id === card.id)
      const questions = at < 0 ? [...state.questions, card] : state.questions.with(at, card)
      return { ...state, questions }
    }
  }
}

/** 주문 카드가 됐거나(ordered) 사장님이 닫은(closed) 통화 카드는 화면에 두지 않는다 */
function shown(card: CallCard): boolean {
  return card.status !== 'ordered' && card.status !== 'closed'
}

/** 맨 위 줄: 지금 통화 중인 카드 */
export function liveCalls(state: OwnerState): CallCard[] {
  return state.calls.filter((c) => c.status === 'live')
}

/** 사장님이 확인하고 닫을 카드: "손님 연락 필요"(handoff)와 회색 미완료(missed) */
export function followUpCalls(state: OwnerState): CallCard[] {
  return state.calls.filter(canDismiss)
}

export function canDismiss(card: CallCard): boolean {
  return card.status === 'handoff' || card.status === 'missed'
}

export function canAccept(card: OrderCard): boolean {
  return card.status === 'new'
}

/** 버튼으로 답할 질문 카드 (먼저 온 것이 앞) */
export function openQuestions(state: OwnerState): QuestionCard[] {
  return state.questions.filter(canAnswer)
}

export function canAnswer(card: QuestionCard): boolean {
  return card.status === 'open'
}

/** 마감까지 남은 초(올림). 지났으면 0. 서버가 보낸 시각부터 센 마감과 같은 시계다 */
export function secondsLeft(card: QuestionCard, nowMs: number): number {
  const left = Date.parse(card.deadline_at) - nowMs
  return Number.isNaN(left) || left <= 0 ? 0 : Math.ceil(left / 1000)
}
