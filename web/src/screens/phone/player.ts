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

/** 말 끊기 때 손님이 들은 자리: 그 턴의 `seq` 문장을 `playedMs`까지 들었다. */
export interface PlayedSpot {
  seq: number
  playedMs: number
}

/** 음성 끼어들기 줄이기: 약 -18 dB (grilling Q9). 줄이기는 약 150ms, 되돌리기는 약 80ms에 걸쳐
 *  바꾼다(vox.ai #36, s3-engineering Q4). setTargetAtTime의 시정수는 그 1/3이다 */
export const DUCK_GAIN = 10 ** (-18 / 20)
const DUCK_TC = 0.05
const RESTORE_TC = 0.027

export class Player {
  private readonly ctx: AudioContext
  private readonly events: PlayerEvents
  /** 봇 목소리 음량. 모든 조각이 여기를 지나 스피커로 간다 */
  private readonly volume: GainNode
  private duckTurn: number | null = null
  private nextTime = 0
  private readonly started = new Set<string>()
  /** 문장마다 첫 조각 예약 시각(지금 턴 것만). 말 끊기 때 들은 자리를 계산한다 */
  private starts: { turn: number; seq: number; at: number }[] = []
  private readonly sources = new Set<AudioBufferSourceNode>()
  private readonly timers = new Set<number>()

  constructor(ctx: AudioContext, events: PlayerEvents) {
    this.ctx = ctx
    this.events = events
    this.volume = ctx.createGain()
    this.volume.connect(ctx.destination)
  }

  /** 음성 끼어들기(티켓 06): 손님 소리가 들려 `turn`의 봇 목소리를 줄인다. 멈추지는 않는다 */
  duck(turn: number): void {
    this.duckTurn = turn
    this.fade(DUCK_GAIN, DUCK_TC)
  }

  /** 맞장구·에코였다. 원래 크기로 돌린다 */
  unduck(): void {
    if (this.duckTurn === null) return
    this.duckTurn = null
    this.fade(1, RESTORE_TC)
  }

  private fade(value: number, timeConstant: number): void {
    const gain = this.volume.gain
    gain.cancelScheduledValues(this.ctx.currentTime)
    gain.setTargetAtTime(value, this.ctx.currentTime, timeConstant)
  }

  enqueue(frame: AudioFrame): void {
    const { turn, seq } = frame
    if (this.duckTurn !== null && turn !== this.duckTurn) this.unduck() // 새 턴은 원래 크기로
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
      this.starts = this.starts.filter((s) => s.turn === turn)
      this.starts.push({ turn, seq, at: slot.start })
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
    source.connect(this.volume)
    this.sources.add(source)
    source.onended = () => {
      this.sources.delete(source)
      source.disconnect()
      if (frame.last) this.events.sentenceEnded(turn, seq)
    }
    source.start(slot.start)
  }

  /**
   * 말 끊기(티켓 05): 예약된 소리를 모두 멈추고 손님이 `turn`의 어느 문장 몇 ms까지 들었는지
   * 돌려준다. 시작·끝 알림은 더 보내지 않는다. 스피커는 `outputLatency`만큼 늦게 소리를 내므로
   * 그만큼 덜 들은 것으로 친다(s3-barge-in Q2-2). 그 턴 소리가 아직 안 났으면 0번 문장 0ms.
   */
  interrupt(turn: number): PlayedSpot {
    const heardAt = this.ctx.currentTime - (this.ctx.outputLatency || 0)
    let spot: PlayedSpot = { seq: 0, playedMs: 0 }
    for (const s of this.starts) {
      if (s.turn === turn && s.at <= heardAt) spot = { seq: s.seq, playedMs: Math.round((heardAt - s.at) * 1000) }
    }
    this.stop()
    return spot
  }

  /** 예약된 소리를 모두 멈춘다(통화 끝·말 끊기). 끝 알림은 보내지 않는다. */
  stop(): void {
    this.starts = []
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
    this.unduck()
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
