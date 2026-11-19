// 카드 색 3단계 (4단계 티켓 08). 픽업까지 남은 시간으로 정한다: 여유 → 곧 → 지남.
// 픽업 시각이 없는 카드(넘김·미완료 통화 등)는 기본색(none)이다.
import { ALERT_SETTINGS } from './alertSettings.ts'

/** relaxed: 여유, soon: 곧, late: 지남, none: 픽업 시각 없음(기본색) */
export type PickupBand = 'relaxed' | 'soon' | 'late' | 'none'

export function pickupBand(
  card: { pickup_time?: string | null },
  nowMs: number,
  soonWithinMs: number = ALERT_SETTINGS.soonWithinMs,
): PickupBand {
  const pickupMs = card.pickup_time ? Date.parse(card.pickup_time) : Number.NaN
  if (Number.isNaN(pickupMs)) return 'none'
  const left = pickupMs - nowMs
  if (left <= 0) return 'late'
  if (left <= soonWithinMs) return 'soon'
  return 'relaxed'
}
