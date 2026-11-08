import { describe, expect, it } from 'vitest'
import { micReopenDelayMs, scheduleChunk } from './schedule.ts'

const RATE = 16000

describe('scheduleChunk', () => {
  it('starts the first chunk a little ahead of now, on a sample boundary', () => {
    const s = scheduleChunk({ nextTime: 0, now: 1.23456, samples: 3200, rate: RATE })
    // now + 50ms 리드 = 1.28456s → 샘플 경계(1/16000 = 62.5µs)로 맞춘 1.2845625
    expect(s.start).toBeCloseTo(1.2845625, 9)
    expect(s.end).toBeCloseTo(1.2845625 + 0.2, 9)
  })

  it('chains the next chunk exactly at the previous end so sentences play without gaps', () => {
    const a = scheduleChunk({ nextTime: 0, now: 2, samples: 3200, rate: RATE })
    const b = scheduleChunk({ nextTime: a.end, now: 2.1, samples: 1600, rate: RATE })
    expect(b.start).toBe(a.end)
    expect(b.end).toBeCloseTo(a.end + 0.1, 9)
  })

  it('restarts ahead of now when the queue ran dry (late chunk)', () => {
    const s = scheduleChunk({ nextTime: 3.0, now: 3.5, samples: 3200, rate: RATE })
    expect(s.start).toBeCloseTo(3.55, 9)
  })

  it('gives an empty chunk zero length at the queue end', () => {
    const s = scheduleChunk({ nextTime: 5, now: 4, samples: 0, rate: RATE })
    expect(s.start).toBe(5)
    expect(s.end).toBe(5)
  })
})

describe('micReopenDelayMs', () => {
  it('waits outputLatency plus 150 ms after the last source ended', () => {
    expect(micReopenDelayMs(0.04)).toBe(190)
  })

  it('falls back to 150 ms when outputLatency is unknown', () => {
    expect(micReopenDelayMs(undefined)).toBe(150)
    expect(micReopenDelayMs(Number.NaN)).toBe(150)
  })
})
