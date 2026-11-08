import { describe, expect, it } from 'vitest'
import {
  type CallAction,
  type CallState,
  type ServerEvent,
  callReducer,
  canEndSpeech,
  initialCallState,
  micLive,
} from './callState.ts'

function run(...actions: CallAction[]): CallState {
  return actions.reduce(callReducer, initialCallState)
}

const dial: CallAction = { type: 'dial' }
const micReady: CallAction = { type: 'mic.ready', settings: { echoCancellation: 'all', sampleRate: 16000 } }
const ready: CallAction = { type: 'server', event: { type: 'call.ready', turn: 0, call: 'c1', sample_rate: 16000 } }
const speaking = (turn: number): CallAction => ({
  type: 'server',
  event: { type: 'state', turn, phase: 'speaking', dialog: 'ordering', cart: [], total: 0 },
})
const listening = (turn: number): CallAction => ({
  type: 'server',
  event: { type: 'state', turn, phase: 'listening', dialog: 'ordering', cart: [], total: 0 },
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

  it('shows the server error message and offers a redial when retry is true', () => {
    const s = run(dial, micReady, ready, {
      type: 'server',
      event: { type: 'error', turn: 1, code: 'tts', message: '잠시 후 다시 걸어 주세요.', retry: true },
    }, { type: 'socket.closed' })
    expect(s.status).toBe('failed')
    expect(s.error).toEqual({ message: '잠시 후 다시 걸어 주세요.', retry: true })
    expect(micLive(s)).toBe(false)
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

  it('ends locally on hangup', () => {
    const s = run(dial, micReady, ready, { type: 'hangup' })
    expect(s.status).toBe('ended')
    expect(micLive(s)).toBe(false)
  })
})
