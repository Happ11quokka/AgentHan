// 사장님 화면 상태와 서버 이벤트 모양 (4단계 티켓 01·02·03·05·10·13, 서버 `voice/owner/hub.py`).
// 화면 상태는 순수 리듀서 하나로 정한다: 이전 상태 + 이벤트 → 다음 상태.
// SSE 이벤트는 카드 전체를 덮어쓴다. `snapshot`이 오면 카드 목록을 통째로 바꾼다.

/** 사장님 처리 상태 (Part A 어휘). 4단계 버튼은 접수 하나 */
export type OrderStatus = 'new' | 'accepted'

/** 손님 요청 배지 (서버 `badges.py`, 한국어 원문 규칙). remove = 빼기, allergy = 알레르기 */
export type RequestBadge = 'remove' | 'allergy'

export interface OrderCardLine {
  menu_id: string
  name_ko: string
  /** 사장님 원어 이름(`MenuItem.name_native`). 비면 서버가 `name_ko`를 넣는다 (티켓 03) */
  name_owner: string
  quantity: number
  options: string[]
  /** 옵션 이름의 사장님 언어(서버 정적 사전). 사전에 없으면 한국어 그대로 */
  options_owner: string[]
  /** 손님 요청 한국어 원문 */
  request: string
  /** 요청 번역(Papago). 아직 안 왔거나 실패·버림이면 빈 글. 번역이 오면 같은 카드가 다시 온다 */
  request_owner: string
  badges: RequestBadge[]
  subtotal: number
}

/**
 * 손님에게 보낸 문자 미리보기 (티켓 07, 서버 `voice/owner/sms.py`). 실제 문자는 보내지 않는다.
 * order: 확정 안내. late_answer: 시간 초과·통화 끝 뒤 버튼 답. free_answer: 자유 입력 답(번역)
 */
export interface SentText {
  id: string
  call_id: string
  kind: 'order' | 'late_answer' | 'free_answer'
  /** 손님이 받은 한국어 문장 */
  text: string
  sent_at: string
  /** 서버 고정 표기 `16:07` */
  sent_label: string
  question_id: string
  /** 자유 입력 글을 번역하지 못해 원문이 갔다 */
  translate_failed: boolean
}

/** 주문 카드. `id`는 통화 id(= 계약 `Order.id`) */
export interface OrderCard {
  id: string
  status: OrderStatus
  /** 허브가 받은 시각(ISO 8601, 가게 시간대) */
  received_at: string
  /** 주문 시각 고정 표기 `16:05` (이야기 25, 리뷰 P1). 리뷰 전 서버는 안 보낸다 */
  received_label?: string
  /** 손님 이름(성). 지금 통화 엔진은 이름을 받지 않아 빈 글이다 */
  customer_name?: string
  pickup_time: string
  /** 서버가 넣은 고정 표기 `18:30` (Intl을 쓰지 않는다) */
  pickup_label: string
  lines: OrderCardLine[]
  total: number
  /** 서버가 넣은 고정 표기 `25,000₩` */
  total_label: string
  /**
   * 그 통화에 답을 못 받은 손님 질문이 있다(티켓 06). 손님은 "확인해서 문자로 알려 드릴게요"를
   * 들었다. 화면은 "사장님 확인 필요"를 붙인다. 06 전 서버는 안 보낸다
   */
  owner_check?: boolean
  /** 손님에게 보낸 문자(보낸 순서, 티켓 07). 07 전 서버는 안 보낸다 */
  sms?: SentText[]
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
  /** `heard`의 줄마다 번역(아직·실패면 빈 글). 길이는 `heard`와 같다 (티켓 03) */
  heard_owner: string[]
  /** 손님에게 보낸 문자(티켓 07, 미완료 통화 뒤 늦은 답 등). 07 전 서버는 안 보낸다 */
  sms?: SentText[]
}

/** 자막 한 줄 (티켓 10). 손님 말 또는 AI 말 */
export interface CaptionLine {
  /** 통화 안 줄 번호(1부터). 번역이 오면 같은 번호 줄이 덮어써진다 */
  n: number
  who: 'customer' | 'bot'
  /** 한국어 원문 */
  text: string
  /** 손님 말 번역(Papago). 아직·실패면 빈 글. AI 말은 늘 빈 글(번역하지 않는다) */
  text_owner: string
}

/** 통화 하나의 자막 전체(`call.caption`). 서버가 최근 4줄만 보낸다 */
export interface CallCaption {
  call_id: string
  lines: CaptionLine[]
}

/** 통화 카드 아래 자막 줄 수 (spec "Papago 실측": 최근 4줄) */
export const CAPTION_LINES = 4

/** 사장님 버튼 답 (티켓 05): 돼요 · 안 돼요 · 모르겠어요 */
export type OwnerAnswer = 'yes' | 'no' | 'unsure'

/**
 * 질문 카드 상태. open: 답 기다림. answered: 답함. timeout: 통화 중 답 대기 시간이 지났다(손님은
 * 문자 약속을 들었다). call_ended: 답 전에 통화가 끝났다. timeout·call_ended도 답을 받는다(티켓 06)
 */
export type QuestionStatus = 'open' | 'answered' | 'timeout' | 'call_ended'

/** 질문 카드 (티켓 05): 손님이 한 근거 없는 메뉴 질문. `id`는 `{통화 id}-q{번호}` */
export interface QuestionCard {
  id: string
  call_id: string
  /** 통화 안 질문 번호 (1부터) */
  number: number
  menu_id: string
  /** 한국어 메뉴명 */
  menu_name: string
  /** 사장님 원어 메뉴명. 비면 서버가 한국어 이름을 넣는다 (티켓 03) */
  menu_name_owner: string
  /** 손님 질문 원문 (한국어) */
  text: string
  /** 손님 질문 번역(Papago). 아직·실패면 빈 글. 번역이 오면 `owner.question.update`로 다시 온다 (티켓 03) */
  text_owner: string
  /**
   * 손님이 들어가는지만 물었다("견과류 들어가요?"). 버튼 yes·no가 들어가요/안 들어가요 뜻이다
   * (리뷰 P2). 없으면 거짓으로 본다
   */
  yes_no?: boolean
  /** 서버가 카드를 보낸 시각(ISO 8601). 카운트다운은 마감 시각까지 센다 */
  sent_at: string
  deadline_at: string
  status: QuestionStatus
  /** free: 자유 입력으로 답함 (티켓 07) */
  answer: OwnerAnswer | 'free' | ''
  /** 사장님이 카드를 처음 본 시각(ISO 8601), 아직이면 ''. 06 전 서버는 안 보낸다 */
  seen_at?: string
  /** 자유 입력 답의 사장님 글 원문 (티켓 07) */
  answer_text?: string
  /** 그 글의 한국어 번역(손님 문자에 들어감). 아직·실패면 '' */
  answer_text_ko?: string
}

/** 메뉴 질문 종류 (서버 `menu_info.QuestionKind`) */
export type KnowledgeKind = 'allergen' | 'allergy' | 'price' | 'option' | 'ingredient' | 'taste' | 'other'

/**
 * "손님이 궁금해한 것" 카드 (티켓 13): AI가 답하지 못한 메뉴 질문. 같은 메뉴·종류·대상 질문은
 * 열린 카드 하나에 횟수로 쌓인다. 사장님이 답을 저장하면 filled, 다음 통화부터 AI가 그 답을 쓴다
 */
export interface KnowledgeCard {
  /** `k1`, `k2`… */
  id: string
  menu_id: string
  menu_name: string
  /** 사장님 원어 메뉴명. 비면 서버가 한국어 이름을 넣는다 */
  menu_name_owner: string
  kind: KnowledgeKind
  /** 알레르기 성분·이름 붙은 재료(한국어). 그 밖 질문은 질문 요약, 나머지는 빈 글 */
  subject: string
  /** 들어가는지만 묻는 질문이다(같은 대상의 "빼 줄 수 있어요?"와 카드가 갈린다, 리뷰 C4) */
  yes_no?: boolean
  /** 마지막으로 물은 통화 */
  call_id: string
  /** 마지막 손님 질문 원문 (한국어) */
  text: string
  /** 그 질문 번역. 아직·실패면 빈 글 */
  text_owner: string
  /** 같은 질문을 받은 횟수 */
  count: number
  asked_at: string
  last_asked_at: string
  status: 'open' | 'filled'
  /** 사장님 글 원문 (사장님 언어) */
  answer_text: string
  /** 저장한 한국어. AI가 "사장님 말씀으로는 …"으로 그대로 말한다 */
  answer_ko: string
  saved_at: string
}

/** 사장님 답 미리보기 (`POST /api/owner/knowledge/{id}/preview`). 저장 전이다 */
export interface KnowledgePreview {
  text: string
  /** 한국어 번역. 번역이 안 되면 빈 글(사장님이 한국어를 직접 적는다) */
  text_ko: string
  translate_failed: boolean
}

/** SSE 이벤트 이름(`event:`)과 `data:` JSON */
export type OwnerServerEvent =
  | {
      type: 'snapshot'
      /** `questions`는 티켓 05, `captions`는 티켓 10부터 온다. 없으면 빈 목록으로 본다 */
      data: {
        orders: OrderCard[]
        calls: CallCard[]
        questions?: QuestionCard[]
        captions?: CallCaption[]
        /** 티켓 13부터 온다 */
        knowledge?: KnowledgeCard[]
        /** 허브 시계 지금 시각(ISO 8601). 화면이 기기 시계와의 차이를 잰다 (리뷰 C2) */
        now?: string
      }
      /** snapshot을 받은 때의 기기 시각(ms). `now`와의 차이가 허브 시계 보정값이다 */
      receivedAt?: number
    }
  | { type: 'order.update'; data: OrderCard }
  | { type: 'call.update'; data: CallCard }
  | { type: 'call.caption'; data: CallCaption }
  | { type: 'owner.question'; data: QuestionCard }
  | { type: 'owner.question.update'; data: QuestionCard }
  | { type: 'knowledge.update'; data: KnowledgeCard }

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
  /** 통화 id → 자막 줄(오래된 것부터). 통화 중 카드 것만 둔다 (티켓 10) */
  captions: Record<string, CaptionLine[]>
  /** "손님이 궁금해한 것" 카드 전부(채운 것 포함). 먼저 생긴 카드가 앞 (티켓 13) */
  knowledge: KnowledgeCard[]
  /**
   * 허브 시계 − 기기 시계(ms). 질문 카드 마감·픽업 시각은 허브 시계라 이만큼 보정해 센다
   * (`PHONE_NOW` 시연·시계가 틀린 태블릿, 리뷰 C2). 모르면 0
   */
  clockOffsetMs: number
}

export const initialOwnerState: OwnerState = {
  connection: 'closed',
  orders: [],
  calls: [],
  questions: [],
  captions: {},
  knowledge: [],
  clockOffsetMs: 0,
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
        captions: Object.fromEntries((action.data.captions ?? []).map((c) => [c.call_id, c.lines])),
        knowledge: action.data.knowledge ?? [],
        clockOffsetMs: clockOffset(action.data.now, action.receivedAt),
      }
    case 'order.update': {
      const card = action.data
      const at = state.orders.findIndex((o) => o.id === card.id)
      const orders = at < 0 ? [...state.orders, card] : state.orders.with(at, card)
      return { ...state, orders }
    }
    case 'call.update': {
      const card = action.data
      // 자막은 통화 중 카드 아래에만 있다. 주문·넘김·미완료가 되면 버린다
      const captions = card.status === 'live' ? state.captions : without(state.captions, card.id)
      const rest = state.calls.filter((c) => c.id !== card.id)
      if (!shown(card)) return { ...state, calls: rest, captions }
      const at = state.calls.findIndex((c) => c.id === card.id)
      const calls = at < 0 ? [...state.calls, card] : state.calls.with(at, card)
      return { ...state, calls, captions }
    }
    case 'call.caption':
      return { ...state, captions: { ...state.captions, [action.data.call_id]: action.data.lines } }
    case 'owner.question':
    case 'owner.question.update': {
      const card = action.data
      const at = state.questions.findIndex((q) => q.id === card.id)
      const questions = at < 0 ? [...state.questions, card] : state.questions.with(at, card)
      return { ...state, questions }
    }
    case 'knowledge.update': {
      const card = action.data
      const at = state.knowledge.findIndex((k) => k.id === card.id)
      const knowledge = at < 0 ? [...state.knowledge, card] : state.knowledge.with(at, card)
      return { ...state, knowledge }
    }
  }
}

function clockOffset(now: string | undefined, receivedAt: number | undefined): number {
  const hub = now === undefined ? NaN : Date.parse(now)
  return receivedAt === undefined || Number.isNaN(hub) ? 0 : hub - receivedAt
}

/** 허브 시계로 본 지금(ms). 카운트다운·픽업 색은 이 값으로 센다 (리뷰 C2) */
export function storeNow(state: OwnerState, deviceNowMs: number): number {
  return deviceNowMs + state.clockOffsetMs
}

function without<T>(record: Record<string, T>, key: string): Record<string, T> {
  if (!(key in record)) return record
  const { [key]: _, ...rest } = record
  return rest
}

/** 주문 카드가 됐거나(ordered) 사장님이 닫은(closed) 통화 카드는 화면에 두지 않는다 */
function shown(card: CallCard): boolean {
  return card.status !== 'ordered' && card.status !== 'closed'
}

/** 맨 위 줄: 지금 통화 중인 카드 */
export function liveCalls(state: OwnerState): CallCard[] {
  return state.calls.filter((c) => c.status === 'live')
}

/** 통화 카드 아래 자막: 최근 `CAPTION_LINES`줄, 오래된 것부터. 없으면 빈 목록 */
export function captionLines(state: OwnerState, callId: string): CaptionLine[] {
  return (state.captions[callId] ?? []).slice(-CAPTION_LINES)
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

/** 버튼으로 답할 질문 카드 (먼저 온 것이 앞). 시간 초과·통화 끝 카드도 남는다(늦은 답) */
export function openQuestions(state: OwnerState): QuestionCard[] {
  return state.questions.filter(canAnswer)
}

export function canAnswer(card: QuestionCard): boolean {
  return card.status !== 'answered'
}

/** 통화가 아직 답을 기다리는 카드가 있다(카운트다운을 다시 그린다) */
export function questionsWaiting(state: OwnerState): boolean {
  return state.questions.some((q) => q.status === 'open')
}

/** 이 카드를 봤다고 서버에 알릴까. 통화가 기다리는 중이고 아직 안 알린 카드만 */
export function needsSeen(card: QuestionCard): boolean {
  return card.status === 'open' && !card.seen_at
}

/**
 * 질문 카드 열람 알림(티켓 06). 사장님이 카드를 눌러 펼칠 때만, 카드마다 한 번 보낸다. 화면이
 * 켜져 있는 것만으로는 보내지 않는다(아무도 안 보는데 "사장님이 보고 계세요"가 나가지 않게, 리뷰 P4)
 */
export class SeenReports {
  private readonly sent = new Set<string>()

  /** 사장님이 이 카드를 펼쳤다. 서버에 알려야 하면 true(그 뒤로는 false) */
  opened(card: QuestionCard): boolean {
    if (!needsSeen(card) || this.sent.has(card.id)) return false
    this.sent.add(card.id)
    return true
  }
}

/** 질문 카드 오른쪽 위: 남은 초(1 이상), 시간 지남, 통화 끝. 문구는 `ownerText` 사전이 붙인다 */
export type QuestionClock = number | 'late' | 'ended'

/** 기다리는 중이면 남은 초, 아니면 왜 기다림이 끝났는지 */
export function questionClock(card: QuestionCard, nowMs: number): QuestionClock {
  if (card.status === 'call_ended') return 'ended'
  const left = card.status === 'open' ? secondsLeft(card, nowMs) : 0
  return left > 0 ? left : 'late'
}

/** 주문 카드 아래 접힌 "손님이 궁금해한 것" 목록 (티켓 13): 먼저 생긴 카드가 앞, 채운 카드도 남는다 */
export function knowledgeCards(state: OwnerState): KnowledgeCard[] {
  return state.knowledge
}

/** 사장님 답을 기다리는 카드 (접힌 목록 제목의 숫자) */
export function unansweredKnowledge(state: OwnerState): KnowledgeCard[] {
  return state.knowledge.filter((k) => k.status === 'open')
}

/** 자유 입력 답 글 최대 길이. 서버(`FREE_TEXT_MAX`)와 같다 */
export const WRITTEN_ANSWER_MAX = 500

/** 자유 입력 답으로 보낼 글(앞뒤 공백 뺌). 비었거나 너무 길면 null (버튼을 막는다) */
export function writtenAnswer(draft: string): string | null {
  const text = draft.trim()
  return text && text.length <= WRITTEN_ANSWER_MAX ? text : null
}

/** 마감까지 남은 초(올림). 지났으면 0. 서버가 보낸 시각부터 센 마감과 같은 시계다 */
export function secondsLeft(card: QuestionCard, nowMs: number): number {
  const left = Date.parse(card.deadline_at) - nowMs
  return Number.isNaN(left) || left <= 0 ? 0 : Math.ceil(left / 1000)
}
