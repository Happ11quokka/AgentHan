// 재생 예약 시각 계산 (verify-web D8·D12). AudioContext 시각(초) 기준 순수 계산.

/** 큐가 비었을 때 지금보다 이만큼 뒤에 시작한다(첫 조각이 잘리지 않게). */
export const START_LEAD_S = 0.05
/** 마지막 소리가 끝난 뒤 스피커 지연(outputLatency)에 더해 기다리는 여유. */
export const MIC_REOPEN_MARGIN_MS = 150

export interface ChunkSlot {
  start: number
  end: number
}

/**
 * 조각 하나의 시작·끝 시각. 앞 조각 끝(nextTime)에 바로 이어 붙이고, 큐가 비어
 * nextTime이 지났으면 now + 리드에서 다시 시작한다. 시작은 샘플 경계에 맞춘다
 * (경계가 아니면 같은 레이트에서도 크롬이 보간 경로를 탄다, D12-1).
 */
export function scheduleChunk(args: {
  nextTime: number
  now: number
  samples: number
  rate: number
  lead?: number
}): ChunkSlot {
  const { nextTime, now, samples, rate, lead = START_LEAD_S } = args
  const start = nextTime >= now ? nextTime : Math.round((now + lead) * rate) / rate
  return { start, end: start + samples / rate }
}

/** `onended` 뒤 마이크 프레임을 다시 보내기까지 기다릴 시간(ms). */
export function micReopenDelayMs(outputLatency: number | undefined): number {
  const latencyMs = outputLatency !== undefined && Number.isFinite(outputLatency) ? outputLatency * 1000 : 0
  return Math.round(latencyMs + MIC_REOPEN_MARGIN_MS)
}
