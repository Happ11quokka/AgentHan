import { describe, expect, it } from 'vitest'
import { TurnPlayback } from './turnPlayback.ts'

describe('TurnPlayback', () => {
  it('finishes the turn when the last sentence ends after bot.done', () => {
    const p = new TurnPlayback()
    expect(p.botDone(1, 2)).toBe('more')
    expect(p.sentenceEnded(1, 0)).toBe('more')
    expect(p.sentenceEnded(1, 1)).toBe('turn-finished')
  })

  it('finishes at bot.done when every sentence already ended before it arrived', () => {
    const p = new TurnPlayback()
    expect(p.sentenceEnded(3, 0)).toBe('more')
    expect(p.botDone(3, 1)).toBe('turn-finished')
  })

  it('reports a turn without sentences as silent (no playback reports needed)', () => {
    expect(new TurnPlayback().botDone(2, 0)).toBe('silent')
  })

  it('ignores a sentence end left over from an earlier turn', () => {
    const p = new TurnPlayback()
    p.botDone(2, 1)
    expect(p.sentenceEnded(1, 0)).toBe('more')
    expect(p.sentenceEnded(2, 0)).toBe('turn-finished')
  })

  it('finishes a turn only once', () => {
    const p = new TurnPlayback()
    p.botDone(0, 1)
    expect(p.sentenceEnded(0, 0)).toBe('turn-finished')
    expect(p.sentenceEnded(0, 0)).toBe('more')
    expect(p.botDone(0, 1)).toBe('more')
  })
})
