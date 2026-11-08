// 봇 음성 재생기. 서버가 내리는 PCM16 16kHz 조각을 AudioBuffer로 바꿔 앞 조각 끝에
// 이어 붙여 예약한다(verify-web D12). 문장(seq)마다 시작·끝을 알려 준다.

import type { AudioFrame } from './frames.ts'
import { WIRE_RATE, int16ToFloat } from './pcm.ts'
import { scheduleChunk } from './schedule.ts'

export interface PlayerEvents {
  sentenceStarted(turn: number, seq: number): void
  /** 그 문장의 마지막 조각이 렌더링을 마쳤다(onended). 스피커 지연은 부르는 쪽이 더한다. */
  sentenceEnded(turn: number, seq: number): void
}

export class Player {
  private readonly ctx: AudioContext
  private readonly events: PlayerEvents
  private nextTime = 0
  private readonly started = new Set<string>()
  private readonly sources = new Set<AudioBufferSourceNode>()
  private readonly timers = new Set<number>()

  constructor(ctx: AudioContext, events: PlayerEvents) {
    this.ctx = ctx
    this.events = events
  }

  enqueue(frame: AudioFrame): void {
    const { turn, seq } = frame
    const slot = scheduleChunk({
      nextTime: this.nextTime,
      now: this.ctx.currentTime,
      samples: frame.pcm.length,
      rate: WIRE_RATE,
    })
    this.nextTime = slot.end

    const key = `${turn}:${seq}`
    if (!this.started.has(key)) {
      this.started.add(key)
      this.at(slot.start, () => this.events.sentenceStarted(turn, seq))
    }

    if (frame.pcm.length === 0) {
      // 빈 마지막 조각(헤더만). 버퍼를 못 만드니 예약 끝 시각에 끝을 알린다.
      if (frame.last) this.at(slot.end, () => this.events.sentenceEnded(turn, seq))
      return
    }

    // 버퍼 레이트를 16kHz로 둔다. 컨텍스트도 16kHz면 보간이 없다(D10-1).
    const buffer = this.ctx.createBuffer(1, frame.pcm.length, WIRE_RATE)
    buffer.copyToChannel(int16ToFloat(frame.pcm), 0)
    const source = this.ctx.createBufferSource()
    source.buffer = buffer
    source.connect(this.ctx.destination)
    this.sources.add(source)
    source.onended = () => {
      this.sources.delete(source)
      source.disconnect()
      if (frame.last) this.events.sentenceEnded(turn, seq)
    }
    source.start(slot.start)
  }

  /** 예약된 소리를 모두 멈춘다(통화 끝). 끝 알림은 보내지 않는다. */
  stop(): void {
    for (const t of this.timers) clearTimeout(t)
    this.timers.clear()
    for (const s of this.sources) {
      s.onended = null
      try {
        s.stop()
      } catch {
        // 아직 시작 전이거나 이미 끝났다
      }
      s.disconnect()
    }
    this.sources.clear()
    this.nextTime = 0
  }

  private at(when: number, fn: () => void): void {
    const delay = Math.max(0, (when - this.ctx.currentTime) * 1000)
    const t = window.setTimeout(() => {
      this.timers.delete(t)
      fn()
    }, delay)
    this.timers.add(t)
  }
}
