// 통화 화면 상태와 서버 이벤트 모양 (WS 계약은 티켓 01 Comments).
// 화면에 보이는 것과 "지금 마이크 소리를 보내도 되나"를 순수 리듀서 하나로 정한다.
import type { CustomerText } from '../customerText.ts'

export type { CustomerText } from '../customerText.ts'

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
      /**
       * 반이중에서 마이크를 닫는 때(6단계 티켓 02). 'playback'이면 `state: speaking`이 아니라 이 턴의
       * 봇 소리가 실제로 재생되기 시작할 때 닫는다. 없으면(낡은 서버·`PHONE_MIC_CLOSE=state`) 전처럼
       */
      mic_close?: 'playback'
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
  /**
   * 서버가 이 손님 턴을 되감았다(6단계 티켓 02): 봇 답의 첫 소리 전에 손님이 말을 이어서, 앞 말과
   * 합친 새 턴이 곧 온다. 이 턴의 `stt.final` 자막을 거둔다. 봇 소리·자막은 나간 적이 없다
   */
  | { type: 'turn.superseded'; turn: number }
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
  /** 대화가 끝나 서버가 통화를 끝낸다(리뷰 S13). 마지막 인사 재생 뒤 소켓이 닫힌다. 위로 보내는 `call.end`는 손님이 끊은 것 */
  | {
      type: 'call.ended'
      turn: number
      reason: string
      /** 그때까지 손님에게 보낸 문자(티켓 07). 사장님 허브 없이 띄운 서버는 빈 목록 */
      sms?: CustomerText[]
    }

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
  /**
   * 봇 소리를 내는 중. 이 동안 마이크 대신 0을 보낸다. 끝은 이 턴 재생 끝 + 여유.
   * 시작은 `micCloseAt`에 따라 서버 speaking(state) 또는 이 턴의 실제 재생 시작(playback)
   */
  botTalking: boolean
  /**
   * 마이크를 닫는 때(`call.ready.mic_close`, 6단계 티켓 02). 서버가 첫 오디오를 보낸 때부터 스피커로
   * 소리가 나갈 때까지는 마이크에 봇 소리가 없다. playback이면 그 사이 손님 말을 그대로 올린다
   */
  micCloseAt: 'state' | 'playback'
  /** 재생 시작으로 마이크를 닫은 마지막 턴. 턴마다 한 번만 닫는다(재생이 끝나 연 뒤 다시 닫지 않게). 없으면 -1 */
  begunTurn: number
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
  /** 서버가 통화를 끝낸 이유(`call.ended`): confirmed · handoff · cancelled · no_input. 손님이 끊었으면 null */
  endReason: string | null
  /** 이 통화의 id(`call.ready`). 통화가 끝나면 이 id로 "받은 문자" 스트림을 연다 (티켓 07) */
  callId: string | null
  /** 받은 문자(보낸 순서). `call.ended`와 통화 뒤 문자 스트림으로 온다 (티켓 07) */
  texts: CustomerText[]
}

/** 소켓에 이만큼(약 2초 분량) 쌓이면 "회선이 느려요" 안내 (리뷰 1번). 100ms 프레임은 3,200바이트 */
export const SLOW_LINK_BYTES = 64 * 1024

export type MicFailure = 'denied' | 'no-device' | 'insecure' | 'other'

export type CallAction =
  | { type: 'dial' }
  | { type: 'mic.ready'; settings: Record<string, unknown> }
  | { type: 'mic.failed'; reason: MicFailure }
  | { type: 'server'; event: ServerEvent }
  /** 이 턴의 첫 봇 소리가 재생되기 시작한다(예약 시각에 닿았다). 여기서부터 마이크를 닫는다 */
  | { type: 'playback.begun'; turn: number }
  | { type: 'playback.finished'; turn: number }
  | { type: 'interrupt' }
  | { type: 'audio.state'; state: string }
  | { type: 'link.slow'; slow: boolean }
  | { type: 'socket.closed'; code?: number }
  | { type: 'hangup' }
  /** 문자 스트림(`/api/phone/calls/{id}/sms`)으로 온 문자. 통화가 끝난 뒤에도 받는다 (티켓 07) */
  | { type: 'texts'; texts: CustomerText[] }

export const initialCallState: CallState = {
  status: 'idle',
  phase: null,
  turn: 0,
  botTalking: false,
  micCloseAt: 'state',
  begunTurn: -1,
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
  endReason: null,
  callId: null,
  texts: [],
}

const MIC_MESSAGES: Record<MicFailure, string> = {
  denied: '마이크 권한이 꺼져 있어요. 주소창 왼쪽 자물쇠 → 마이크 → 허용으로 바꾼 뒤 다시 걸어 주세요.',
  'no-device': '마이크를 찾지 못했어요. 이어폰·마이크 연결을 확인하고 다시 걸어 주세요.',
  insecure: '이 주소에서는 마이크를 쓸 수 없어요. https 주소나 localhost로 열어 주세요.',
  other: '마이크를 켜지 못했어요. 잠시 후 다시 걸어 주세요.',
}

const CLOSED_EARLY = '서버에 연결하지 못했어요. 잠시 후 다시 걸어 주세요.'
/** 서버가 다시 뜨는 중에 끊겼다(WS 닫힘 코드 1012 서비스 재시작·1001 떠남, 6단계) */
const CLOSED_RESTART = '서버 점검으로 통화가 끊겼어요. 1분쯤 뒤에 다시 걸어 주세요.'
const RESTART_CODES = [1001, 1012]

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
    case 'playback.begun':
      // 지금 말하는 턴의 소리만. 끊은 턴의 늦은 알림은 마이크를 다시 닫지 않는다
      if (state.status !== 'live' || state.phase !== 'speaking' || state.botTalking) return state
      if (action.turn !== state.turn || action.turn <= state.cutTurn) return state
      // 턴마다 한 번만. 재생이 끝나 마이크를 연 뒤(서버의 listening 전)에 다시 닫지 않는다
      if (action.turn <= state.begunTurn) return state
      return { ...state, botTalking: true, begunTurn: action.turn }
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
    case 'socket.closed': {
      const restart = action.code !== undefined && RESTART_CODES.includes(action.code)
      if (state.status === 'live') {
        // 주문이 끝난 뒤(call.ended)에 닫힌 것은 정상 종료다. 그 전에 서버가 내려가면 이유를 보여 준다
        if (restart && state.endReason === null) {
          return { ...state, status: 'failed', botTalking: false, error: { message: CLOSED_RESTART, retry: true } }
        }
        return { ...state, status: 'ended', botTalking: false }
      }
      if (state.status === 'starting') {
        const message = restart ? CLOSED_RESTART : CLOSED_EARLY
        return { ...state, status: 'failed', error: { message, retry: true } }
      }
      return state
    }
    case 'hangup':
      if (state.status === 'starting' || state.status === 'live') {
        return { ...state, status: 'ended', botTalking: false }
      }
      return state
    case 'texts':
      return withTexts(state, action.texts)
  }
}

/** 받은 문자에 더한다. 이미 받은 id는 버린다(스트림 snapshot은 `call.ended`와 겹친다) */
function withTexts(state: CallState, texts: CustomerText[]): CallState {
  const known = new Set(state.texts.map((t) => t.id))
  const fresh = texts.filter((t) => !known.has(t.id))
  return fresh.length === 0 ? state : { ...state, texts: [...state.texts, ...fresh] }
}

function onServer(state: CallState, event: ServerEvent): CallState {
  if (state.status === 'ended' || state.status === 'failed') return state
  switch (event.type) {
    case 'call.ready':
      return {
        ...state,
        status: 'live',
        bargeIn: event.voice_barge_in ?? null,
        micCloseAt: event.mic_close === 'playback' ? 'playback' : 'state',
        callId: event.call,
      }
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
      // playback이면 여기서 닫지 않는다. 이 턴의 소리가 실제로 시작할 때(`playback.begun`) 닫는다
      if (event.phase === 'speaking' && !cut && state.micCloseAt === 'state') next.botTalking = true
      if (event.phase === 'listening') next.botTalking = false
      return next
    }
    case 'stt.final':
      return { ...state, captions: [...state.captions, { who: 'guest', turn: event.turn, text: event.text }] }
    case 'turn.superseded':
      return { ...state, captions: state.captions.filter((c) => !(c.who === 'guest' && c.turn === event.turn)) }
    case 'bot.say':
      return { ...state, captions: [...state.captions, { who: 'bot', turn: event.turn, text: event.text }] }
    case 'order':
      return { ...state, order: { order: event.order, cart: event.cart, total: event.total } }
    case 'call.ended':
      // 끝난 이유만 남긴다. 마지막 인사가 아직 재생 중일 수 있어 끝내기는 소켓 닫힘이 한다
      return withTexts({ ...state, endReason: event.reason }, event.sms ?? [])
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
