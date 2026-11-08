// 통화 1건을 맡는 컨트롤러: 마이크·AudioContext·워클릿·재생기·WS·Wake Lock을 묶는다.
// 화면 상태는 callReducer로 만들고 subscribe/getState로 React에 내준다.
//
// 순서(verify-web D10·D12): "전화 받기" 탭 핸들러 안에서 AudioContext를 먼저 만들고
// (재생 잠금 해제), 그다음 getUserMedia를 기다린다.

import {
  type CallAction,
  type CallState,
  type MicFailure,
  SLOW_LINK_BYTES,
  type ServerEvent,
  callReducer,
  initialCallState,
  micLive,
} from './callState.ts'
import captureWorkletUrl from './capture.worklet.ts?worker&url'
import { parseAudioFrame } from './frames.ts'
import { CAPTURE_PROCESSOR, FRAME_SAMPLES, WIRE_RATE } from './pcm.ts'
import { Player } from './player.ts'
import { micReopenDelayMs } from './schedule.ts'
import { TurnPlayback } from './turnPlayback.ts'

export const WS_PATH = '/api/phone/ws'

const MIC_CONSTRAINTS: MediaTrackConstraints = {
  // 크롬 141+는 "all"(시스템 재생음 전부 제거). 모르는 브라우저는 true로 받아들인다(D11).
  echoCancellation: { ideal: 'all' },
  noiseSuppression: true,
  autoGainControl: true,
  channelCount: 1,
}

const SILENT_FRAME = new ArrayBuffer(FRAME_SAMPLES * 2) // 반이중 중 보내는 0 프레임(100ms)

type CaptureMode = 'context-16k' | 'downsample'

interface TurnClock {
  firstAudio?: number
  playStart?: number
  playEnd?: number
}

export class PhoneCall {
  private state: CallState = initialCallState
  private readonly listeners = new Set<() => void>()

  private ctx: AudioContext | null = null
  private stream: MediaStream | null = null
  private node: AudioWorkletNode | null = null
  private ws: WebSocket | null = null
  private player: Player | null = null
  private playback = new TurnPlayback()
  private wakeLock: WakeLockSentinel | null = null
  private timers = new Set<number>()
  private clocks = new Map<number, TurnClock>()
  private generation = 0 // 다시 걸기 뒤 이전 통화의 늦은 콜백을 버린다

  getState = (): CallState => this.state

  subscribe = (fn: () => void): (() => void) => {
    this.listeners.add(fn)
    return () => this.listeners.delete(fn)
  }

  /** "전화 받기"·"다시 걸기". 반드시 클릭 핸들러에서 동기로 부른다. */
  dial(): void {
    this.teardown()
    const gen = ++this.generation
    this.dispatch({ type: 'dial' })

    if (!window.isSecureContext || !navigator.mediaDevices?.getUserMedia) {
      this.dispatch({ type: 'mic.failed', reason: 'insecure' })
      return
    }
    // 제스처 안에서 만들어야 재생이 막히지 않는다.
    const ctx = makeContext(WIRE_RATE)
    this.useContext(ctx)
    void this.requestWakeLock()
    void this.connect(gen)
  }

  hangup(): void {
    if (this.ws?.readyState === WebSocket.OPEN) {
      this.send({ type: 'call.end', turn: this.state.turn })
    }
    // 권한 창·워클릿 로드를 기다리던 connect()가 끊은 뒤에 이어 가지 않게 (리뷰 9번)
    this.generation++
    this.dispatch({ type: 'hangup' })
    this.teardown()
  }

  endOfSpeech(): void {
    this.send({ type: 'user.end_of_speech', turn: this.state.turn })
  }

  /** AudioContext가 suspended·interrupted일 때 화면의 "소리 다시 켜기" 버튼. */
  resumeAudio(): void {
    const ctx = this.ctx
    if (ctx) this.resumeAndReport(ctx)
  }

  /** 화면이 붙을 때(useEffect). */
  attach(): void {
    document.addEventListener('visibilitychange', this.onVisibility)
  }

  /** 화면이 떨어질 때. 통화 중이면 끊는다. */
  detach(): void {
    document.removeEventListener('visibilitychange', this.onVisibility)
    this.hangup()
  }

  // ------------------------------------------------------------------ 연결

  private async connect(gen: number): Promise<void> {
    let stream: MediaStream
    try {
      stream = await navigator.mediaDevices.getUserMedia({ audio: MIC_CONSTRAINTS })
    } catch (err) {
      if (gen !== this.generation) return
      this.dispatch({ type: 'mic.failed', reason: micFailure(err) })
      this.teardown()
      return
    }
    if (gen !== this.generation) {
      stopStream(stream)
      return
    }
    this.stream = stream

    try {
      const capture = await this.startCapture(stream)
      if (gen !== this.generation) return
      const settings = { ...stream.getAudioTracks()[0]?.getSettings() } as Record<string, unknown>
      if (!this.ctx) return
      // 화면에는 컨텍스트 레이트·캡처 방식도 보인다(실기에서 16000인지 확인, D10).
      this.dispatch({
        type: 'mic.ready',
        settings: { ...settings, contextSampleRate: this.ctx?.sampleRate, capture },
      })
      this.openSocket(gen, settings, capture)
    } catch (err) {
      if (gen !== this.generation) return
      console.error('마이크 캡처 시작 실패', err)
      this.dispatch({ type: 'mic.failed', reason: 'other' })
      this.teardown()
    }
  }

  private async startCapture(stream: MediaStream): Promise<CaptureMode> {
    let ctx = this.ctx!
    let mode: CaptureMode = 'context-16k'
    let source: MediaStreamAudioSourceNode
    try {
      source = ctx.createMediaStreamSource(stream)
    } catch (err) {
      // 파이어폭스: 16kHz 컨텍스트에 장치 레이트가 다른 트랙을 못 붙인다(D10-2).
      if (!(err instanceof DOMException && err.name === 'NotSupportedError')) throw err
      void ctx.close()
      ctx = makeContext()
      this.useContext(ctx) // 제스처 밖이라 suspended로 남으면 다시 켜기 버튼이 뜬다

      source = ctx.createMediaStreamSource(stream)
      mode = 'downsample'
    }
    await ctx.audioWorklet.addModule(captureWorkletUrl)
    if (this.ctx !== ctx) throw new Error('통화가 끝났다') // 기다리는 동안 끊었다
    const node = new AudioWorkletNode(ctx, CAPTURE_PROCESSOR, {
      numberOfInputs: 1,
      numberOfOutputs: 1,
      channelCount: 1,
      channelCountMode: 'explicit',
    })
    node.port.onmessage = (e: MessageEvent<ArrayBuffer>) => this.onMicFrame(e.data)
    // 출력은 소리 없이 목적지에 붙여 둔다. 그래야 모든 브라우저가 노드를 렌더링한다.
    const mute = ctx.createGain()
    mute.gain.value = 0
    source.connect(node).connect(mute).connect(ctx.destination)
    this.node = node
    return mode
  }

  private openSocket(gen: number, mic: Record<string, unknown>, capture: CaptureMode): void {
    const scheme = location.protocol === 'https:' ? 'wss' : 'ws'
    const ws = new WebSocket(`${scheme}://${location.host}${WS_PATH}`)
    ws.binaryType = 'arraybuffer'
    this.ws = ws
    const ctx = this.ctx!
    ws.onopen = () => {
      this.send({
        type: 'call.start',
        turn: 0,
        mic,
        capture,
        sample_rate: ctx.sampleRate,
        output_latency: ctx.outputLatency,
        base_latency: ctx.baseLatency,
        ua: navigator.userAgent,
        client_at: Date.now(),
      })
    }
    ws.onmessage = (e: MessageEvent<ArrayBuffer | string>) => {
      if (gen !== this.generation) return
      if (typeof e.data === 'string') this.onServerText(e.data)
      else this.onServerAudio(e.data)
    }
    ws.onclose = () => {
      if (gen !== this.generation) return
      this.dispatch({ type: 'socket.closed' })
      this.teardown()
    }
  }

  // ------------------------------------------------------------------ 받기

  private onServerText(text: string): void {
    let event: ServerEvent
    try {
      event = JSON.parse(text) as ServerEvent
    } catch {
      return
    }
    this.dispatch({ type: 'server', event })
    if (event.type === 'bot.done') {
      const progress = this.playback.botDone(event.turn, event.sentences)
      if (progress === 'turn-finished') this.finishTurn(event.turn, event.sentences - 1)
    }
  }

  private onServerAudio(buf: ArrayBuffer): void {
    let frame
    try {
      frame = parseAudioFrame(buf)
    } catch (err) {
      console.warn(err)
      return
    }
    const clock = this.clock(frame.turn)
    clock.firstAudio ??= performance.now()
    this.player?.enqueue(frame)
  }

  private onSentenceStarted(turn: number, seq: number): void {
    this.clock(turn).playStart ??= performance.now()
    this.send({ type: 'playback.started', turn, seq })
  }

  private onSentenceEnded(turn: number, seq: number): void {
    if (this.playback.sentenceEnded(turn, seq) === 'turn-finished') this.finishTurn(turn, seq)
  }

  /** 턴의 마지막 문장이 끝났다. 스피커 지연 + 150ms 뒤 마이크를 다시 열고 보고한다(D8). */
  private finishTurn(turn: number, lastSeq: number): void {
    const clock = this.clock(turn)
    clock.playEnd = performance.now()
    const outputLatency = this.ctx?.outputLatency
    this.later(micReopenDelayMs(outputLatency), () => {
      this.dispatch({ type: 'playback.finished', turn })
      // metrics를 먼저: 확정·handoff 턴은 서버가 playback.done을 받자마자 통화를 끝낸다.
      this.send({
        type: 'client.metrics',
        turn,
        first_audio_ms: clock.firstAudio,
        play_start_ms: clock.playStart,
        play_end_ms: clock.playEnd,
        mic_open_ms: performance.now(),
        output_latency: outputLatency ?? null,
        base_latency: this.ctx?.baseLatency ?? null,
        client_at: Date.now(),
      })
      this.send({ type: 'playback.done', turn, seq: lastSeq, output_latency: outputLatency ?? null })
      this.clocks.delete(turn)
    })
  }

  // ------------------------------------------------------------------ 보내기

  private onMicFrame(frame: ArrayBuffer): void {
    if (this.ws?.readyState !== WebSocket.OPEN || this.state.status !== 'live') return
    // 봇이 말하는 동안에도 같은 속도로 보낸다(CLOVA "계속 보내기"). 내용만 0.
    this.ws.send(micLive(this.state) ? frame : SILENT_FRAME)
    // 회선이 느려 쌓이면 안내만 한다. 프레임을 버리면 서버의 STT 시각이 틀어진다(리뷰 1번)
    this.dispatch({ type: 'link.slow', slow: this.ws.bufferedAmount > SLOW_LINK_BYTES })
  }

  private send(event: Record<string, unknown>): void {
    if (this.ws?.readyState === WebSocket.OPEN) this.ws.send(JSON.stringify(event))
  }

  // ------------------------------------------------------------------ 기타

  private useContext(ctx: AudioContext): void {
    this.ctx = ctx
    this.player?.stop()
    this.player = new Player(ctx, {
      sentenceStarted: (turn, seq) => this.onSentenceStarted(turn, seq),
      sentenceEnded: (turn, seq) => this.onSentenceEnded(turn, seq),
    })
    // 크롬 136+: 외부 사유로 'interrupted'가 될 수 있다. 화면에 다시 켜기 버튼(D12-3).
    ctx.onstatechange = () => {
      if (this.ctx === ctx) this.dispatch({ type: 'audio.state', state: ctx.state })
    }
    // 처음부터 suspended로 남으면 statechange가 오지 않는다. 지금 상태를 한 번 알린다(리뷰 4번)
    this.resumeAndReport(ctx)
  }

  private resumeAndReport(ctx: AudioContext): void {
    const report = () => {
      if (this.ctx === ctx) this.dispatch({ type: 'audio.state', state: ctx.state })
    }
    // interrupted 중에는 reject된다. 그래도 지금 상태로 버튼을 맞춘다.
    void ctx.resume().then(report, report)
  }

  private async requestWakeLock(): Promise<void> {
    if (!('wakeLock' in navigator) || document.visibilityState !== 'visible') return
    try {
      this.wakeLock = await navigator.wakeLock.request('screen')
    } catch {
      // 배터리 절약 모드 등. 화면 꺼짐만 막지 못할 뿐 통화는 된다.
    }
  }

  /** 문서가 숨겨지면 잠금이 풀린다. 다시 보이면 다시 잡는다. */
  private onVisibility = (): void => {
    const s = this.state.status
    if (document.visibilityState === 'visible' && (s === 'starting' || s === 'live')) {
      if (!this.wakeLock || this.wakeLock.released) void this.requestWakeLock()
      // 다른 앱에 다녀오면 소리가 멈춰 있을 수 있다. 멈췄으면 다시 켜기 버튼을 띄운다
      if (this.ctx && this.ctx.state !== 'running') {
        this.dispatch({ type: 'audio.state', state: this.ctx.state })
      }
    }
  }

  private clock(turn: number): TurnClock {
    let c = this.clocks.get(turn)
    if (!c) {
      c = {}
      this.clocks.set(turn, c)
    }
    return c
  }

  private later(ms: number, fn: () => void): void {
    const t = window.setTimeout(() => {
      this.timers.delete(t)
      fn()
    }, ms)
    this.timers.add(t)
  }

  private dispatch(action: CallAction): void {
    const next = callReducer(this.state, action)
    if (next === this.state) return
    this.state = next
    for (const fn of this.listeners) fn()
  }

  /** 마이크·소켓·재생·잠금을 모두 정리한다. 화면 상태(자막·주문서·오류)는 남긴다. */
  private teardown(): void {
    for (const t of this.timers) clearTimeout(t)
    this.timers.clear()
    this.clocks.clear()
    this.playback = new TurnPlayback()
    this.player?.stop()
    this.player = null
    if (this.node) {
      this.node.port.onmessage = null
      this.node.disconnect()
      this.node = null
    }
    if (this.stream) {
      stopStream(this.stream)
      this.stream = null
    }
    if (this.ws) {
      const ws = this.ws
      this.ws = null
      ws.onopen = ws.onmessage = ws.onclose = null
      if (ws.readyState === WebSocket.OPEN || ws.readyState === WebSocket.CONNECTING) ws.close()
    }
    if (this.ctx) {
      this.ctx.onstatechange = null
      void this.ctx.close().catch(() => {})
      this.ctx = null
    }
    if (this.wakeLock) {
      void this.wakeLock.release().catch(() => {})
      this.wakeLock = null
    }
  }
}

function makeContext(sampleRate?: number): AudioContext {
  return new AudioContext(sampleRate ? { sampleRate, latencyHint: 'interactive' } : { latencyHint: 'interactive' })
}

function stopStream(stream: MediaStream): void {
  for (const track of stream.getTracks()) track.stop()
}

function micFailure(err: unknown): MicFailure {
  const name = err instanceof DOMException ? err.name : ''
  if (name === 'NotAllowedError' || name === 'SecurityError') return 'denied'
  if (name === 'NotFoundError' || name === 'OverconstrainedError') return 'no-device'
  return 'other'
}
