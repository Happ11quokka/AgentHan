// 통화 뒤 "받은 문자" 스트림 (4단계 티켓 07). EventSource는 가짜로 바꿔 끼운다.
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import type { CustomerText } from './callState.ts'
import { TEXTS_WINDOW_MS, openTextStream, textsUrl } from './textStream.ts'

class FakeSource {
  static CLOSED = 2
  readyState = 1
  closed = false
  onerror: (() => void) | null = null
  private readonly listeners = new Map<string, (e: { data: string }) => void>()
  readonly url: string
  constructor(url: string) {
    this.url = url
  }
  addEventListener(name: string, fn: (e: { data: string }) => void) {
    this.listeners.set(name, fn)
  }
  emit(name: string, data: unknown) {
    this.listeners.get(name)?.({ data: JSON.stringify(data) })
  }
  close() {
    this.closed = true
    this.readyState = FakeSource.CLOSED
  }
}

const text = (id: string): CustomerText => ({
  id,
  call_id: 'call-1',
  kind: 'late_answer',
  text: '메도빅 문의 답변이에요. 사장님께 여쭤보니 네, 된다고 하세요.',
  sent_at: '2026-11-08T16:07:00+09:00',
  sent_label: '16:07',
  question_id: 'call-1-q1',
  translate_failed: false,
})

describe('received-text stream after the call (ticket 07)', () => {
  let opened: FakeSource[]
  const open = (url: string) => {
    const source = new FakeSource(url)
    opened.push(source)
    return source
  }

  beforeEach(() => {
    opened = []
    vi.useFakeTimers()
  })
  afterEach(() => {
    vi.useRealTimers()
  })

  it('opens the stream for this call and hands over the snapshot and each new text', () => {
    const got: CustomerText[][] = []
    openTextStream('call 1', (texts) => got.push(texts), { open })
    const [source] = opened
    expect(source.url).toBe(textsUrl('call 1'))
    expect(source.url).toBe('/api/phone/calls/call%201/sms')
    source.emit('sms.snapshot', { call_id: 'call 1', sms: [text('s1')] })
    source.emit('sms', text('s2'))
    expect(got.map((texts) => texts.map((t) => t.id))).toEqual([['s1'], ['s2']])
  })

  it('closes when the server says the window ended, so the browser does not reconnect', () => {
    openTextStream('call-1', () => {}, { open })
    opened[0].emit('sms.end', {})
    expect(opened[0].closed).toBe(true)
  })

  it('closes by itself after two minutes', () => {
    openTextStream('call-1', () => {}, { open })
    vi.advanceTimersByTime(TEXTS_WINDOW_MS - 1)
    expect(opened[0].closed).toBe(false)
    vi.advanceTimersByTime(1)
    expect(opened[0].closed).toBe(true)
    expect(TEXTS_WINDOW_MS).toBe(120_000)
  })

  it('stops when the server refuses the stream (unknown call) and when the caller closes it', () => {
    openTextStream('nope', () => {}, { open })
    opened[0].readyState = FakeSource.CLOSED
    opened[0].onerror?.()
    expect(opened[0].closed).toBe(true)

    const close = openTextStream('call-1', () => {}, { open })
    close()
    expect(opened[1].closed).toBe(true)
  })
})
