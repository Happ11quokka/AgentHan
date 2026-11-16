// 서버가 내리는 오디오 프레임 해석 (spec WS 계약, 서버 voice/call/frames.py).
//
//   u8 kind (1 = 오디오) | u8 flags (bit0 = 그 문장의 마지막 조각) | u16 seq | u32 turn
//   | PCM16LE 16kHz mono
//
// 모두 리틀엔디언. seq는 턴 안의 문장 번호(0부터)이고 bot.say·playback.*의 seq와 같다.

export const KIND_AUDIO = 1
const FLAG_LAST_CHUNK = 1
const HEADER_BYTES = 8

export interface AudioFrame {
  turn: number
  seq: number
  last: boolean
  pcm: Int16Array
}

export function parseAudioFrame(buf: ArrayBuffer): AudioFrame {
  if (buf.byteLength < HEADER_BYTES) {
    throw new Error(`오디오 프레임이 너무 짧아요: ${buf.byteLength}바이트`)
  }
  const view = new DataView(buf)
  const kind = view.getUint8(0)
  if (kind !== KIND_AUDIO) {
    throw new Error(`모르는 프레임 kind: ${kind}`)
  }
  const flags = view.getUint8(1)
  // 오프셋 8이라 Int16Array 정렬 조건(2의 배수)을 만족한다. 홀수 길이 끝 바이트는 버린다.
  const samples = Math.floor((buf.byteLength - HEADER_BYTES) / 2)
  return {
    turn: view.getUint32(4, true),
    seq: view.getUint16(2, true),
    last: (flags & FLAG_LAST_CHUNK) !== 0,
    pcm: new Int16Array(buf, HEADER_BYTES, samples),
  }
}
