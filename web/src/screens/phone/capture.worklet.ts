// 마이크 캡처 워클릿. 컨텍스트 레이트의 Float32 블록(128샘플)을 받아
// 16kHz Int16 100ms 프레임(1,600샘플 = 3,200바이트)으로 묶어 메인 스레드에 넘긴다.
// 반이중의 0 바꾸기는 메인 스레드가 한다(보낼 때 프레임을 0으로). 여기선 늘 진짜 소리를 낸다.
//
// 불러오기: `import url from './capture.worklet.ts?worker&url'` → audioWorklet.addModule(url).
// Vite가 이 파일과 pcm.ts를 한 파일로 묶어 자산 URL을 준다.

import { CAPTURE_PROCESSOR, Downsampler, FrameChunker, WIRE_RATE, floatToInt16 } from './pcm.ts'

// AudioWorkletGlobalScope 전역. lib.dom에 없어서 여기서만 선언한다.
declare const sampleRate: number
declare class AudioWorkletProcessor {
  readonly port: MessagePort
}
declare function registerProcessor(name: string, ctor: new () => AudioWorkletProcessor): void

class CaptureProcessor extends AudioWorkletProcessor {
  private readonly downsampler = sampleRate === WIRE_RATE ? null : new Downsampler(sampleRate)
  private readonly chunker = new FrameChunker()

  process(inputs: Float32Array[][]): boolean {
    const channel = inputs[0]?.[0]
    if (channel && channel.length > 0) {
      const at16k = this.downsampler ? this.downsampler.push(channel) : channel
      for (const frame of this.chunker.push(floatToInt16(at16k))) {
        this.port.postMessage(frame.buffer, [frame.buffer])
      }
    }
    return true
  }
}

registerProcessor(CAPTURE_PROCESSOR, CaptureProcessor)
