// 주문 현황 스트림 (전화 메뉴·주문 현황·쿠폰 티켓 05). EventSource는 가짜로 바꿔 끼운다.
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import type { OrderStatusJson } from './orderStatus.ts'
import { ORDER_STREAM_RETRY_MS, openOrderStream, orderStreamUrl } from './orderStream.ts'

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

const status = (stage: OrderStatusJson['stage']) => ({ order_id: 'call-1', stage }) as OrderStatusJson

describe('order status stream (티켓 05)', () => {
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

  it('opens the stream for this order number and hands over each status', () => {
    const got: string[] = []
    openOrderStream('call 1', (o) => got.push(o.stage), () => {}, { open })
    const [source] = opened
    expect(source.url).toBe(orderStreamUrl('call 1'))
    expect(source.url).toBe('/api/phone/orders/call%201/stream')
    source.emit('order.status', status('checking'))
    source.emit('order.status', status('confirmed'))
    expect(got).toEqual(['checking', 'confirmed'])
  })

  it('says the stream was lost so the caller reads the order again', () => {
    let lost = 0
    openOrderStream('call-1', () => {}, () => lost++, { open })
    opened[0].onerror?.() // 브라우저가 스스로 다시 붙는 끊김
    expect(lost).toBe(1)
    expect(opened[0].closed).toBe(false)
    expect(opened).toHaveLength(1)
  })

  it('opens again later when the server refused the stream', () => {
    let lost = 0
    openOrderStream('call-1', () => {}, () => lost++, { open })
    opened[0].readyState = FakeSource.CLOSED // 404·서버 오류: 브라우저가 다시 붙지 않는다
    opened[0].onerror?.()
    expect(lost).toBe(1)
    vi.advanceTimersByTime(ORDER_STREAM_RETRY_MS - 1)
    expect(opened).toHaveLength(1)
    vi.advanceTimersByTime(1)
    expect(opened).toHaveLength(2)
    expect(opened[1].url).toBe('/api/phone/orders/call-1/stream')
  })

  it('closes when the server says its window ended, and when the caller closes it', () => {
    openOrderStream('call-1', () => {}, () => {}, { open })
    opened[0].emit('order.end', {})
    expect(opened[0].closed).toBe(true)

    const close = openOrderStream('call-1', () => {}, () => {}, { open })
    opened[1].readyState = FakeSource.CLOSED
    opened[1].onerror?.()
    close() // 다시 열기도 멈춘다
    vi.advanceTimersByTime(ORDER_STREAM_RETRY_MS * 2)
    expect(opened).toHaveLength(2)
  })
})
