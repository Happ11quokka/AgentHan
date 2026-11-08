// 통화 화면에 보이는 글 모양.

/** 가게 시간대. 서버(`PHONE_TZ`) 기본값과 같다. 픽업 시각은 기기 시간대가 아니라 가게 시각으로 보인다. */
export const STORE_TIME_ZONE = 'Asia/Seoul'

/** 주문서의 픽업 시각(ISO 8601, 시간대 포함) → "오후 6:30" (가게 시각). */
export function pickupLabel(iso: string): string {
  return new Date(iso).toLocaleTimeString('ko-KR', {
    hour: 'numeric',
    minute: '2-digit',
    timeZone: STORE_TIME_ZONE,
  })
}
