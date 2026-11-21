// 사장님 화면 ↔ 서버 (서버 `voice/owner/http.py`). SSE 하나로 받고 POST로 보낸다.
// 모든 경로에 쿼리 `key`(사장님 화면 비밀 토큰)를 붙인다. `EventSource`는 헤더를 못 붙인다.
import type { KnowledgePreview, OwnerAction, OwnerAnswer } from './ownerState.ts'
import type { RejectBody } from './rejectSheet.ts'

const BASE = '/api/owner'

const KEY_STORE = 'agenthan.ownerKey.v1'

/**
 * 사장님 화면 토큰. 주소(`?key=…`)에 있으면 이 브라우저에 기억해 두고, 없으면 기억해 둔 값을 쓴다.
 * 그래서 한 번 키 있는 주소(`/?key=…` 또는 `/owner?key=…`)로 열었던 브라우저는 비서 화면의
 * "전화 주문 카드" 링크나 키 없는 `/owner`로 와도 연결된다. 둘 다 없으면 빈 글(서버가 401)
 */
export function ownerKey(search: string): string {
  const fromUrl = new URLSearchParams(search).get('key') ?? ''
  try {
    if (fromUrl) localStorage.setItem(KEY_STORE, fromUrl)
    else return localStorage.getItem(KEY_STORE) ?? ''
  } catch {
    // 사생활 보호 창 등에서 저장소를 못 쓰면 주소의 키만 쓴다
  }
  return fromUrl
}

/** SSE를 열고 이벤트를 리듀서 행동으로 넘긴다. 돌려준 함수로 닫는다. */
export function connectOwnerStream(key: string, dispatch: (a: OwnerAction) => void): () => void {
  const source = new EventSource(`${BASE}/stream?key=${encodeURIComponent(key)}`)
  source.addEventListener('snapshot', (e) => {
    const data = JSON.parse((e as MessageEvent<string>).data)
    dispatch({ type: 'snapshot', data, receivedAt: Date.now() })
  })
  source.addEventListener('order.update', (e) => {
    dispatch({ type: 'order.update', data: JSON.parse((e as MessageEvent<string>).data) })
  })
  source.addEventListener('call.update', (e) => {
    dispatch({ type: 'call.update', data: JSON.parse((e as MessageEvent<string>).data) })
  })
  source.addEventListener('call.caption', (e) => {
    dispatch({ type: 'call.caption', data: JSON.parse((e as MessageEvent<string>).data) })
  })
  source.addEventListener('owner.question', (e) => {
    dispatch({ type: 'owner.question', data: JSON.parse((e as MessageEvent<string>).data) })
  })
  source.addEventListener('owner.question.update', (e) => {
    dispatch({ type: 'owner.question.update', data: JSON.parse((e as MessageEvent<string>).data) })
  })
  source.addEventListener('knowledge.update', (e) => {
    dispatch({ type: 'knowledge.update', data: JSON.parse((e as MessageEvent<string>).data) })
  })
  source.addEventListener('owner.sold_out', (e) => {
    dispatch({ type: 'owner.sold_out', data: JSON.parse((e as MessageEvent<string>).data) })
  })
  source.onerror = () => {
    // 200이 아니면(토큰 틀림 401) 브라우저가 다시 붙지 않고 CLOSED가 된다. 그 밖은 스스로 다시 붙는다
    dispatch({ type: source.readyState === EventSource.CLOSED ? 'failed' : 'lost' })
  }
  return () => source.close()
}

/** 사장님 화면 POST 하나. 경로는 `/api/owner/` 뒤, 토큰은 쿼리 `key`로 붙인다 */
async function post(key: string, path: string, body?: unknown): Promise<Response> {
  const url = `${BASE}/${path}?key=${encodeURIComponent(key)}`
  if (body === undefined) return fetch(url, { method: 'POST' })
  return fetch(url, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
  })
}

/**
 * 사장님 동작 POST. 됐는지만 돌려준다. 바뀐 카드는 SSE로만 받는다(서버가 늘 같은 카드를 다시
 * 보낸다). 응답 카드를 화면에 넣으면 늦게 온 응답이 SSE로 먼저 온 더 새 카드를 덮는다 (리뷰 C8)
 */
async function act(key: string, path: string, body?: unknown): Promise<boolean> {
  return (await post(key, path, body)).ok
}

const id = (value: string) => encodeURIComponent(value)

/** 접수 버튼. 실패(없는 카드 404)면 false */
export function acceptOrder(key: string, orderId: string): Promise<boolean> {
  return act(key, `orders/${id(orderId)}/accept`)
}

/**
 * 거절 사유 시트 보내기 (주문 승인 티켓 05). 통화 중이면 손님이 사유별 사과를 듣고, 손님에게 취소
 * 문자가 간다. 실패(이미 결정함·손님이 고치는 중 409, 빈 글 422 등)면 false
 */
export function rejectOrder(key: string, orderId: string, body: RejectBody): Promise<boolean> {
  return act(key, `orders/${id(orderId)}/reject`, body)
}

/**
 * 품절 되돌리기 (주문 승인 티켓 06). 오늘 품절에서 그 메뉴를 빼 다음 통화부터 다시 받는다. 바뀐
 * 목록은 SSE `owner.sold_out`으로 온다. 실패(없는 메뉴 404 등)면 false
 */
export function undoSoldOut(key: string, menuId: string): Promise<boolean> {
  return act(key, `sold-out/${id(menuId)}/undo`)
}

/** 닫기 버튼(넘김·미완료 통화 카드). 실패(통화 중·주문됨 409 등)면 false */
export function dismissCall(key: string, callId: string): Promise<boolean> {
  return act(key, `calls/${id(callId)}/dismiss`)
}

/**
 * 질문 카드 열람(티켓 06). 사장님이 카드를 눌러 펼칠 때만 보낸다(리뷰 P4). 통화가 기다리는 중이면
 * 손님이 "사장님이 보고 계세요"를 한 번 듣는다
 */
export function seeQuestion(key: string, questionId: string): Promise<boolean> {
  return act(key, `questions/${id(questionId)}/seen`)
}

/**
 * 질문 카드 답: 버튼(티켓 05) 또는 자유 입력(`'free'` + 사장님 글, 티켓 07). 자유 입력 글은 손님에게
 * 말하지 않고 번역해 문자로만 간다. 실패(이미 답함 409, 빈 글 422 등)면 false
 */
export function answerQuestion(
  key: string,
  questionId: string,
  answer: OwnerAnswer | 'free',
  text = '',
): Promise<boolean> {
  return act(key, `questions/${id(questionId)}/answer`, answer === 'free' ? { answer, text } : { answer })
}

/**
 * "손님이 궁금해한 것" 카드 답 미리보기(티켓 13): 사장님 글 → 한국어 번역. 저장하지 않는다.
 * 실패면 null(없는 카드 404, 빈 글 422 등)
 */
export async function previewKnowledge(
  key: string,
  cardId: string,
  text: string,
): Promise<KnowledgePreview | null> {
  const res = await post(key, `knowledge/${id(cardId)}/preview`, { text })
  return res.ok ? ((await res.json()) as KnowledgePreview) : null
}

/**
 * "저장"(티켓 13): 한국어(번역문 또는 사장님이 고친 글)를 그대로 보충 지식으로. 다음 통화부터 AI가
 * "사장님 말씀으로는 …"으로 말한다. 실패면 false
 */
export function saveKnowledge(key: string, cardId: string, textKo: string, text: string): Promise<boolean> {
  return act(key, `knowledge/${id(cardId)}/save`, { text_ko: textKo, text })
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
