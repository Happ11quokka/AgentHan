// 통화 뒤 "받은 문자" 스트림 (4단계 티켓 07, 서버 `voice/owner/http.py` customer_texts_router).
// 통화가 끝나면 통화 id로 짧은 SSE를 열어 사장님의 늦은 답 같은 문자를 받는다. 실제 문자는 보내지 않는다.
// 토큰은 없다(통화 id는 추측할 수 없는 값). 서버는 먼저 `sms.snapshot`, 그 뒤 새 문자마다 `sms`,
// 2분이 지나면 `sms.end`를 보내고 닫는다. EventSource는 서버가 닫으면 다시 붙으므로 `sms.end`에서 닫는다.
import type { CustomerText } from './callState.ts'

/** 문자 스트림을 열어 두는 시간 (spec "최대 2분"). 서버도 같은 시간에 `sms.end`로 닫는다 */
export const TEXTS_WINDOW_MS = 120_000

/** `EventSource.CLOSED` (시험 환경에는 EventSource가 없다) */
const SOURCE_CLOSED = 2

export function textsUrl(callId: string): string {
  return `/api/phone/calls/${encodeURIComponent(callId)}/sms`
}

/** EventSource에서 쓰는 것만. 시험은 가짜를 넣는다 */
export interface TextSource {
  readyState: number
  onerror: ((ev: Event) => unknown) | null
  addEventListener(name: string, listener: (e: { data: string }) => void): void
  close(): void
}

export interface TextStreamOptions {
  windowMs?: number
  open?: (url: string) => TextSource
}

/** 스트림을 연다. 받은 문자는 `onTexts`로(이미 받은 것이 다시 올 수 있다, 리듀서가 id로 거른다). 돌려준 함수로 닫는다 */
export function openTextStream(
  callId: string,
  onTexts: (texts: CustomerText[]) => void,
  { windowMs = TEXTS_WINDOW_MS, open = (url) => new EventSource(url) }: TextStreamOptions = {},
): () => void {
  const source = open(textsUrl(callId))
  let timer: ReturnType<typeof setTimeout> | null = null
  const close = () => {
    if (timer !== null) clearTimeout(timer)
    timer = null
    source.close()
  }
  source.addEventListener('sms.snapshot', (e) => {
    onTexts((JSON.parse(e.data) as { sms: CustomerText[] }).sms)
  })
  source.addEventListener('sms', (e) => onTexts([JSON.parse(e.data) as CustomerText]))
  source.addEventListener('sms.end', close)
  source.onerror = () => {
    // 200이 아니면(모르는 통화 404) 브라우저가 다시 붙지 않는다. 그 밖은 창이 남아 있는 동안 스스로 다시 붙는다
    if (source.readyState === SOURCE_CLOSED) close()
  }
  timer = setTimeout(close, windowMs)
  return close
}
