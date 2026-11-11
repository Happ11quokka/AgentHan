import { describe, expect, it } from 'vitest'
import {
  type CallAction,
  type CallState,
  type CustomerText,
  type ServerEvent,
  callReducer,
  canEndSpeech,
  canInterrupt,
  initialCallState,
  micLive,
} from './callState.ts'

function run(...actions: CallAction[]): CallState {
  return actions.reduce(callReducer, initialCallState)
}

const dial: CallAction = { type: 'dial' }
const micReady: CallAction = { type: 'mic.ready', settings: { echoCancellation: 'all', sampleRate: 16000 } }
const ready: CallAction = { type: 'server', event: { type: 'call.ready', turn: 0, call: 'c1', sample_rate: 16000 } }
const speaking = (turn: number, interruptible = true): CallAction => ({
  type: 'server',
  event: { type: 'state', turn, phase: 'speaking', interruptible, dialog: 'ordering', cart: [], total: 0 },
})
const listening = (turn: number): CallAction => ({
  type: 'server',
  event: { type: 'state', turn, phase: 'listening', interruptible: false, dialog: 'ordering', cart: [], total: 0 },
})
const interrupt: CallAction = { type: 'interrupt' }

describe('voice barge-in (ticket 06)', () => {
  const readyWith = (mode: 'on' | 'shadow'): CallAction => ({
    type: 'server',
    event: { type: 'call.ready', turn: 0, call: 'c1', sample_rate: 16000, voice_barge_in: mode },
  })
  const stop = (turn: number): CallAction => ({ type: 'server', event: { type: 'bot.stop', turn } })

  it('keeps half duplex unless the server turned voice barge-in on for this call', () => {
    expect(micLive(run(dial, micReady, ready, speaking(1)))).toBe(false)
    expect(micLive(run(dial, micReady, readyWith('on'), speaking(1)))).toBe(true)
    expect(micLive(run(dial, micReady, readyWith('shadow'), speaking(3, false)))).toBe(true)
  })

  it('cuts the turn on bot.stop even where the button is blocked (readback correction)', () => {
    const s = run(dial, micReady, readyWith('on'), speaking(3, false), stop(3))
    expect(s.botTalking).toBe(false)
    expect(s.cutTurn).toBe(3)
    // 끊은 턴의 늦은 speaking 상태는 다시 말하는 중으로 돌리지 않는다
    expect(callReducer(s, speaking(3, false)).botTalking).toBe(false)
  })

  it('ignores a bot.stop for another turn', () => {
    const s = run(dial, micReady, readyWith('on'), speaking(2), stop(1))
    expect(s.botTalking).toBe(true)
    expect(s.cutTurn).toBe(-1)
  })
})

describe('mic closes at playback start (6단계 티켓 02)', () => {
  const readyPlayback: CallAction = {
    type: 'server',
    event: { type: 'call.ready', turn: 0, call: 'c1', sample_rate: 16000, mic_close: 'playback' },
  }
  const begun = (turn: number): CallAction => ({ type: 'playback.begun', turn })

  it('keeps the mic live after state speaking until this turn starts to play', () => {
    const waiting = run(dial, micReady, readyPlayback, speaking(1))
    expect(waiting.botTalking).toBe(false)
    expect(micLive(waiting)).toBe(true)
    const playing = callReducer(waiting, begun(1))
    expect(micLive(playing)).toBe(false)
    expect(micLive(callReducer(playing, { type: 'playback.finished', turn: 1 }))).toBe(true)
  })

  it('offers the interrupt button only once the sound has started', () => {
    const waiting = run(dial, micReady, readyPlayback, speaking(1))
    expect(canInterrupt(waiting)).toBe(false)
    expect(canInterrupt(callReducer(waiting, begun(1)))).toBe(true)
  })

  it('ignores a playback start of another turn, of a cut turn, or outside the speaking phase', () => {
    const waiting = run(dial, micReady, readyPlayback, speaking(2))
    expect(callReducer(waiting, begun(1))).toBe(waiting)
    const cut = run(dial, micReady, readyPlayback, speaking(2), begun(2), interrupt)
    expect(micLive(callReducer(cut, begun(2)))).toBe(true)
    const listeningNow = run(dial, micReady, readyPlayback, speaking(2), begun(2), listening(2))
    expect(callReducer(listeningNow, begun(2))).toBe(listeningNow)
  })

  it('does not close the mic again after this turn finished playing', () => {
    // 재생이 끝나 마이크를 연 뒤, 서버의 listening이 오기 전에 같은 턴의 재생 시작이 또 보고돼도 닫지 않는다
    const finished = run(dial, micReady, readyPlayback, speaking(1), begun(1), {
      type: 'playback.finished',
      turn: 1,
    })
    expect(micLive(finished)).toBe(true)
    expect(micLive(callReducer(finished, begun(1)))).toBe(true)
    // 다음 턴은 다시 닫는다
    expect(micLive(run(dial, micReady, readyPlayback, speaking(1), begun(1), listening(1), speaking(2), begun(2)))).toBe(
      false,
    )
  })

  it('still closes at state speaking when the server did not ask for playback timing', () => {
    expect(micLive(run(dial, micReady, ready, speaking(1)))).toBe(false)
  })

  it('never closes the mic of a voice barge-in call', () => {
    const barge: CallAction = {
      type: 'server',
      event: { type: 'call.ready', turn: 0, call: 'c1', sample_rate: 16000, voice_barge_in: 'on' },
    }
    expect(micLive(run(dial, micReady, barge, speaking(1), begun(1)))).toBe(true)
  })
})

describe('말 끊기 button (ticket 05)', () => {
  it('is offered only while the bot speaks a turn the server marks interruptible', () => {
    expect(canInterrupt(run(dial, micReady, ready, speaking(1)))).toBe(true)
    expect(canInterrupt(run(dial, micReady, ready, speaking(3, false)))).toBe(false) // 최종 복창
    expect(canInterrupt(run(dial, micReady, ready, listening(1)))).toBe(false)
    expect(canInterrupt(run(dial, micReady, ready, speaking(1), { type: 'hangup' }))).toBe(false)
  })

  it('opens the mic at once on press, without waiting for the server', () => {
    const s = run(dial, micReady, ready, speaking(1), interrupt)
    expect(micLive(s)).toBe(true)
    expect(canInterrupt(s)).toBe(false)
    expect(s.cutTurn).toBe(1)
  })

  it('does nothing when the turn is not interruptible (readback stays half duplex)', () => {
    const talking = run(dial, micReady, ready, speaking(3, false))
    const pressed = callReducer(talking, interrupt)
    expect(pressed).toBe(talking)
    expect(micLive(pressed)).toBe(false)
  })

  it('keeps the mic open if a stale speaking state of the cut turn arrives', () => {
    const s = run(dial, micReady, ready, speaking(1), interrupt, speaking(1))
    expect(micLive(s)).toBe(true)
    expect(canInterrupt(s)).toBe(false)
  })

  it('goes back to half duplex when the next turn speaks', () => {
    const s = run(dial, micReady, ready, speaking(1), interrupt, listening(1), speaking(2))
    expect(micLive(s)).toBe(false)
    expect(canInterrupt(s)).toBe(true)
  })
})

describe('callReducer', () => {
  it('goes live on call.ready and keeps the applied mic settings for the screen', () => {
    const s = run(dial, micReady, ready)
    expect(s.status).toBe('live')
    expect(s.mic).toEqual({ echoCancellation: 'all', sampleRate: 16000 })
    expect(micLive(s)).toBe(true)
  })

  it('does not send mic audio before call.ready', () => {
    expect(micLive(run(dial, micReady))).toBe(false)
  })

  it('sends zeros instead of the mic from bot speaking until local playback has finished', () => {
    const talking = run(dial, micReady, ready, speaking(0))
    expect(micLive(talking)).toBe(false)
    const done = callReducer(talking, { type: 'playback.finished', turn: 0 })
    expect(micLive(done)).toBe(true)
  })

  it('ignores a playback.finished from an older turn', () => {
    const s = run(dial, micReady, ready, speaking(0), listening(0), speaking(1), { type: 'playback.finished', turn: 0 })
    expect(micLive(s)).toBe(false)
  })

  it('takes back the caption of a turn the server rewound (turn.superseded)', () => {
    // 6단계 티켓 02: "아니요" 뒤에 "그게 다예요"를 덧붙이면 서버가 앞 턴을 되감고 합친 말로 새 턴을 연다
    const final = (turn: number, text: string): CallAction => ({
      type: 'server',
      event: { type: 'stt.final', turn, text, epd_type: 'endPoint', fragments: 1, end_by: 'vad' },
    })
    const s = run(
      dial,
      micReady,
      ready,
      { type: 'server', event: { type: 'bot.say', turn: 1, seq: 0, text: '더 필요하신 거 있으세요?' } },
      final(2, '아니요'),
      { type: 'server', event: { type: 'turn.superseded', turn: 2 } },
      final(3, '아니요 그게 다예요'),
    )
    expect(s.captions).toEqual([
      { who: 'bot', turn: 1, text: '더 필요하신 거 있으세요?' },
      { who: 'guest', turn: 3, text: '아니요 그게 다예요' },
    ])
  })

  it('shows guest and bot captions in order', () => {
    const s = run(
      dial,
      micReady,
      ready,
      { type: 'server', event: { type: 'bot.say', turn: 0, seq: 0, text: '네, 달빛제과입니다.' } },
      { type: 'server', event: { type: 'stt.final', turn: 1, text: '메도빅 하나요', epd_type: 'gap', fragments: 1, end_by: 'vad' } },
      { type: 'server', event: { type: 'bot.say', turn: 1, seq: 0, text: '메도빅 하나 담았어요.' } },
    )
    expect(s.captions).toEqual([
      { who: 'bot', turn: 0, text: '네, 달빛제과입니다.' },
      { who: 'guest', turn: 1, text: '메도빅 하나요' },
      { who: 'bot', turn: 1, text: '메도빅 하나 담았어요.' },
    ])
  })

  it('tracks the server phase and cart from state events', () => {
    const s = run(dial, micReady, ready, {
      type: 'server',
      event: {
        type: 'state',
        turn: 2,
        phase: 'thinking',
        interruptible: false,
        dialog: 'ordering',
        cart: [{ menu_id: 'm1', name: '메도빅', qty: 2, options: [], request: null, subtotal: 15000 }],
        total: 15000,
      },
    })
    expect(s.phase).toBe('thinking')
    expect(s.turn).toBe(2)
    expect(s.total).toBe(15000)
    expect(s.cart).toHaveLength(1)
  })

  it('lets the guest press 말 끝 only while listening or thinking', () => {
    expect(canEndSpeech(run(dial, micReady, ready, listening(0)))).toBe(true)
    expect(canEndSpeech(run(dial, micReady, ready, speaking(0)))).toBe(false)
    expect(canEndSpeech(run(dial, micReady))).toBe(false)
  })

  it('keeps the order card after the server closes the call', () => {
    const order: ServerEvent = {
      type: 'order',
      turn: 4,
      order: { id: 'o1', status: 'confirmed', lines: [], pickup_time: '2026-11-08T18:30:00+09:00', customer_name: null, customer_phone: null, transcript: [] },
      cart: [],
      total: 7500,
    }
    const s = run(dial, micReady, ready, { type: 'server', event: order }, { type: 'socket.closed' })
    expect(s.status).toBe('ended')
    expect(s.order?.total).toBe(7500)
    expect(s.order?.order.status).toBe('confirmed')
  })

  it('keeps why the server ended the call (call.ended) for the end-of-call screen', () => {
    // 리뷰 S13: 서버가 끝낸 이유는 아래쪽 call.ended로 온다. 남은 재생은 소켓이 닫힐 때까지 둔다
    const ended: CallAction = { type: 'server', event: { type: 'call.ended', turn: 3, reason: 'no_input' } }
    const live = run(dial, micReady, ready, ended)
    expect(live.status).toBe('live')
    expect(live.endReason).toBe('no_input')
    const closed = callReducer(live, { type: 'socket.closed' })
    expect(closed.status).toBe('ended')
    expect(closed.endReason).toBe('no_input')
    expect(run(dial, micReady, ready, { type: 'socket.closed' }).endReason).toBeNull()
  })

  it('shows the server error message and offers a redial when retry is true', () => {
    const s = run(dial, micReady, ready, {
      type: 'server',
      event: { type: 'error', turn: 1, code: 'tts', message: '잠시 후 다시 걸어 주세요.', retry: true },
    }, { type: 'socket.closed' })
    expect(s.status).toBe('failed')
    expect(s.error).toEqual({ message: '잠시 후 다시 걸어 주세요.', retry: true })
    expect(micLive(s)).toBe(false)
  })

  it('shows a server refusal (busy, daily limit, maintenance) and hides redial when retry is false', () => {
    // 6단계 문지기: 거절은 busy와 같은 error 이벤트로 온다. 문구는 서버가 준 그대로 보여 준다
    const s = run(dial, micReady, {
      type: 'server',
      event: { type: 'error', turn: 0, code: 'closed_today', message: '오늘은 전화 주문이 마감됐어요. 내일 다시 걸어 주세요.', retry: false },
    }, { type: 'socket.closed', code: 1000 })
    expect(s.status).toBe('failed')
    expect(s.error).toEqual({ message: '오늘은 전화 주문이 마감됐어요. 내일 다시 걸어 주세요.', retry: false })
  })

  it('tells the caller the server is restarting when the socket closes with 1012 mid-call', () => {
    const s = run(dial, micReady, ready, { type: 'socket.closed', code: 1012 })
    expect(s.status).toBe('failed')
    expect(s.error?.message).toContain('점검')
    expect(s.error?.retry).toBe(true)
    // 서버가 먼저 이유를 보냈으면 그 문구를 둔다
    const told = run(dial, micReady, ready, {
      type: 'server',
      event: { type: 'error', turn: 2, code: 'restarting', message: '서버 점검으로 통화를 끝낼게요.', retry: true },
    }, { type: 'socket.closed', code: 1012 })
    expect(told.error?.message).toBe('서버 점검으로 통화를 끝낼게요.')
    // 주문이 끝난 뒤 닫힌 것은 정상 종료
    const ended: CallAction = { type: 'server', event: { type: 'call.ended', turn: 3, reason: 'confirmed' } }
    expect(run(dial, micReady, ready, ended, { type: 'socket.closed', code: 1012 }).status).toBe('ended')
  })

  it('treats a socket that closes before call.ready as a failed call', () => {
    const s = run(dial, micReady, { type: 'socket.closed' })
    expect(s.status).toBe('failed')
    expect(s.error?.retry).toBe(true)
  })

  it('reports a denied microphone with guidance', () => {
    const s = run(dial, { type: 'mic.failed', reason: 'denied' })
    expect(s.status).toBe('failed')
    expect(s.error?.message).toMatch(/마이크/)
    expect(s.error?.retry).toBe(true)
  })

  it('asks for a tap when the audio context is suspended or interrupted, and clears it when running', () => {
    const blocked = run(dial, micReady, ready, { type: 'audio.state', state: 'interrupted' })
    expect(blocked.audioBlocked).toBe(true)
    expect(callReducer(blocked, { type: 'audio.state', state: 'running' }).audioBlocked).toBe(false)
  })

  it('starts fresh on redial', () => {
    const failed = run(dial, { type: 'mic.failed', reason: 'denied' })
    const again = callReducer(failed, dial)
    expect(again.status).toBe('starting')
    expect(again.error).toBeNull()
    expect(again.captions).toEqual([])
  })

  it('stays idle when hangup comes before any call (screen unmount)', () => {
    expect(run({ type: 'hangup' }).status).toBe('idle')
  })

  it('ends locally on hangup', () => {
    const s = run(dial, micReady, ready, { type: 'hangup' })
    expect(s.status).toBe('ended')
    expect(micLive(s)).toBe(false)
  })
})

describe('received texts after the call (4단계 티켓 07)', () => {
  const orderText: CustomerText = {
    id: 'c1-s1',
    call_id: 'c1',
    kind: 'order',
    text: '주문이 확정됐어요. 메도빅 1개, 합계 7,500원. 18:30에 준비해 둘게요.',
    sent_at: '2026-11-08T16:05:00+09:00',
    sent_label: '16:05',
    question_id: '',
    translate_failed: false,
  }
  const lateText: CustomerText = {
    ...orderText,
    id: 'c1-s2',
    kind: 'late_answer',
    text: '메도빅 문의 답변이에요. 사장님께 여쭤보니 네, 된다고 하세요.',
    sent_label: '16:07',
    question_id: 'c1-q1',
  }
  const ended: CallAction = {
    type: 'server',
    event: { type: 'call.ended', turn: 6, reason: 'confirmed', sms: [orderText] },
  }

  it('remembers the call id so the end-of-call screen can open the text stream', () => {
    expect(run(dial, micReady, ready).callId).toBe('c1')
    expect(run(dial, micReady, ready, { type: 'hangup' }, dial).callId).toBeNull()
  })

  it('shows the texts that came with the end of the call', () => {
    const s = run(dial, micReady, ready, ended, { type: 'socket.closed' })
    expect(s.texts).toEqual([orderText])
  })

  it('takes texts from the stream after the call ended, once each, in sent order', () => {
    const s = run(
      dial,
      micReady,
      ready,
      ended,
      { type: 'socket.closed' },
      { type: 'texts', texts: [orderText] }, // 스트림 snapshot: 이미 받은 문자
      { type: 'texts', texts: [lateText] }, // 늦은 사장님 답
      { type: 'texts', texts: [lateText] },
    )
    expect(s.texts.map((t) => t.id)).toEqual(['c1-s1', 'c1-s2'])
  })

  it('keeps texts when the customer hung up before the server ended the call', () => {
    const s = run(dial, micReady, ready, { type: 'hangup' }, { type: 'texts', texts: [orderText] })
    expect(s.texts).toEqual([orderText])
  })
})
