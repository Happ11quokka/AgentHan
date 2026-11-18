// 한 턴의 봇 문장 재생이 다 끝났는지 센다.
// 서버는 그 턴 마지막 seq의 playback.done을 받아야 듣기로 돌아간다(티켓 01 Comments).
// 문장 수는 bot.done.sentences로 알게 되는데, bot.done과 마지막 문장 재생 끝 중 어느 것이
// 먼저 올지 모르므로 양쪽에서 확인한다.

export type TurnProgress = 'more' | 'turn-finished' | 'silent'

export class TurnPlayback {
  private turn = -1
  private sentences: number | null = null
  private lastEnded = -1
  private finished = false

  botDone(turn: number, sentences: number): TurnProgress {
    if (turn < this.turn) return 'more'
    this.enter(turn)
    if (this.finished) return 'more'
    this.sentences = sentences
    if (sentences === 0) {
      this.finished = true
      return 'silent'
    }
    return this.check()
  }

  sentenceEnded(turn: number, seq: number): TurnProgress {
    if (turn < this.turn) return 'more'
    this.enter(turn)
    if (this.finished) return 'more'
    this.lastEnded = Math.max(this.lastEnded, seq)
    return this.check()
  }

  private enter(turn: number): void {
    if (turn === this.turn) return
    this.turn = turn
    this.sentences = null
    this.lastEnded = -1
    this.finished = false
  }

  private check(): TurnProgress {
    if (this.sentences !== null && this.lastEnded >= this.sentences - 1) {
      this.finished = true
      return 'turn-finished'
    }
    return 'more'
  }
}
