import { describe, expect, it } from 'vitest'
import { Downsampler, FrameChunker, floatToInt16, int16ToFloat } from './pcm.ts'

function sine(freq: number, rate: number, seconds: number): Float32Array {
  const out = new Float32Array(Math.round(rate * seconds))
  for (let i = 0; i < out.length; i++) out[i] = 0.5 * Math.sin((2 * Math.PI * freq * i) / rate)
  return out
}

// 128샘플 렌더 블록 단위로 넣고 나온 것을 이어 붙인다(워클릿과 같은 방식).
function runInBlocks(ds: Downsampler, input: Float32Array): Float32Array {
  const parts: Float32Array[] = []
  for (let i = 0; i < input.length; i += 128) parts.push(ds.push(input.subarray(i, i + 128)))
  const out = new Float32Array(parts.reduce((n, p) => n + p.length, 0))
  let o = 0
  for (const p of parts) {
    out.set(p, o)
    o += p.length
  }
  return out
}

function peak(x: Float32Array): number {
  return x.reduce((m, v) => Math.max(m, Math.abs(v)), 0)
}

describe('Downsampler', () => {
  it('produces one 16 kHz sample per three 48 kHz samples', () => {
    const out = runInBlocks(new Downsampler(48000), new Float32Array(48000))
    expect(Math.abs(out.length - 16000)).toBeLessThanOrEqual(20)
  })

  it('handles 44.1 kHz input too', () => {
    const out = runInBlocks(new Downsampler(44100), new Float32Array(44100))
    expect(Math.abs(out.length - 16000)).toBeLessThanOrEqual(20)
  })

  it('keeps speech-band tones (1 kHz) at about the same level', () => {
    const out = runInBlocks(new Downsampler(48000), sine(1000, 48000, 0.5))
    expect(peak(out.subarray(400))).toBeGreaterThan(0.45)
    expect(peak(out.subarray(400))).toBeLessThan(0.55)
  })

  it('removes tones above 8 kHz instead of aliasing them into the voice band', () => {
    const out = runInBlocks(new Downsampler(48000), sine(12000, 48000, 0.5))
    // 0.5 진폭의 12 kHz → 20 dB 이상 줄어야 한다(3탭 평균은 약 9.5 dB뿐)
    expect(peak(out.subarray(400))).toBeLessThan(0.05)
  })

  it('passes 16 kHz input through unchanged', () => {
    const input = sine(440, 16000, 0.01)
    expect(Array.from(new Downsampler(16000).push(input))).toEqual(Array.from(input))
  })
})

describe('FrameChunker', () => {
  it('turns 128-sample render blocks into continuous 1600-sample (100 ms) frames', () => {
    const chunker = new FrameChunker(1600)
    const frames: Int16Array[] = []
    let n = 0
    for (let block = 0; block < 25; block++) {
      const samples = new Int16Array(128)
      for (let i = 0; i < 128; i++) samples[i] = n++ % 30000
      frames.push(...chunker.push(samples))
    }
    // 25 × 128 = 3,200샘플 → 프레임 2개, 남는 것 없음
    expect(frames.map((f) => f.length)).toEqual([1600, 1600])
    expect(frames[0][0]).toBe(0)
    expect(frames[0][1599]).toBe(1599)
    expect(frames[1][0]).toBe(1600)
    expect(frames[1][1599]).toBe(3199)
  })

  it('keeps the remainder until the next frame fills', () => {
    const chunker = new FrameChunker(4)
    expect(chunker.push(new Int16Array([1, 2, 3]))).toEqual([])
    const out = chunker.push(new Int16Array([4, 5, 6, 7, 8, 9]))
    expect(out.map((f) => Array.from(f))).toEqual([[1, 2, 3, 4], [5, 6, 7, 8]])
  })
})

describe('floatToInt16', () => {
  it('maps -1..1 to the full Int16 range and clamps beyond it', () => {
    const out = floatToInt16(new Float32Array([0, 1, -1, 1.5, -2, 0.5, -0.5]))
    expect(Array.from(out)).toEqual([0, 32767, -32768, 32767, -32768, 16384, -16384])
  })
})

describe('int16ToFloat', () => {
  it('maps Int16 samples back to -1..1', () => {
    const out = int16ToFloat(new Int16Array([-32768, 0, 16384]))
    expect(Array.from(out)).toEqual([-1, 0, 0.5])
  })
})
