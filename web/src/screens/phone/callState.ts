// 통화 화면 상태와 서버 이벤트 모양 (WS 계약은 티켓 01 Comments).
// 화면에 보이는 것과 "지금 마이크 소리를 보내도 되나"를 순수 리듀서 하나로 정한다.

export type Phase = 'listening' | 'thinking' | 'speaking' | 'ended'

export interface CartLine {
  menu_id: string
  name: string
  qty: number
  options: string[]
  request: string | null
  subtotal: number
}

export interface OrderJson {
  id: string
  status: string
  lines: { menu_id: string; quantity: number; options: string[]; request: string | null }[]
  pickup_time: string | null
  customer_name: string | null
  customer_phone: string | null
  transcript: string[]
}

export type ServerEvent =
  | {
      type: 'call.ready'
      turn: number
      call: string
      sample_rate: number
      /** 음성 끼어들기(티켓 06). 서버 스위치가 켜져 있고 이 브라우저가 할 수 있다고 알렸을 때만 온다 */
      voice_barge_in?: BargeMode
    }
  | { type: 'stt.final'; turn: number; text: string; epd_type: string; fragments: number; end_by: string }
  | {
      type: 'state'
      turn: number
      phase: Phase
      /** 말 끊기 버튼을 받나. 봇이 말하는 중이고 최종 복창·마무리·사장님 연결 안내가 아닐 때 (티켓 05) */
      interruptible: boolean
      dialog: string | null
      cart: CartLine[]
      total: number
    }
  | { type: 'bot.say'; turn: number; seq: number; text: string }
  | { type: 'bot.audio.end'; turn: number; seq: number }
  | { type: 'bot.done'; turn: number; sentences: number; interrupted?: boolean }
  | { type: 'bot.thinking'; turn: number }
  /** 손님 소리가 들려 봇 목소리를 줄인다 / 되돌린다 / 멈춘다 (음성 끼어들기, 티켓 06) */
  | { type: 'bot.duck'; turn: number }
  | { type: 'bot.unduck'; turn: number }
  | { type: 'bot.stop'; turn: number }
  | { type: 'order'; turn: number; order: OrderJson; cart: CartLine[]; total: number }
  | { type: 'metrics'; turn: number; [key: string]: unknown }
  | { type: 'error'; turn: number; code: string; message: string; retry: boolean }

export type Status = 'idle' | 'starting' | 'live' | 'ended' | 'failed'

/** on: 서버가 줄이고 멈춘다. shadow: 서버가 판정만 기록한다. 둘 다 봇 말 중에도 진짜 소리를 보낸다 */
export type BargeMode = 'on' | 'shadow'

export interface Caption {
  who: 'guest' | 'bot'
  turn: number
  text: string
}

export interface CallState {
  status: Status
  phase: Phase | null
  turn: number
  /** 봇 소리를 내는 중(서버 speaking ~ 이 턴 재생 끝 + 여유). 이 동안 마이크 대신 0을 보낸다. */
  botTalking: boolean
  /** 서버가 지금 턴의 말 끊기를 받는다(state.interruptible) */
  interruptible: boolean
  /** 이 통화의 음성 끼어들기(call.ready). null이면 반이중: 봇이 말하는 동안 0 프레임 */
  bargeIn: BargeMode | null
  /** 말 끊기로 멈춘 마지막 턴. 이 턴 이하의 봇 오디오는 버린다(8바이트 헤더의 turn). 없으면 -1 */
  cutTurn: number
  captions: Caption[]
  cart: CartLine[]
  total: number
  order: { order: OrderJson; cart: CartLine[]; total: number } | null
  mic: Record<string, unknown> | null
  error: { message: string; retry: boolean } | null
  /** AudioContext가 suspended·interrupted라 탭으로 다시 켜야 함 */
  audioBlocked: boolean
  /** 올려 보낼 소리가 소켓에 많이 쌓였다(회선이 느림). 프레임은 버리지 않고 안내만 한다 */
  slowLink: boolean
}

/** 소켓에 이만큼(약 2초 분량) 쌓이면 "회선이 느려요" 안내 (리뷰 1번). 100ms 프레임은 3,200바이트 */
export const SLOW_LINK_BYTES = 64 * 1024

export type MicFailure = 'denied' | 'no-device' | 'insecure' | 'other'

export type CallAction =
  | { type: 'dial' }
  | { type: 'mic.ready'; settings: Record<string, unknown> }
  | { type: 'mic.failed'; reason: MicFailure }
  | { type: 'server'; event: ServerEvent }
  | { type: 'playback.finished'; turn: number }
  | { type: 'interrupt' }
  | { type: 'audio.state'; state: string }
  | { type: 'link.slow'; slow: boolean }
  | { type: 'socket.closed' }
  | { type: 'hangup' }

export const initialCallState: CallState = {
  status: 'idle',
  phase: null,
  turn: 0,
  botTalking: false,
  interruptible: false,
  bargeIn: null,
  cutTurn: -1,
  captions: [],
  cart: [],
  total: 0,
  order: null,
  mic: null,
  error: null,
  audioBlocked: false,
  slowLink: false,
}

const MIC_MESSAGES: Record<MicFailure, string> = {
  denied: '마이크 권한이 꺼져 있어요. 주소창 왼쪽 자물쇠 → 마이크 → 허용으로 바꾼 뒤 다시 걸어 주세요.',
  'no-device': '마이크를 찾지 못했어요. 이어폰·마이크 연결을 확인하고 다시 걸어 주세요.',
  insecure: '이 주소에서는 마이크를 쓸 수 없어요. https 주소나 localhost로 열어 주세요.',
  other: '마이크를 켜지 못했어요. 잠시 후 다시 걸어 주세요.',
}

const CLOSED_EARLY = '서버에 연결하지 못했어요. 잠시 후 다시 걸어 주세요.'

export function callReducer(state: CallState, action: CallAction): CallState {
  switch (action.type) {
    case 'dial':
      return { ...initialCallState, status: 'starting' }
    case 'mic.ready':
      return { ...state, mic: action.settings }
    case 'mic.failed':
      return { ...state, status: 'failed', error: { message: MIC_MESSAGES[action.reason], retry: true } }
    case 'server':
      return onServer(state, action.event)
    case 'playback.finished':
      return action.turn === state.turn ? { ...state, botTalking: false } : state
    case 'interrupt':
      // 서버 왕복을 기다리지 않고 마이크를 바로 연다. 아니면 누르자마자 한 말 앞부분이 0이 된다
      // (s3-verify-barge-in 4-1). 남은 스피커 소리(outputLatency)는 에코 제거가 맡고, 서버 무음
      // 감지는 200ms 넘게 이어진 소리만 말로 본다
      if (!canInterrupt(state)) return state
      return { ...state, botTalking: false, interruptible: false, cutTurn: state.turn }
    case 'audio.state': {
      const audioBlocked = action.state === 'suspended' || action.state === 'interrupted'
      return audioBlocked === state.audioBlocked ? state : { ...state, audioBlocked }
    }
    case 'link.slow':
      return action.slow === state.slowLink ? state : { ...state, slowLink: action.slow }
    case 'socket.closed':
      if (state.status === 'live') return { ...state, status: 'ended', botTalking: false }
      if (state.status === 'starting') {
        return { ...state, status: 'failed', error: { message: CLOSED_EARLY, retry: true } }
      }
      return state
    case 'hangup':
      if (state.status === 'starting' || state.status === 'live') {
        return { ...state, status: 'ended', botTalking: false }
      }
      return state
  }
}

function onServer(state: CallState, event: ServerEvent): CallState {
  if (state.status === 'ended' || state.status === 'failed') return state
  switch (event.type) {
    case 'call.ready':
      return { ...state, status: 'live', bargeIn: event.voice_barge_in ?? null }
    case 'bot.stop':
      // 서버가 음성 끼어들기로 멈췄다. 버튼과 같이 끊되, 복창 정정처럼 버튼이 막힌 턴에서도 끊는다
      if (event.turn !== state.turn || event.turn <= state.cutTurn) return state
      return { ...state, botTalking: false, interruptible: false, cutTurn: event.turn }
    case 'state': {
      const cut = event.turn <= state.cutTurn // 끊은 턴의 늦은 상태는 마이크를 다시 닫지 않는다
      const next = {
        ...state,
        phase: event.phase,
        turn: event.turn,
        cart: event.cart,
        total: event.total,
        interruptible: event.interruptible === true && !cut,
      }
      if (event.phase === 'speaking' && !cut) next.botTalking = true
      if (event.phase === 'listening') next.botTalking = false
      return next
    }
    case 'stt.final':
      return { ...state, captions: [...state.captions, { who: 'guest', turn: event.turn, text: event.text }] }
    case 'bot.say':
      return { ...state, captions: [...state.captions, { who: 'bot', turn: event.turn, text: event.text }] }
    case 'order':
      return { ...state, order: { order: event.order, cart: event.cart, total: event.total } }
    case 'error':
      return {
        ...state,
        status: 'failed',
        botTalking: false,
        interruptible: false,
        error: { message: event.message, retry: event.retry },
      }
    default:
      return state
  }
}

/**
 * 지금 올리는 프레임에 진짜 마이크 소리를 담아도 되나. 거짓이면 같은 속도로 0 프레임.
 * 음성 끼어들기가 켜진 통화(안드로이드 크롬)는 봇이 말하는 동안에도 진짜 소리다. 판정은 서버가 한다.
 */
export function micLive(state: CallState): boolean {
  return state.status === 'live' && (!state.botTalking || state.bargeIn !== null)
}

/** "말 끝" 버튼(user.end_of_speech)을 받을 수 있는 국면인가. 서버도 listening·thinking만 받는다. */
export function canEndSpeech(state: CallState): boolean {
  return state.status === 'live' && (state.phase === 'listening' || state.phase === 'thinking')
}

/** "말 끊기" 버튼을 보이고 받을 수 있나. 서버가 허용한 턴의 봇 소리가 나는 중일 때만. */
export function canInterrupt(state: CallState): boolean {
  return (
    state.status === 'live' &&
    state.phase === 'speaking' &&
    state.interruptible &&
    state.botTalking &&
    state.turn > state.cutTurn
  )
}
