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
