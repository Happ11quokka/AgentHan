// 생각 중 효과음 (3단계 티켓 01). 서버가 `bot.thinking`을 보내면(말 끝 + 1.5초까지 봇 목소리가
// 없음) 짧은 비음성 효과음을 반복 재생하고, 그 턴의 봇 목소리가 오거나 턴이 넘어가면 멈춘다.
// 효과음은 직접 만든 사인파 파일이다(`web/scripts/make-thinking-cue.mjs`). 사람 목소리·TTS가 아니다.
// 주문 승인 티켓 07: 사장님 확인을 기다리는 동안의 대기음(`bot.hold`)도 같은 소리다. 그 턴이 듣는 중인
// 동안 이어 틀고, 손님 말이 턴이 되거나(생각 중) 봇이 말하면 멈춘다.

import cueUrl from './thinking-cue.wav?url'

/** 서버가 목소리를 끝내 못 보내도 효과음이 끝없이 돌지 않게 (엔진 시간 제한 20초보다 짧게) */
const MAX_PLAY_S = 15

/** 생각 중(`bot.thinking`, 국면 thinking 동안) · 대기음(`bot.hold`, 국면 listening 동안) */
export type CueMode = 'thinking' | 'hold'

/** 지금 울리는 효과음을 이 `state` 이벤트 뒤에도 이어 틀까. 같은 턴이고 그 소리의 국면일 때만 */
export function cueKeeps(playing: { turn: number; mode: CueMode }, event: { turn: number; phase: string }): boolean {
  const phase = playing.mode === 'hold' ? 'listening' : 'thinking'
  return event.turn === playing.turn && event.phase === phase
}

export class ThinkingCue {
  private readonly ctx: AudioContext
  private buffer: AudioBuffer | null = null
  private source: AudioBufferSourceNode | null = null
  private turn = -1
  private mode: CueMode = 'thinking'
  private stopped = false

  constructor(ctx: AudioContext) {
    this.ctx = ctx
    void this.load()
  }

  /** 지금 울리는 효과음(턴·종류). 안 울리면 null. */
  get playing(): { turn: number; mode: CueMode } | null {
    return this.source ? { turn: this.turn, mode: this.mode } : null
  }

  /**
   * 그 턴의 효과음을 시작한다. 시작했으면 performance.now() 시각을 돌려준다(지표).
   * `maxS`는 재생 상한(초). 대기음은 서버가 남은 확인 대기에 맞춰 준다.
   */
  play(turn: number, mode: CueMode = 'thinking', maxS: number = MAX_PLAY_S): number | null {
    if (this.stopped || !this.buffer || this.source) return null
    const source = this.ctx.createBufferSource()
    source.buffer = this.buffer
    source.loop = true
    source.connect(this.ctx.destination)
    source.onended = () => {
      if (this.source === source) this.source = null
      source.disconnect()
    }
    source.start()
    source.stop(this.ctx.currentTime + maxS)
    this.source = source
    this.turn = turn
    this.mode = mode
    return performance.now()
  }

  /** `turn`(없으면 아무 턴)의 효과음을 멈춘다. */
  stop(turn?: number): void {
    const source = this.source
    if (!source || (turn !== undefined && turn !== this.turn)) return
    this.source = null
    source.onended = null
    try {
      source.stop()
    } catch {
      // 이미 끝났다
    }
    source.disconnect()
  }

  /** 통화 끝. 그 뒤에는 다시 울리지 않는다. */
  close(): void {
    this.stop()
    this.stopped = true
  }

  private async load(): Promise<void> {
    try {
      const response = await fetch(cueUrl)
      if (!response.ok) return
      const data = await response.arrayBuffer()
      const buffer = await this.ctx.decodeAudioData(data)
      if (!this.stopped) this.buffer = buffer
    } catch (err) {
      console.warn('생각 중 효과음을 불러오지 못했어요', err) // 효과음만 빠질 뿐 통화는 된다
    }
  }
}
