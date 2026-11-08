import { describe, expect, it } from 'vitest'
import { parseAudioFrame } from './frames.ts'

// 서버 voice/call/frames.py와 같은 모양: <BBHI (kind, flags, seq, turn) + PCM16LE
function frame(kind: number, flags: number, seq: number, turn: number, samples: number[]): ArrayBuffer {
  const buf = new ArrayBuffer(8 + samples.length * 2)
  const view = new DataView(buf)
  view.setUint8(0, kind)
  view.setUint8(1, flags)
  view.setUint16(2, seq, true)
  view.setUint32(4, turn, true)
  samples.forEach((s, i) => view.setInt16(8 + i * 2, s, true))
  return buf
}

describe('parseAudioFrame', () => {
  it('reads seq, turn, last-chunk flag and the PCM samples', () => {
    const f = parseAudioFrame(frame(1, 1, 3, 70000, [0, -32768, 32767, 5]))
    expect(f.seq).toBe(3)
    expect(f.turn).toBe(70000)
    expect(f.last).toBe(true)
    expect(Array.from(f.pcm)).toEqual([0, -32768, 32767, 5])
  })

  it('treats flags without bit0 as a middle chunk', () => {
    expect(parseAudioFrame(frame(1, 2, 0, 1, [1])).last).toBe(false)
  })

  it('accepts an empty last chunk (header only)', () => {
    const f = parseAudioFrame(frame(1, 1, 0, 2, []))
    expect(f.pcm.length).toBe(0)
    expect(f.last).toBe(true)
  })

  it('rejects a frame shorter than the 8-byte header', () => {
    expect(() => parseAudioFrame(new ArrayBuffer(5))).toThrow()
  })

  it('rejects an unknown kind', () => {
    expect(() => parseAudioFrame(frame(2, 0, 0, 0, [1]))).toThrow()
  })
})
