// 손님 취소 (전화 메뉴·주문 현황·쿠폰 티켓 07): 취소 API 응답 → 화면 동작, 버튼·안내가 보이는 때, 취소한 뒤 문구.
import { describe, expect, it } from 'vitest'
import { cancelHint, cancelOrder, orderCancelUrl } from './orderCancel.ts'
import { orderHeadline, type OrderStatusJson, orderStatusReducer, initialOrderStatus } from './orderStatus.ts'

const status = (extra: Partial<OrderStatusJson> = {}): OrderStatusJson => ({
  order_id: 'call-abc',
  stage: 'checking',
  placed_at: '2026-11-21T16:00:00+09:00',
  lines: [{ menu_id: 'm01', name: '메도빅', quantity: 1, options: [], request: '', subtotal: 7500 }],
  amount: { subtotal: 7500, total: 7500 },
  pickup: { time: '2026-11-21T18:30:00+09:00', label: '18:30', original_time: '', original_label: '' },
  outcome: null,
  reason: null,
  sold_out: [],
  can_cancel: true,
  ...extra,
})

const json = (body: unknown, init: ResponseInit = {}) =>
  Promise.resolve(new Response(JSON.stringify(body), { headers: { 'content-type': 'application/json' }, ...init }))

const CUSTOMER_CANCELLED = status({ stage: 'cancelled', outcome: 'customer_cancelled', can_cancel: false })
const ACCEPTED = status({ stage: 'confirmed', outcome: 'accepted', can_cancel: false })

describe('the cancel API', () => {
  it('posts to the order number path with no token, and a cancel shows the cancelled order', async () => {
    const calls: [string, RequestInit | undefined][] = []
    const result = await cancelOrder('call-abc', (url, init) => {
      calls.push([url, init])
      return json(CUSTOMER_CANCELLED)
    })
    expect(calls).toEqual([['/api/phone/orders/call-abc/cancel', { method: 'POST' }]])
    expect(orderCancelUrl('a/b')).toBe('/api/phone/orders/a%2Fb/cancel')
    expect(result).toEqual({ type: 'cancelled', order: CUSTOMER_CANCELLED })
  })

  it('a refusal carries the reason, its line and the order as it is now (the side that won)', async () => {
    const message = '가게가 이미 주문을 확정했어요. 취소하려면 가게로 전화 주세요.'
    const result = await cancelOrder('call-abc', () =>
      json({ detail: { code: 'accepted', message, order: ACCEPTED } }, { status: 409 }),
    )
    expect(result).toEqual({ type: 'refused', code: 'accepted', message, order: ACCEPTED })
  })

  it('404 is missing; other errors and network failures are failed', async () => {
    expect(await cancelOrder('x', () => json({ detail: '주문을 찾을 수 없어요' }, { status: 404 }))).toEqual({
      type: 'missing',
    })
    expect(await cancelOrder('x', () => json({}, { status: 500 }))).toEqual({ type: 'failed' })
    expect(await cancelOrder('x', () => Promise.reject(new TypeError('offline')))).toEqual({ type: 'failed' })
  })
})

describe('when the customer can cancel', () => {
  it('an order on time cannot be cancelled here: call the store', () => {
    expect(cancelHint(ACCEPTED)).toBe('취소하려면 가게로 전화 주세요.')
    expect(cancelHint(status({ stage: 'confirmed', outcome: 'auto', can_cancel: false }))).toBe(
      '취소하려면 가게로 전화 주세요.',
    )
  })

  it('a checking or later-pickup order has the button instead; a cancelled order has neither', () => {
    expect(cancelHint(status())).toBeNull()
    expect(cancelHint(status({ stage: 'confirmed', outcome: 'delayed', can_cancel: true }))).toBeNull()
    expect(cancelHint(CUSTOMER_CANCELLED)).toBeNull()
  })

  it('after the cancel the headline says the customer cancelled it', () => {
    expect(orderHeadline(CUSTOMER_CANCELLED)).toMatchObject({ tone: 'stop', title: '주문을 취소했어요' })
  })

  it('a cancel moves a later-pickup order from confirmed to cancelled', () => {
    let s = initialOrderStatus('call-abc')
    s = orderStatusReducer(s, { type: 'loaded', order: status({ stage: 'confirmed', outcome: 'delayed' }) })
    s = orderStatusReducer(s, { type: 'loaded', order: CUSTOMER_CANCELLED })
    expect(s.order?.outcome).toBe('customer_cancelled')
  })
})
