// 손님 화면의 주문 현황 다시 열기 (전화 메뉴·주문 현황·쿠폰 티켓 06): 주소·브라우저 저장소·[새로 전화하기].
// 통화 연결(WS·마이크)은 가짜 `PhoneCall`로 바꾸고, 조회 API는 가짜 fetch로 답한다.
import { act, render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { type CallState, initialCallState } from './callState.ts'
import type { OrderStatusJson } from './orderStatus.ts'

const calls = vi.hoisted(() => [] as { push: (s: unknown) => void; dialed: number }[])

vi.mock('./phoneCall.ts', async () => {
  const { initialCallState } = await import('./callState.ts')
  class FakeCall {
    private state: unknown = initialCallState
    private readonly listeners = new Set<() => void>()
    constructor() {
      calls.push(this)
    }
    getState = () => this.state
    subscribe = (fn: () => void) => {
      this.listeners.add(fn)
      return () => this.listeners.delete(fn)
    }
    push(state: unknown) {
      this.state = state
      for (const fn of this.listeners) fn()
    }
    dialed = 0
    attach() {}
    detach() {}
    dial() {
      this.dialed += 1
    }
    hangup() {}
  }
  return { PhoneCall: FakeCall, phoneOptions: () => ({ micFirst: false, agc: true }) }
})

const { default: PhonePage } = await import('./PhonePage.tsx')

const KEY = 'agenthan.phone.order.v1'

const order = (extra: Partial<OrderStatusJson> = {}): OrderStatusJson => ({
  order_id: 'call-abc',
  stage: 'checking',
  placed_at: '2026-11-21T16:00:00+09:00',
  lines: [{ menu_id: 'm01', name: '메도빅', quantity: 1, options: [], request: '', subtotal: 7500 }],
  amount: { subtotal: 7500, total: 7500 },
  pickup: { time: '2099-11-21T18:30:00+09:00', label: '18:30', original_time: '', original_label: '' },
  outcome: null,
  reason: null,
  detail: null,
  sold_out: [],
  can_cancel: false,
  ...extra,
})

/** 조회 API 가짜: 주문 번호 → 응답(없으면 404) */
function serve(orders: Record<string, OrderStatusJson>) {
  const asked: string[] = []
  vi.stubGlobal(
    'fetch',
    vi.fn(async (url: string) => {
      asked.push(url)
      const id = decodeURIComponent(url.split('/').pop() ?? '')
      const found = orders[id]
      return found
        ? new Response(JSON.stringify(found), { status: 200 })
        : new Response(JSON.stringify({ detail: '주문을 찾을 수 없어요' }), { status: 404 })
    }),
  )
  return asked
}

function open(path: string) {
  window.history.replaceState(null, '', path)
  const view = render(<PhonePage />)
  return { ...view, call: calls[calls.length - 1] }
}

/** 주문 현황 스트림(티켓 05) 가짜: 열리기만 하고 아무것도 보내지 않는다. 현황은 조회 API 가짜가 준다 */
class SilentEventSource {
  readyState = 0
  onerror: ((ev: Event) => unknown) | null = null
  addEventListener() {}
  close() {
    this.readyState = 2
  }
}

beforeEach(() => {
  calls.length = 0
  vi.stubGlobal('EventSource', SilentEventSource)
})

afterEach(() => {
  vi.unstubAllGlobals()
  vi.restoreAllMocks()
})

describe('주문 현황 주소', () => {
  it('통화가 주문으로 끝나면 주소가 주문 현황 주소로 바뀌고 주문 번호가 남는다', async () => {
    serve({ 'call-abc': order() })
    const { call } = open('/phone')
    expect(await screen.findByRole('button', { name: '전화 받기' })).toBeInTheDocument()

    const ended: CallState = {
      ...initialCallState,
      status: 'ended',
      callId: 'call-abc',
      order: {
        order: {
          id: 'call-abc',
          status: 'confirmed',
          lines: [],
          pickup_time: '2099-11-21T18:30:00+09:00',
          customer_name: null,
          customer_phone: null,
          transcript: [],
        },
        cart: [],
        total: 7500,
      },
    }
    act(() => call.push(ended))

    expect(await screen.findByText('가게에서 주문을 확인하고 있어요')).toBeInTheDocument()
    expect(window.location.pathname).toBe('/phone/orders/call-abc')
    expect(localStorage.getItem(KEY)).toBe('call-abc')
  })

  it('주문 현황 주소를 새로 열어도(새로 고침·새 탭) 같은 주문 현황이 보인다', async () => {
    const asked = serve({ 'call-abc': order({ stage: 'confirmed' }) })
    open('/phone/orders/call-abc')

    expect(await screen.findByText('주문이 확정됐어요')).toBeInTheDocument()
    expect(screen.getByText('주문 현황')).toBeInTheDocument()
    expect(asked).toEqual(['/api/phone/orders/call-abc'])
    expect(localStorage.getItem(KEY)).toBe('call-abc')
  })

  it('없는 주문은 "주문을 찾을 수 없어요"와 [새로 전화하기]를 보인다', async () => {
    serve({})
    open('/phone/orders/call-nope')

    expect(await screen.findByText('주문을 찾을 수 없어요')).toBeInTheDocument()
    expect(screen.getByRole('button', { name: '새로 전화하기' })).toBeInTheDocument()
  })

  it('[새로 전화하기]는 통화 화면으로 가고 그 뒤 /phone은 옛 주문으로 가지 않는다', async () => {
    serve({ 'call-abc': order() })
    const { unmount } = open('/phone/orders/call-abc')
    await screen.findByText('가게에서 주문을 확인하고 있어요')

    await userEvent.click(screen.getByRole('button', { name: '새로 전화하기' }))

    expect(screen.getByRole('button', { name: '전화 받기' })).toBeInTheDocument()
    expect(window.location.pathname).toBe('/phone')
    expect(localStorage.getItem(KEY)).toBeNull()
    unmount()
    open('/phone')
    expect(screen.getByRole('button', { name: '전화 받기' })).toBeInTheDocument()
  })

  it('취소된 주문의 [다시 주문하기]는 통화 화면으로 돌아가 새 통화를 건다(티켓 05)', async () => {
    serve({ 'call-abc': order({ stage: 'cancelled', outcome: 'expired' }) })
    open('/phone/orders/call-abc')

    await userEvent.click(await screen.findByRole('button', { name: '다시 주문하기' }))

    expect(window.location.pathname).toBe('/phone')
    expect(localStorage.getItem(KEY)).toBeNull()
    expect(calls).toHaveLength(2)
    expect(calls[1].dialed).toBe(1) // 새 통화에서 건다(옛 통화가 아니다)
    expect(screen.queryByRole('button', { name: '다시 주문하기' })).toBeNull()
  })
})

describe('/phone 다시 열기', () => {
  it('끝나지 않은 주문이 있으면 주문 현황으로 간다', async () => {
    localStorage.setItem(KEY, 'call-abc')
    serve({ 'call-abc': order() })
    open('/phone')

    expect(screen.getByText('주문 현황을 확인하고 있어요.')).toBeInTheDocument()
    expect(await screen.findByText('가게에서 주문을 확인하고 있어요')).toBeInTheDocument()
    expect(window.location.pathname).toBe('/phone/orders/call-abc')
  })

  it('이미 끝난 주문이나 없는 주문이면 통화 화면이 뜨고 남긴 번호를 지운다', async () => {
    const cases: Record<string, OrderStatusJson>[] = [{ 'call-abc': order({ stage: 'cancelled' }) }, {}]
    for (const orders of cases) {
      localStorage.setItem(KEY, 'call-abc')
      serve(orders)
      const { unmount } = open('/phone')

      expect(await screen.findByRole('button', { name: '전화 받기' })).toBeInTheDocument()
      expect(window.location.pathname).toBe('/phone')
      expect(localStorage.getItem(KEY)).toBeNull()
      unmount()
    }
  })

  it('브라우저 저장소를 못 써도(비공개 창) 화면이 깨지지 않고 주소만으로 돈다', async () => {
    const blocked = () => {
      throw new DOMException('막힘', 'SecurityError')
    }
    vi.spyOn(Storage.prototype, 'getItem').mockImplementation(blocked)
    vi.spyOn(Storage.prototype, 'setItem').mockImplementation(blocked)
    vi.spyOn(Storage.prototype, 'removeItem').mockImplementation(blocked)
    serve({ 'call-abc': order() })

    const { unmount } = open('/phone/orders/call-abc')
    expect(await screen.findByText('가게에서 주문을 확인하고 있어요')).toBeInTheDocument()
    await userEvent.click(screen.getByRole('button', { name: '새로 전화하기' }))
    expect(screen.getByRole('button', { name: '전화 받기' })).toBeInTheDocument()
    unmount()

    open('/phone')
    expect(screen.getByRole('button', { name: '전화 받기' })).toBeInTheDocument()
  })
})

describe('손님 취소 (티켓 07 × 06)', () => {
  it('주소로 연 주문 현황에서도 [취소하기]가 되고, 취소한 주문으로는 /phone이 다시 가지 않는다', async () => {
    // 쿠폰 주문(티켓 09): 취소한 뒤에도 할인 줄이 같은 금액으로 남는다
    const amount = {
      subtotal: 7500,
      discount: 1000,
      coupon_name: '전화 주문 1,000원 할인',
      coupon_label: '전화 주문 1,000원 할인 쿠폰',
      total: 6500,
    }
    let current = order({ amount, can_cancel: true })
    vi.stubGlobal(
      'fetch',
      vi.fn(async (url: string, init?: RequestInit) => {
        if (init?.method === 'POST' && url === '/api/phone/orders/call-abc/cancel') {
          current = order({ amount, stage: 'cancelled', outcome: 'customer_cancelled' })
        }
        return new Response(JSON.stringify(current), { status: 200 })
      }),
    )
    const { unmount } = open('/phone/orders/call-abc')

    await userEvent.click(await screen.findByRole('button', { name: '취소하기' }))
    await userEvent.click(screen.getByRole('button', { name: '네, 취소할게요' }))

    expect(await screen.findByText('주문을 취소했어요')).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: '취소하기' })).toBeNull()
    expect(screen.getByText('전화 주문 1,000원 할인 쿠폰')).toBeInTheDocument()
    expect(screen.getByText('6,500원')).toBeInTheDocument()
    unmount()

    open('/phone')
    expect(await screen.findByRole('button', { name: '전화 받기' })).toBeInTheDocument()
    expect(window.location.pathname).toBe('/phone')
    expect(localStorage.getItem(KEY)).toBeNull()
  })
})
