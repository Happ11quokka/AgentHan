// 손님 주문 현황 상태 모듈 (전화 메뉴·주문 현황·쿠폰 티켓 04): 조회 응답 → 단계·문구, 통화가 끝났을 때 주문
// 현황으로 가는 경우와 안 가는 경우. 티켓 05: 사장님 결정별 결과·사유 문구, 스트림 현황, [다시 주문하기].
import { describe, expect, it } from 'vitest'
import { type CallState, initialCallState, type OrderJson } from './callState.ts'
import {
  callOrderId,
  canReorder,
  fetchOrderStatus,
  initialOrderStatus,
  orderHeadline,
  type OrderStatusJson,
  orderStatusReducer,
  orderSettled,
  orderStatusUrl,
  orderSteps,
  pickupMoved,
} from './orderStatus.ts'

const status = (extra: Partial<OrderStatusJson> = {}): OrderStatusJson => ({
  order_id: 'call-abc',
  stage: 'checking',
  placed_at: '2026-11-21T16:00:00+09:00',
  lines: [
    {
      menu_id: 'm01',
      name: '메도빅',
      quantity: 1,
      options: [],
      request: '',
      subtotal: 7500,
    },
  ],
  amount: { subtotal: 7500, total: 7500 },
  pickup: {
    time: '2026-11-21T18:30:00+09:00',
    label: '18:30',
    original_time: '',
    original_label: '',
  },
  outcome: null,
  reason: null,
  sold_out: [],
  can_cancel: false,
  ...extra,
})

const orderJson = (status: string): OrderJson => ({
  id: 'call-abc',
  status,
  lines: [{ menu_id: 'm01', quantity: 1, options: [], request: null }],
  pickup_time: '2026-11-21T18:30:00+09:00',
  customer_name: null,
  customer_phone: null,
  transcript: [],
})

const ended = (order: string | null, extra: Partial<CallState> = {}): CallState => ({
  ...initialCallState,
  status: 'ended',
  callId: 'call-abc',
  order: order === null ? null : { order: orderJson(order), cart: [], total: 7500 },
  ...extra,
})

const json = (body: unknown, init: ResponseInit = {}) =>
  Promise.resolve(
    new Response(JSON.stringify(body), {
      headers: { 'content-type': 'application/json' },
      ...init,
    }),
  )

describe('which ended calls turn into the order status', () => {
  it('a call that ended with a confirmed order shows its order status', () => {
    expect(callOrderId(ended('confirmed'))).toBe('call-abc')
  })

  it('a handed-over, cancelled or silent call keeps the call result', () => {
    expect(callOrderId(ended('handoff', { endReason: 'handoff' }))).toBeNull()
    expect(callOrderId(ended(null, { endReason: 'cancelled' }))).toBeNull()
    expect(callOrderId(ended(null, { endReason: 'no_input' }))).toBeNull()
  })

  it('stays on the call screen while the closing line is still playing', () => {
    expect(callOrderId({ ...ended('confirmed'), status: 'live' })).toBeNull()
  })
})

describe('order status steps and wording', () => {
  it('while the store checks: placed, then "가게 확인 중" is current; no countdown', () => {
    const order = status()
    expect(orderSteps(order).map((s) => [s.label, s.state])).toEqual([
      ['주문 넣음', 'done'],
      ['가게 확인 중', 'current'],
      ['확정', 'todo'],
      ['픽업 18:30', 'todo'],
    ])
    const headline = orderHeadline(order)
    expect(headline).toMatchObject({
      tone: 'wait',
      title: '가게에서 주문을 확인하고 있어요',
    })
    expect(JSON.stringify(headline)).not.toMatch(/분|초|접수/)
  })

  it('after the owner accepts: "확정" with the pickup time', () => {
    const order = status({ stage: 'confirmed', outcome: 'accepted' })
    expect(orderSteps(order).map((s) => [s.label, s.state])).toEqual([
      ['주문 넣음', 'done'],
      ['가게 확인', 'done'],
      ['확정', 'done'],
      ['픽업 18:30', 'current'],
    ])
    expect(orderHeadline(order)).toEqual({
      tone: 'ok',
      title: '주문이 확정됐어요',
      detail: '18:30에 준비해 둘게요.', // 확정 문자와 같은 말
      quote: null,
    })
    expect(pickupMoved(order)).toBeNull()
  })

  it('a cancelled order ends at "주문 취소" without a pickup step', () => {
    const order = status({ stage: 'cancelled', outcome: 'rejected', reason: { code: 'busy', text: BUSY } })
    expect(orderSteps(order).map((s) => s.label)).toEqual(['주문 넣음', '가게 확인', '주문 취소'])
    expect(orderHeadline(order)).toMatchObject({
      tone: 'stop',
      title: '주문이 취소됐어요',
    })
  })
})

const BUSY = '죄송해요, 가게가 바빠서 주문을 받기 어려워 주문이 취소됐어요. 다음에 다시 주문해 주세요.'
const STOCK_OUT = '죄송해요, 메도빅은 지금 재료가 떨어져 주문이 취소됐어요. 다른 메뉴로 다시 주문해 주세요.'

describe('what the order status says after each owner decision (티켓 05)', () => {
  it('a later pickup is confirmed at the new time and shows the original time too', () => {
    const order = status({
      stage: 'confirmed',
      outcome: 'delayed',
      pickup: {
        time: '2026-11-21T18:45:00+09:00',
        label: '18:45',
        original_time: '2026-11-21T18:30:00+09:00',
        original_label: '18:30',
      },
    })
    expect(orderSteps(order).map((s) => [s.label, s.state])).toEqual([
      ['주문 넣음', 'done'],
      ['가게 확인', 'done'],
      ['확정', 'done'],
      ['픽업 18:45', 'current'],
    ])
    expect(pickupMoved(order)).toEqual({ from: '18:30', to: '18:45' })
    expect(orderHeadline(order)).toMatchObject({ tone: 'ok', title: '주문이 확정됐어요' })
    expect(orderHeadline(order).detail).toBe(
      '픽업 시각이 18:30에서 18:45로 바뀌었어요. 18:45에 준비돼요. 어려우시면 전화 주세요.',
    )
    expect(orderHeadline(order).detail).not.toMatch(/괜찮으세요\?/) // 손님에게 되묻지 않는다
    expect(canReorder(order)).toBe(false)
  })

  it('a reject shows the one-line reason the store gave', () => {
    const order = status({ stage: 'cancelled', outcome: 'rejected', reason: { code: 'busy', text: BUSY } })
    expect(orderHeadline(order)).toEqual({ tone: 'stop', title: '주문이 취소됐어요', detail: BUSY, quote: null })
    expect(canReorder(order)).toBe(true)
  })

  it("a written reject shows the owner's words in Korean once translated", () => {
    const translating = status({ stage: 'cancelled', outcome: 'rejected', reason: { code: 'free', text: '' } })
    expect(orderHeadline(translating)).toMatchObject({
      detail: '죄송해요, 가게 사정으로 주문이 취소됐어요.',
      quote: null,
    })
    const translated = status({
      stage: 'cancelled',
      outcome: 'rejected',
      reason: { code: 'free', text: '오늘은 일찍 닫아요' },
    })
    expect(orderHeadline(translated).quote).toBe('오늘은 일찍 닫아요')
  })

  it('out of stock names the menu that ran out', () => {
    const order = status({
      stage: 'cancelled',
      outcome: 'out_of_stock',
      reason: { code: 'out_of_stock', text: STOCK_OUT },
      sold_out: ['메도빅'],
    })
    expect(orderHeadline(order)).toMatchObject({ tone: 'stop', title: '주문이 취소됐어요', detail: STOCK_OUT })
    expect(canReorder(order)).toBe(true)
  })

  it('five silent minutes say the store could not check it', () => {
    const order = status({ stage: 'cancelled', outcome: 'expired' })
    expect(orderHeadline(order).detail).toContain('가게에서 확인하지 못해 주문이 취소됐어요')
    expect(JSON.stringify(orderHeadline(order))).not.toMatch(/5분|접수/)
    expect(canReorder(order)).toBe(true)
  })

  it('only a decided order stops listening; a checking or confirmed order has no reorder button', () => {
    expect(orderSettled(null)).toBe(false)
    expect(orderSettled(status())).toBe(false)
    expect(orderSettled(status({ stage: 'confirmed', outcome: 'accepted' }))).toBe(true)
    expect(orderSettled(status({ stage: 'cancelled', outcome: 'expired' }))).toBe(true)
    expect(canReorder(status())).toBe(false)
    expect(canReorder(status({ stage: 'confirmed', outcome: 'auto' }))).toBe(false)
  })
})

describe('order status state', () => {
  it('loads, shows, and keeps the last status while reloading', () => {
    let s = initialOrderStatus('call-abc')
    expect(s.phase).toBe('loading')
    s = orderStatusReducer(s, { type: 'loaded', order: status() })
    expect(s).toMatchObject({ phase: 'shown', order: { stage: 'checking' } })
    s = orderStatusReducer(s, { type: 'load' })
    expect(s).toMatchObject({ phase: 'loading', order: { stage: 'checking' } })
    s = orderStatusReducer(s, {
      type: 'loaded',
      order: status({ stage: 'confirmed' }),
    })
    expect(s.order?.stage).toBe('confirmed')
  })

  it('takes each status the stream sends', () => {
    let s = orderStatusReducer(initialOrderStatus('call-abc'), { type: 'streamed', order: status() })
    expect(s).toMatchObject({ phase: 'shown', order: { stage: 'checking' } })
    s = orderStatusReducer(s, {
      type: 'streamed',
      order: status({ stage: 'confirmed', outcome: 'accepted' }),
    })
    expect(s.order?.outcome).toBe('accepted')
  })

  it('a late read does not undo a decision the stream already showed', () => {
    let s = orderStatusReducer(initialOrderStatus('call-abc'), {
      type: 'streamed',
      order: status({ stage: 'cancelled', outcome: 'expired' }),
    })
    s = orderStatusReducer(s, { type: 'load' })
    s = orderStatusReducer(s, { type: 'loaded', order: status() }) // 끊김 전에 읽기 시작한 응답
    expect(s).toMatchObject({ phase: 'shown', order: { stage: 'cancelled', outcome: 'expired' } })
  })

  it('drops a response for another order', () => {
    const s = initialOrderStatus('call-abc')
    expect(
      orderStatusReducer(s, {
        type: 'loaded',
        order: status({ order_id: 'call-other' }),
      }),
    ).toBe(s)
  })

  it('an unknown order number is "missing"; a failed read keeps what was shown', () => {
    const shown = orderStatusReducer(initialOrderStatus('call-abc'), {
      type: 'loaded',
      order: status(),
    })
    expect(orderStatusReducer(shown, { type: 'missing' })).toMatchObject({
      phase: 'missing',
      order: null,
    })
    expect(orderStatusReducer(shown, { type: 'failed' })).toMatchObject({
      phase: 'failed',
      order: { stage: 'checking' },
    })
  })
})

describe('reading the order status API', () => {
  it('asks the order number path with no token', async () => {
    const urls: string[] = []
    const action = await fetchOrderStatus('call-abc', (url) => {
      urls.push(url)
      return json(status())
    })
    expect(urls).toEqual(['/api/phone/orders/call-abc'])
    expect(orderStatusUrl('a/b')).toBe('/api/phone/orders/a%2Fb')
    expect(action).toEqual({ type: 'loaded', order: status() })
  })

  it('404 is missing, other errors and network failures are failed', async () => {
    expect(await fetchOrderStatus('x', () => json({ detail: '주문을 찾을 수 없어요' }, { status: 404 }))).toEqual({
      type: 'missing',
    })
    expect(await fetchOrderStatus('x', () => json({}, { status: 500 }))).toEqual({ type: 'failed' })
    expect(await fetchOrderStatus('x', () => Promise.reject(new TypeError('offline')))).toEqual({ type: 'failed' })
  })
})
