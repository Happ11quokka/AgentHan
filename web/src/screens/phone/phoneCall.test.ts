// PhoneCall 컨트롤러를 브라우저 API 가짜로 돌려 본다(리뷰 1·4·9번).
// 마이크·AudioContext·WebSocket은 최소한의 가짜다. 보는 것은 화면 상태(getState)와 소켓으로 나간 것뿐이다.
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { PhoneCall, phoneOptions } from './phoneCall.ts'

type Listener = (e: { data: unknown }) => void

class FakeAudioContext {
  static instances: FakeAudioContext[] = []
  static startState: AudioContextState = 'running'
  state: AudioContextState = FakeAudioContext.startState
  sampleRate = 16000
  outputLatency = 0.02
  baseLatency = 0.01
  currentTime = 0
  destination = {}
  onstatechange: (() => void) | null = null
  audioWorklet = { addModule: async () => {} }
  constructor() {
    FakeAudioContext.instances.push(this)
  }
  async resume() {}
  async close() {}
  createMediaStreamSource() {
    return { connect: (next: unknown) => next, disconnect() {} }
  }
  gains: FakeGain[] = []
  createGain() {
    const g = new FakeGain()
    this.gains.push(g)
    return g
  }
  sources: FakeSource[] = []
  async decodeAudioData(data: ArrayBuffer) {
    return { decodedFrom: data }
  }
  createBuffer(_channels: number, length: number) {
    return { length, copyToChannel() {} }
  }
  createBufferSource() {
    const source = new FakeSource()
    this.sources.push(source)
    return source
  }
}

class FakeGain {
  /** setTargetAtTime은 목표값을 바로 value에 둔다(시정수는 따로 남긴다) */
  gain = {
    value: 1,
    timeConstant: 0,
    setTargetAtTime(v: number, _when: number, tc: number) {
      this.value = v
      this.timeConstant = tc
    },
    cancelScheduledValues() {},
  }
  connect(next: unknown) {
    return next
  }
  disconnect() {}
}

class FakeSource {
  buffer: unknown = null
  loop = false
  started = false
  stopped = false
  onended: (() => void) | null = null
  connect(next: unknown) {
    return next
  }
  disconnect() {}
  start() {
    this.started = true
  }
  /** 실제처럼 `when`이 미래면 예약만 한다(그때까지는 울린다). */
  stop(when?: number) {
    if (when === undefined || when <= 0) this.stopped = true
  }
}

const CUE_BYTES = new ArrayBuffer(16) // 가짜 효과음 파일 내용

/** 효과음 파일로 만든(디코드한) 재생 소스들. 봇 음성 조각 소스와 구별한다. */
function cueSources(ctx: FakeAudioContext): FakeSource[] {
  return ctx.sources.filter((s) => (s.buffer as { decodedFrom?: unknown })?.decodedFrom === CUE_BYTES)
}

/** 서버가 내리는 오디오 프레임(8바이트 헤더 + PCM 100샘플). */
function audioFrame(turn: number, seq: number): ArrayBuffer {
  const buf = new ArrayBuffer(8 + 200)
  const view = new DataView(buf)
  view.setUint8(0, 1)
  view.setUint16(2, seq, true)
  view.setUint32(4, turn, true)
  return buf
}

class FakeWorkletNode {
  static last: FakeWorkletNode | null = null
  port: { onmessage: Listener | null } = { onmessage: null }
  constructor() {
    FakeWorkletNode.last = this
  }
  connect(next: unknown) {
    return next
  }
  disconnect() {}
}

class FakeSocket {
  static CONNECTING = 0
  static OPEN = 1
  static CLOSED = 3
  static instances: FakeSocket[] = []
  readyState = FakeSocket.CONNECTING
  bufferedAmount = 0
  binaryType = 'blob'
  sent: unknown[] = []
  onopen: (() => void) | null = null
  onmessage: Listener | null = null
  onclose: (() => void) | null = null
  readonly url: string
  constructor(url: string) {
    this.url = url
    FakeSocket.instances.push(this)
  }
  send(data: unknown) {
    this.sent.push(data)
  }
  close() {
    this.readyState = FakeSocket.CLOSED
  }
  open() {
    this.readyState = FakeSocket.OPEN
    this.onopen?.()
  }
  server(event: Record<string, unknown>) {
    this.onmessage?.({ data: JSON.stringify(event) })
  }
}

function deferred<T>() {
  let resolve!: (v: T) => void
  const promise = new Promise<T>((r) => (resolve = r))
  return { promise, resolve }
}

const track = { stop: vi.fn(), getSettings: () => ({ sampleRate: 16000, echoCancellation: 'all' }) }
const stream = { getTracks: () => [track], getAudioTracks: () => [track] }
let mic = deferred<typeof stream>()
let doc: EventTarget & { visibilityState: string }

const flush = () => new Promise((r) => setTimeout(r, 0))

beforeEach(() => {
  FakeAudioContext.instances = []
  FakeAudioContext.startState = 'running'
  FakeSocket.instances = []
  FakeWorkletNode.last = null
  track.stop.mockClear()
  mic = deferred<typeof stream>()
  doc = Object.assign(new EventTarget(), { visibilityState: 'visible' })
  vi.stubGlobal('window', { isSecureContext: true, setTimeout, clearTimeout })
  vi.stubGlobal('document', doc)
  vi.stubGlobal('location', { protocol: 'http:', host: 'localhost:5173' })
  vi.stubGlobal('navigator', { userAgent: 'test', mediaDevices: { getUserMedia: () => mic.promise } })
  vi.stubGlobal('AudioContext', FakeAudioContext)
  vi.stubGlobal('AudioWorkletNode', FakeWorkletNode)
  vi.stubGlobal('WebSocket', FakeSocket)
  vi.stubGlobal('fetch', async () => ({ ok: true, arrayBuffer: async () => CUE_BYTES }))
})

afterEach(() => {
  vi.unstubAllGlobals()
})

async function liveCall(): Promise<{ call: PhoneCall; ws: FakeSocket }> {
  const call = new PhoneCall()
  call.attach()
  call.dial()
  mic.resolve(stream)
  await flush()
  const ws = FakeSocket.instances[0]
  ws.open()
  ws.server({ type: 'call.ready', turn: 0, call: 'c1', sample_rate: 16000 })
  return { call, ws }
}

describe('PhoneCall', () => {
  it('stays hung up when the mic permission resolves after the guest hung up', async () => {
    const call = new PhoneCall()
    call.dial()
    call.hangup() // 권한 창이 떠 있는 동안 끊었다
    mic.resolve(stream)
    await flush()
    expect(call.getState().status).toBe('ended')
    expect(call.getState().error).toBeNull()
    expect(track.stop).toHaveBeenCalled()
    expect(FakeSocket.instances).toHaveLength(0)
  })

  it('shows the resume button when the audio context starts suspended and never changes', async () => {
    FakeAudioContext.startState = 'suspended'
    const call = new PhoneCall()
    call.dial()
    await flush()
    expect(call.getState().audioBlocked).toBe(true)
  })

  it('shows the resume button when the page comes back and audio is not running', async () => {
    const { call } = await liveCall()
    expect(call.getState().audioBlocked).toBe(false)
    FakeAudioContext.instances[0].state = 'suspended' // statechange 없이 멈춤
    doc.dispatchEvent(new Event('visibilitychange'))
    await flush()
    expect(call.getState().audioBlocked).toBe(true)
  })

  it('warns about a slow line while the socket buffer is large but keeps sending frames', async () => {
    const { call, ws } = await liveCall()
    const frame = new ArrayBuffer(3200)
    ws.bufferedAmount = 70_000
    FakeWorkletNode.last!.port.onmessage!({ data: frame })
    expect(call.getState().slowLink).toBe(true)
    ws.bufferedAmount = 0
    FakeWorkletNode.last!.port.onmessage!({ data: frame })
    expect(call.getState().slowLink).toBe(false)
    expect(ws.sent.filter((m) => m instanceof ArrayBuffer)).toHaveLength(2)
  })
})

describe('thinking cue (bot.thinking)', () => {
  const thinking = (turn: number) => ({ type: 'state', turn, phase: 'thinking', dialog: 'ordering', cart: [], total: 0 })

  it('plays the generated cue on a loop when the server says the answer is late', async () => {
    const { ws } = await liveCall()
    await flush() // 효과음 파일을 받아 디코드한다
    ws.server(thinking(1))
    ws.server({ type: 'bot.thinking', turn: 1 })
    const ctx = FakeAudioContext.instances[0]
    const [cue] = cueSources(ctx)
    expect(cue.started).toBe(true)
    expect(cue.loop).toBe(true)
    expect(cue.stopped).toBe(false)
  })

  it('stops the cue as soon as the bot voice for that turn arrives', async () => {
    const { ws } = await liveCall()
    await flush()
    ws.server(thinking(1))
    ws.server({ type: 'bot.thinking', turn: 1 })
    ws.onmessage!({ data: audioFrame(1, 0) })
    const [cue] = cueSources(FakeAudioContext.instances[0])
    expect(cue.stopped).toBe(true)
  })

  it('stops the cue when the turn moves on without a voice and on hang-up', async () => {
    const { call, ws } = await liveCall()
    await flush()
    ws.server(thinking(1))
    ws.server({ type: 'bot.thinking', turn: 1 })
    ws.server({ type: 'state', turn: 1, phase: 'listening', dialog: 'ordering', cart: [], total: 0 })
    ws.server(thinking(2))
    ws.server({ type: 'bot.thinking', turn: 2 })
    const ctx = FakeAudioContext.instances[0]
    const [first, second] = cueSources(ctx)
    expect(first.stopped).toBe(true)
    expect(second.stopped).toBe(false)
    call.hangup()
    expect(second.stopped).toBe(true)
  })

  it('ignores a late bot.thinking from an older turn', async () => {
    const { ws } = await liveCall()
    await flush()
    ws.server(thinking(2))
    ws.server({ type: 'bot.thinking', turn: 1 })
    expect(cueSources(FakeAudioContext.instances[0])).toHaveLength(0)
  })
})

describe('말 끊기 button (user.interrupt · playback.interrupted)', () => {
  const speaking = (turn: number, interruptible = true) => ({
    type: 'state',
    turn,
    phase: 'speaking',
    interruptible,
    dialog: 'ordering',
    cart: [],
    total: 0,
  })
  const events = (ws: FakeSocket) =>
    ws.sent.filter((m): m is string => typeof m === 'string').map((m) => JSON.parse(m) as Record<string, unknown>)
  const botSources = (ctx: FakeAudioContext) => ctx.sources.filter((s) => !cueSources(ctx).includes(s))
  const micFrame = () => {
    const frame = new ArrayBuffer(3200)
    FakeWorkletNode.last!.port.onmessage!({ data: frame })
    return frame
  }

  it('stops playback, reports where it stopped and opens the mic at once', async () => {
    const { call, ws } = await liveCall()
    const ctx = FakeAudioContext.instances[0]
    ws.server(speaking(1))
    ws.onmessage!({ data: audioFrame(1, 0) }) // 지금(0초)에 시작하도록 예약된다
    expect(micFrame()).not.toBe(ws.sent.at(-1)) // 봇이 말하는 동안은 0 프레임
    ctx.currentTime = 0.15 // 시작 뒤 150ms, 스피커 지연 20ms → 손님 귀에는 130ms
    call.interrupt()
    const [source] = botSources(ctx)
    expect(source.stopped).toBe(true)
    expect(events(ws).filter((e) => String(e.type).includes('interrupt'))).toEqual([
      { type: 'user.interrupt', turn: 1 },
      { type: 'playback.interrupted', turn: 1, seq: 0, played_ms: 130 },
    ])
    const frame = micFrame()
    expect(ws.sent.at(-1)).toBe(frame) // 서버 답을 기다리지 않고 진짜 마이크 소리
  })

  it('drops late audio of the cut turn but plays the next turn', async () => {
    const { call, ws } = await liveCall()
    const ctx = FakeAudioContext.instances[0]
    ws.server(speaking(1))
    ws.onmessage!({ data: audioFrame(1, 0) })
    call.interrupt()
    ws.onmessage!({ data: audioFrame(1, 1) }) // 서버가 끊기 전에 이미 보낸 소리
    expect(botSources(ctx)).toHaveLength(1)
    ws.server({ type: 'state', turn: 1, phase: 'listening', interruptible: false, dialog: 'ordering', cart: [], total: 0 })
    ws.server(speaking(2))
    ws.onmessage!({ data: audioFrame(2, 0) })
    expect(botSources(ctx)).toHaveLength(2)
    expect(botSources(ctx)[1].started).toBe(true)
  })

  it('never reports playback.done for the cut turn', async () => {
    const { call, ws } = await liveCall()
    ws.server(speaking(1))
    ws.onmessage!({ data: audioFrame(1, 0) })
    call.interrupt()
    ws.server({ type: 'bot.done', turn: 1, sentences: 1, interrupted: true })
    await new Promise((r) => setTimeout(r, 250)) // 마이크 다시 열기 지연(스피커 지연 + 150ms)보다 길게
    expect(events(ws).filter((e) => e.type === 'playback.done' || e.type === 'client.metrics')).toEqual([])
  })

  it('does nothing while the server blocks the button (final readback)', async () => {
    const { call, ws } = await liveCall()
    const ctx = FakeAudioContext.instances[0]
    ws.server(speaking(3, false))
    ws.onmessage!({ data: audioFrame(3, 0) })
    call.interrupt()
    expect(botSources(ctx)[0].stopped).toBe(false)
    expect(events(ws).filter((e) => String(e.type).includes('interrupt'))).toEqual([])
    expect(micFrame()).not.toBe(ws.sent.at(-1)) // 반이중 그대로
    expect(call.getState().botTalking).toBe(true)
  })
})

describe('voice barge-in (bot.duck · bot.unduck · bot.stop)', () => {
  const ANDROID_CHROME =
    'Mozilla/5.0 (Linux; Android 10; K) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/141.0.0.0 Mobile Safari/537.36'
  const speaking = (turn: number, interruptible = true) => ({
    type: 'state',
    turn,
    phase: 'speaking',
    interruptible,
    dialog: 'ordering',
    cart: [],
    total: 0,
  })
  const events = (ws: FakeSocket) =>
    ws.sent.filter((m): m is string => typeof m === 'string').map((m) => JSON.parse(m) as Record<string, unknown>)
  const botSources = (ctx: FakeAudioContext) => ctx.sources.filter((s) => !cueSources(ctx).includes(s))
  /** 봇 목소리가 지나가는 GainNode (캡처 쪽 소리 없는 GainNode는 값이 0이다) */
  const voiceGain = (ctx: FakeAudioContext) => ctx.gains.find((g) => g.gain.value !== 0)!

  async function bargeCall(): Promise<{ call: PhoneCall; ws: FakeSocket; ctx: FakeAudioContext }> {
    const call = new PhoneCall()
    call.dial()
    mic.resolve(stream)
    await flush()
    const ws = FakeSocket.instances[0]
    ws.open()
    ws.server({ type: 'call.ready', turn: 0, call: 'c1', sample_rate: 16000, voice_barge_in: 'on' })
    return { call, ws, ctx: FakeAudioContext.instances[0] }
  }

  it('tells the server it can barge in only on Android Chrome with echo cancellation', async () => {
    const { ws } = await liveCall()
    expect(events(ws)[0]).toMatchObject({ type: 'call.start', voice_barge_in: false })

    vi.stubGlobal('navigator', { userAgent: ANDROID_CHROME, mediaDevices: { getUserMedia: () => mic.promise } })
    FakeSocket.instances = []
    mic = deferred<typeof stream>()
    const { ws: android } = await liveCall()
    expect(events(android)[0]).toMatchObject({ type: 'call.start', voice_barge_in: true })
  })

  it('sends the real mic while the bot speaks when the server turned it on', async () => {
    const { ws } = await bargeCall()
    ws.server(speaking(1))
    ws.onmessage!({ data: audioFrame(1, 0) })
    const frame = new ArrayBuffer(3200)
    FakeWorkletNode.last!.port.onmessage!({ data: frame })
    expect(ws.sent.at(-1)).toBe(frame)
  })

  it('fades the bot voice down about 18 dB on bot.duck and back on bot.unduck', async () => {
    const { ws, ctx } = await bargeCall()
    ws.server(speaking(1))
    ws.onmessage!({ data: audioFrame(1, 0) })
    const gain = voiceGain(ctx)
    ws.server({ type: 'bot.duck', turn: 1 })
    expect(20 * Math.log10(gain.gain.value)).toBeCloseTo(-18, 0)
    ws.server({ type: 'bot.unduck', turn: 1 })
    expect(gain.gain.value).toBe(1)
  })

  it('restores the full volume when the next turn starts even without bot.unduck', async () => {
    const { ws, ctx } = await bargeCall()
    ws.server(speaking(1))
    ws.onmessage!({ data: audioFrame(1, 0) })
    const gain = voiceGain(ctx)
    ws.server({ type: 'bot.duck', turn: 1 })
    ws.server({ type: 'bot.duck', turn: 0 }) // 지난 턴의 늦은 줄이기는 무시한다
    ws.server(speaking(2))
    ws.onmessage!({ data: audioFrame(2, 0) })
    expect(gain.gain.value).toBe(1)
  })

  it('stops playback on bot.stop, reports where it stopped and drops late audio of that turn', async () => {
    const { call, ws, ctx } = await bargeCall()
    ws.server(speaking(3, false)) // 복창: 버튼은 막혔지만 정정 음성으로는 멈춘다
    ws.onmessage!({ data: audioFrame(3, 0) })
    ctx.currentTime = 0.5
    ws.server({ type: 'bot.stop', turn: 3 })
    expect(botSources(ctx)[0].stopped).toBe(true)
    expect(events(ws).filter((e) => String(e.type).includes('interrupt'))).toEqual([
      { type: 'playback.interrupted', turn: 3, seq: 0, played_ms: 480 },
    ])
    ws.onmessage!({ data: audioFrame(3, 1) })
    expect(botSources(ctx)).toHaveLength(1)
    expect(call.getState().botTalking).toBe(false)
  })
})

describe('device test options (?mic_first=1 · ?agc=0, 실기기 시험 T4·T5)', () => {
  const events = (ws: FakeSocket) =>
    ws.sent.filter((m): m is string => typeof m === 'string').map((m) => JSON.parse(m) as Record<string, unknown>)

  it('reads the options from the page query and defaults to the stage-2 order with auto gain', () => {
    expect(phoneOptions('')).toEqual({ micFirst: false, agc: true })
    expect(phoneOptions('?mic_first=1&agc=0')).toEqual({ micFirst: true, agc: false })
  })

  it('creates the playback context only after the mic is granted with mic_first', async () => {
    const call = new PhoneCall({ micFirst: true, agc: true })
    call.dial()
    expect(FakeAudioContext.instances).toHaveLength(0)
    mic.resolve(stream)
    await flush()
    expect(FakeAudioContext.instances).toHaveLength(1)
    const ws = FakeSocket.instances[0]
    ws.open()
    expect(events(ws)[0]).toMatchObject({ type: 'call.start', ctx_order: 'mic-first' })
  })

  it('asks for the mic without auto gain control when agc is off', async () => {
    const getUserMedia = vi.fn(() => mic.promise)
    vi.stubGlobal('navigator', { userAgent: 'test', mediaDevices: { getUserMedia } })
    const call = new PhoneCall({ micFirst: false, agc: false })
    call.dial()
    mic.resolve(stream)
    await flush()
    expect(getUserMedia).toHaveBeenCalledWith({ audio: expect.objectContaining({ autoGainControl: false }) })
    const ws = FakeSocket.instances[0]
    ws.open()
    expect(events(ws)[0]).toMatchObject({ ctx_order: 'context-first' })
  })
})

describe('received texts after the call (4단계 티켓 07)', () => {
  class FakeEventSource {
    static instances: FakeEventSource[] = []
    readyState = 1
    closed = false
    onerror: (() => void) | null = null
    readonly listeners = new Map<string, (e: { data: string }) => void>()
    readonly url: string
    constructor(url: string) {
      this.url = url
      FakeEventSource.instances.push(this)
    }
    addEventListener(name: string, fn: (e: { data: string }) => void) {
      this.listeners.set(name, fn)
    }
    emit(name: string, data: unknown) {
      this.listeners.get(name)?.({ data: JSON.stringify(data) })
    }
    close() {
      this.closed = true
    }
  }
  const text = (id: string) => ({
    id,
    call_id: 'c1',
    kind: 'late_answer',
    text: '메도빅 문의 답변이에요. 사장님께 여쭤보니 네, 된다고 하세요.',
    sent_at: '2026-11-08T16:07:00+09:00',
    sent_label: '16:07',
    question_id: 'c1-q1',
    translate_failed: false,
  })

  beforeEach(() => {
    FakeEventSource.instances = []
    vi.stubGlobal('EventSource', FakeEventSource)
  })

  it('opens the text stream for this call when the server ends the call', async () => {
    const { call, ws } = await liveCall()
    expect(FakeEventSource.instances).toHaveLength(0) // 통화 중에는 열지 않는다
    ws.server({ type: 'call.ended', turn: 6, reason: 'confirmed', sms: [text('c1-s1')] })
    ws.onclose?.()
    const [source] = FakeEventSource.instances
    expect(source.url).toBe('/api/phone/calls/c1/sms')
    source.emit('sms.snapshot', { call_id: 'c1', sms: [text('c1-s1')] })
    source.emit('sms', text('c1-s2'))
    expect(call.getState().texts.map((t) => t.id)).toEqual(['c1-s1', 'c1-s2'])
  })

  it('opens it when the customer hangs up, and closes it on a redial', async () => {
    const { call } = await liveCall()
    call.hangup()
    expect(FakeEventSource.instances).toHaveLength(1)
    call.dial()
    expect(FakeEventSource.instances[0].closed).toBe(true)
    expect(call.getState().texts).toEqual([])
  })

  it('does not open a stream for a call that never connected', () => {
    const call = new PhoneCall()
    call.dial()
    call.hangup()
    expect(FakeEventSource.instances).toHaveLength(0)
  })
})
