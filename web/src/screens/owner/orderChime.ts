// 새 주문 알림음 (4단계 티켓 08). "가게 열기"에서 풀린 `AudioContext`로 한 번 낸다.
// 소리는 직접 만든 사인파 파일이다(`web/scripts/make-order-chime.mjs`). 사람 목소리·TTS가 아니다.
// 가게 열기 직후 snapshot이 파일보다 먼저 와도 소리를 잃지 않게, 불러오기 전 요청은 불러온 뒤 한 번 낸다.

import chimeUrl from './order-chime.wav?url'

export class OrderChime {
  private readonly ctx: AudioContext
  private buffer: AudioBuffer | null = null
  private pending = false
  private closed = false

  constructor(ctx: AudioContext) {
    this.ctx = ctx
    void this.load()
  }

  /** 알림음을 한 번 낸다. 겹쳐 불러도 각자 한 번씩 난다(호출하는 쪽이 동기화마다 1회로 묶는다) */
  play(): void {
    if (this.closed) return
    if (!this.buffer) {
      this.pending = true
      return
    }
    const source = this.ctx.createBufferSource()
    source.buffer = this.buffer
    source.connect(this.ctx.destination)
    source.onended = () => source.disconnect()
    source.start()
  }

  close(): void {
    this.closed = true
  }

  private async load(): Promise<void> {
    try {
      const response = await fetch(chimeUrl)
      if (!response.ok) return
      const buffer = await this.ctx.decodeAudioData(await response.arrayBuffer())
      this.buffer = buffer
      if (this.pending) {
        this.pending = false
        this.play()
      }
    } catch (err) {
      console.warn('새 주문 알림음을 불러오지 못했어요', err) // 소리만 빠질 뿐 카드는 뜬다
    }
  }
}
