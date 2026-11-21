// 사장님 화면 POST (서버 `voice/owner/http.py`). fetch는 가짜로 바꿔 끼운다.
import { afterEach, describe, expect, it, vi } from 'vitest'
import { acceptOrder, answerQuestion, ownerKey, previewKnowledge } from './ownerStream.ts'

function stubFetch(status: number, body: unknown = {}) {
  const calls: { url: string; init: RequestInit }[] = []
  vi.stubGlobal('fetch', async (url: string, init: RequestInit) => {
    calls.push({ url, init })
    return new Response(JSON.stringify(body), { status })
  })
  return calls
}

afterEach(() => vi.unstubAllGlobals())

describe('owner actions (review C8)', () => {
  it('only says whether the action went through; the changed card comes by SSE', async () => {
    // 늦게 온 POST 응답 카드가 SSE로 온 더 새 카드(답함)를 덮지 않게, 응답 카드는 화면에 넣지 않는다
    const calls = stubFetch(200, { id: 'call-1-q1', status: 'open' })
    await expect(answerQuestion('k 1', 'call-1-q1', 'free', 'Без орехов')).resolves.toBe(true)
    expect(calls[0].url).toBe('/api/owner/questions/call-1-q1/answer?key=k%201')
    expect(calls[0].init.method).toBe('POST')
    expect(JSON.parse(String(calls[0].init.body))).toEqual({ answer: 'free', text: 'Без орехов' })
  })

  it('says no when the server refuses', async () => {
    stubFetch(409)
    await expect(acceptOrder('k', 'call-1')).resolves.toBe(false)
  })

  it('still hands back the translation preview, which is not a card', async () => {
    stubFetch(200, { text: 'Не сладкий', text_ko: '달지 않아요', translate_failed: false })
    await expect(previewKnowledge('k', 'k1', 'Не сладкий')).resolves.toEqual({
      text: 'Не сладкий',
      text_ko: '달지 않아요',
      translate_failed: false,
    })
  })
})

describe('ownerKey', () => {
  afterEach(() => localStorage.clear())

  it('주소의 키를 쓰고 기억해, 키 없는 주소(비서 화면 링크)에서도 쓴다', () => {
    expect(ownerKey('?key=abc&lang=ko')).toBe('abc')
    expect(ownerKey('?lang=ko')).toBe('abc')
  })

  it('주소에도 기억에도 없으면 빈 글', () => {
    expect(ownerKey('')).toBe('')
  })

  it('새 키가 오면 바꿔 기억한다', () => {
    ownerKey('?key=old')
    ownerKey('?key=new')
    expect(ownerKey('')).toBe('new')
  })
})
