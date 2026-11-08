// PhoneCall 컨트롤러를 브라우저 API 가짜로 돌려 본다(리뷰 1·4·9번).
// 마이크·AudioContext·WebSocket은 최소한의 가짜다. 보는 것은 화면 상태(getState)와 소켓으로 나간 것뿐이다.
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { PhoneCall } from './phoneCall.ts'

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
  createGain() {
    return { gain: { value: 1 }, connect: (next: unknown) => next, disconnect() {} }
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
