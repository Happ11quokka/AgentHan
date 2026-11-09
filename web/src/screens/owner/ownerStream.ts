// 사장님 화면 ↔ 서버 (서버 `voice/owner/http.py`). SSE 하나로 받고 POST로 보낸다.
// 모든 경로에 쿼리 `key`(사장님 화면 비밀 토큰)를 붙인다. `EventSource`는 헤더를 못 붙인다.
import type { CallCard, OrderCard, OwnerAction } from './ownerState.ts'

const BASE = '/api/owner'

/** `/owner?key=…` 주소에서 토큰을 읽는다. 없으면 빈 글(서버가 401) */
export function ownerKey(search: string): string {
  return new URLSearchParams(search).get('key') ?? ''
}

/** SSE를 열고 이벤트를 리듀서 행동으로 넘긴다. 돌려준 함수로 닫는다. */
export function connectOwnerStream(key: string, dispatch: (a: OwnerAction) => void): () => void {
  const source = new EventSource(`${BASE}/stream?key=${encodeURIComponent(key)}`)
  source.addEventListener('snapshot', (e) => {
    dispatch({ type: 'snapshot', data: JSON.parse((e as MessageEvent<string>).data) })
  })
  source.addEventListener('order.update', (e) => {
    dispatch({ type: 'order.update', data: JSON.parse((e as MessageEvent<string>).data) })
  })
  source.addEventListener('call.update', (e) => {
    dispatch({ type: 'call.update', data: JSON.parse((e as MessageEvent<string>).data) })
  })
  source.onerror = () => {
    // 200이 아니면(토큰 틀림 401) 브라우저가 다시 붙지 않고 CLOSED가 된다. 그 밖은 스스로 다시 붙는다
    dispatch({ type: source.readyState === EventSource.CLOSED ? 'failed' : 'lost' })
  }
  return () => source.close()
}

/** 접수 버튼. 성공하면 바뀐 카드(같은 카드는 SSE `order.update`로도 온다). 실패면 null */
export async function acceptOrder(key: string, id: string): Promise<OrderCard | null> {
  const res = await fetch(
    `${BASE}/orders/${encodeURIComponent(id)}/accept?key=${encodeURIComponent(key)}`,
    { method: 'POST' },
  )
  return res.ok ? ((await res.json()) as OrderCard) : null
}

/** 닫기 버튼(넘김·미완료 통화 카드). 성공하면 닫힌 카드(SSE `call.update`로도 온다). 실패면 null */
export async function dismissCall(key: string, id: string): Promise<CallCard | null> {
  const res = await fetch(
    `${BASE}/calls/${encodeURIComponent(id)}/dismiss?key=${encodeURIComponent(key)}`,
    { method: 'POST' },
  )
  return res.ok ? ((await res.json()) as CallCard) : null
}

/**
 * "가게 열기" 버튼에서(사용자 동작 안에서) 부른다: 알림 소리용 `AudioContext`를 푼다.
 * 지원하지 않거나 거절되면 null(화면에 "소리를 켤 수 없어요"). 화면 꺼짐 방지는 `screenWakeLock.ts`(티켓 08).
 */
export async function wakeDevice(): Promise<AudioContext | null> {
  try {
    const audio = new AudioContext()
    await audio.resume()
    return audio
  } catch {
    return null
  }
}
