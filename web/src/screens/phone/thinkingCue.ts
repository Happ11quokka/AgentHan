// 생각 중 효과음 (3단계 티켓 01). 서버가 `bot.thinking`을 보내면(말 끝 + 1.5초까지 봇 목소리가
// 없음) 짧은 비음성 효과음을 반복 재생하고, 그 턴의 봇 목소리가 오거나 턴이 넘어가면 멈춘다.
// 효과음은 직접 만든 사인파 파일이다(`web/scripts/make-thinking-cue.mjs`). 사람 목소리·TTS가 아니다.

import cueUrl from './thinking-cue.wav?url'

/** 서버가 목소리를 끝내 못 보내도 효과음이 끝없이 돌지 않게 (엔진 시간 제한 20초보다 짧게) */
const MAX_PLAY_S = 15

export class ThinkingCue {
  private readonly ctx: AudioContext
  private buffer: AudioBuffer | null = null
  private source: AudioBufferSourceNode | null = null
  private turn = -1
  private stopped = false

  constructor(ctx: AudioContext) {
    this.ctx = ctx
    void this.load()
  }

  /** 지금 효과음이 울리는 턴. 안 울리면 null. */
  get playingTurn(): number | null {
    return this.source ? this.turn : null
  }

  /** 그 턴의 효과음을 시작한다. 시작했으면 performance.now() 시각을 돌려준다(지표). */
  play(turn: number): number | null {
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
    source.stop(this.ctx.currentTime + MAX_PLAY_S)
    this.source = source
    this.turn = turn
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
