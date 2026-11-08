// PCM 변환·다운샘플·100ms 프레임 묶기. 캡처 워클릿과 재생기가 같이 쓴다.
// 워클릿 안에서도 돌아야 하므로 DOM·브라우저 API를 쓰지 않는다.

export const WIRE_RATE = 16000 // 올리는·내리는 PCM 모두 16kHz mono
export const FRAME_SAMPLES = 1600 // 올리는 프레임 100ms = 1,600샘플 = 3,200바이트

/** Float32(-1..1) → Int16. 범위를 넘으면 자른다. */
export function floatToInt16(input: Float32Array): Int16Array {
  const out = new Int16Array(input.length)
  for (let i = 0; i < input.length; i++) {
    const s = Math.max(-1, Math.min(1, input[i]))
    out[i] = Math.round(s < 0 ? s * 32768 : s * 32767)
  }
  return out
}

/** 들어오는 샘플을 정해진 길이의 프레임으로 묶는다. 남는 샘플은 다음 push로 넘긴다. */
export class FrameChunker {
  private readonly size: number
  private buf: Int16Array
  private filled = 0

  constructor(size: number = FRAME_SAMPLES) {
    this.size = size
    this.buf = new Int16Array(size)
  }

  push(samples: Int16Array): Int16Array[] {
    const frames: Int16Array[] = []
    let i = 0
    while (i < samples.length) {
      const take = Math.min(this.size - this.filled, samples.length - i)
      this.buf.set(samples.subarray(i, i + take), this.filled)
      this.filled += take
      i += take
      if (this.filled === this.size) {
        frames.push(this.buf)
        this.buf = new Int16Array(this.size)
        this.filled = 0
      }
    }
    return frames
  }
}

/**
 * 기본 레이트(보통 48kHz) → 16kHz. 16kHz AudioContext를 못 만드는 브라우저(파이어폭스
 * `NotSupportedError`)용 대체 분기다. 크롬은 컨텍스트가 직접 리샘플하므로 이 길을 타지 않는다.
 *
 * 블랙만 창을 씌운 sinc 저역 통과(차단 7kHz)를 출력 시각마다 분수 위치에서 계산한다.
 * 3샘플 평균은 12kHz를 9.5dB밖에 못 줄여서 쓰지 않는다(verify-web D10-3).
 */
export class Downsampler {
  private readonly ratio: number // 입력 샘플 / 출력 샘플
  private readonly half: number // 커널 반폭(입력 샘플 수)
  private readonly fc: number // 차단 주파수 / 입력 레이트
  private buf: number[] = []
  private pos = 0 // 다음 출력 샘플의 위치(buf 기준 입력 샘플 번호)

  constructor(inputRate: number, outputRate: number = WIRE_RATE) {
    this.ratio = inputRate / outputRate
    this.half = Math.ceil(16 * this.ratio)
    this.fc = (outputRate * 0.4375) / inputRate // 16k 출력이면 7kHz
  }

  push(input: Float32Array): Float32Array {
    if (this.ratio === 1) return input.slice()
    for (let i = 0; i < input.length; i++) this.buf.push(input[i])
    const out: number[] = []
    while (this.pos + this.half < this.buf.length) {
      out.push(this.sampleAt(this.pos))
      this.pos += this.ratio
    }
    const drop = Math.floor(this.pos - this.half) - 1
    if (drop > 0) {
      this.buf.splice(0, drop)
      this.pos -= drop
    }
    return Float32Array.from(out)
  }

  private sampleAt(t: number): number {
    let acc = 0
    let norm = 0
    const from = Math.max(0, Math.ceil(t - this.half))
    const to = Math.min(this.buf.length - 1, Math.floor(t + this.half))
    for (let i = from; i <= to; i++) {
      const w = this.kernel(t - i)
      acc += this.buf[i] * w
      norm += w
    }
    return norm === 0 ? 0 : acc / norm
  }

  private kernel(d: number): number {
    const x = 2 * this.fc * d
    const sinc = x === 0 ? 1 : Math.sin(Math.PI * x) / (Math.PI * x)
    const r = d / this.half // -1..1
    const blackman = 0.42 + 0.5 * Math.cos(Math.PI * r) + 0.08 * Math.cos(2 * Math.PI * r)
    return sinc * blackman
  }
}

/** Int16 → Float32(-1..1). AudioBuffer에 넣을 값. */
export function int16ToFloat(input: Int16Array): Float32Array {
  const out = new Float32Array(input.length)
  for (let i = 0; i < input.length; i++) {
    out[i] = input[i] / 32768
  }
  return out
}
