// 사장님 화면 새 주문 알림음 파일을 만든다 (4단계 티켓 08). 사람 목소리·TTS가 아닌 사인파 세 개다.
// 생각 중 효과음(`make-thinking-cue.mjs`)처럼 직접 만든 파일만 쓴다(CLOVA Voice 합성음은 저장·재사용 불가).
//
//   node web/scripts/make-order-chime.mjs   # → web/src/screens/owner/order-chime.wav
//
// 0.75초 한 번: 미(659Hz) → 솔(784Hz) → 높은 도(1047Hz)를 0.13초 간격으로 올린다. 음마다 5ms에 올라가
// 종소리처럼 줄어든다(지수 감쇠 + 끝 직선 페이드, 딸깍 소리 없음). 생각 중 효과음(도·미, 18%)과
// 다르게 들리도록 세 음 상행이고, 주방 소음 속에서 들리게 크기는 풀스케일의 45%(약 -7 dBFS)로 둔다.
import { writeFileSync } from 'node:fs'
import { dirname, join } from 'node:path'
import { fileURLToPath } from 'node:url'

const RATE = 16_000
const LENGTH_S = 0.75
const PEAK = 0.45
const ATTACK_S = 0.005
const DECAY_PER_S = 9
const NOTES = [
  { at: 0.0, hz: 659.25, len: 0.35 },
  { at: 0.13, hz: 783.99, len: 0.35 },
  { at: 0.26, hz: 1046.5, len: 0.49 },
]

const mix = new Float64Array(Math.round(RATE * LENGTH_S))
for (const { at, hz, len } of NOTES) {
  const start = Math.round(at * RATE)
  const n = Math.round(len * RATE)
  for (let i = 0; i < n && start + i < mix.length; i++) {
    const t = i / RATE
    const attack = Math.min(1, t / ATTACK_S)
    const decay = Math.exp(-DECAY_PER_S * t)
    const tail = 1 - i / n // 음 끝에서 0으로
    mix[start + i] += attack * decay * tail * Math.sin(2 * Math.PI * hz * t)
  }
}

// 겹친 음의 가장 큰 값을 PEAK에 맞춘다
const loudest = mix.reduce((m, v) => Math.max(m, Math.abs(v)), 0)
const samples = new Int16Array(mix.length)
for (let i = 0; i < mix.length; i++) {
  samples[i] = Math.round((mix[i] / loudest) * PEAK * 32_767)
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

const out = join(dirname(fileURLToPath(import.meta.url)), '../src/screens/owner/order-chime.wav')
writeFileSync(out, Buffer.concat([header, data]))
console.log(`${out} (${header.length + data.length} bytes)`)
