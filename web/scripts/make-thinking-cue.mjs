// 생각 중 효과음 파일을 만든다 (3단계 티켓 01). 사람 목소리·TTS가 아닌 사인파 두 개다.
// CLOVA Voice 합성음은 저장·재사용할 수 없어서(이용 약관) 효과음은 직접 만든 파일만 쓴다.
//
//   node web/scripts/make-thinking-cue.mjs   # → web/src/screens/phone/thinking-cue.wav
//
// 1.2초 한 주기: 0ms에 도(523Hz), 180ms에 미(659Hz)를 90ms씩 부드럽게(올림 코사인) 내고 나머지는
// 무음. 브라우저가 반복 재생하다가 봇 목소리가 오면 멈춘다. 크기는 풀스케일의 18%(약 -15 dBFS)로
// 목소리보다 작게 둔다.
import { writeFileSync } from 'node:fs'
import { dirname, join } from 'node:path'
import { fileURLToPath } from 'node:url'

const RATE = 16_000
const PERIOD_S = 1.2
const PEAK = 0.18
const NOTES = [
  { at: 0.0, hz: 523.25, len: 0.09 },
  { at: 0.18, hz: 659.25, len: 0.09 },
]

const samples = new Int16Array(Math.round(RATE * PERIOD_S))
for (const { at, hz, len } of NOTES) {
  const start = Math.round(at * RATE)
  const n = Math.round(len * RATE)
  for (let i = 0; i < n; i++) {
    const envelope = 0.5 - 0.5 * Math.cos((2 * Math.PI * i) / n) // 0 → 1 → 0, 딸깍 소리 없음
    const value = PEAK * envelope * Math.sin((2 * Math.PI * hz * i) / RATE)
    samples[start + i] = Math.round(value * 32_767)
  }
}

const data = Buffer.from(samples.buffer)
const header = Buffer.alloc(44)
header.write('RIFF', 0)
header.writeUInt32LE(36 + data.length, 4)
header.write('WAVE', 8)
header.write('fmt ', 12)
header.writeUInt32LE(16, 16) // PCM fmt 크기
header.writeUInt16LE(1, 20) // PCM
header.writeUInt16LE(1, 22) // mono
header.writeUInt32LE(RATE, 24)
header.writeUInt32LE(RATE * 2, 28) // 바이트/초
header.writeUInt16LE(2, 32) // 블록 정렬
header.writeUInt16LE(16, 34) // 비트
header.write('data', 36)
header.writeUInt32LE(data.length, 40)

const out = join(dirname(fileURLToPath(import.meta.url)), '../src/screens/phone/thinking-cue.wav')
writeFileSync(out, Buffer.concat([header, data]))
console.log(`${out} (${header.length + data.length} bytes)`)
