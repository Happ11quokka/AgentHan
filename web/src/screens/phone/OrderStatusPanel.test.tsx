// 주문 현황 화면 (전화 메뉴·주문 현황·쿠폰 티켓 05): 화면을 열어 둔 채 사장님이 결정하면 스스로 바뀌고, 끊겼다
// 다시 이어지면 그 사이의 결정을 조회로 읽는다. 조회 API와 스트림은 가짜로 바꿔 끼운다.
import { act, render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { describe, expect, it, vi } from 'vitest'
import type { OrderStatusJson } from './orderStatus.ts'
import OrderStatusPanel from './OrderStatusPanel.tsx'
import { ORDER_STREAM_RETRY_MS } from './orderStream.ts'

class FakeSource {
  readyState = 1
  closed = false
  onerror: (() => void) | null = null
  private readonly listeners = new Map<string, (e: { data: string }) => void>()
  addEventListener(name: string, fn: (e: { data: string }) => void) {
    this.listeners.set(name, fn)
  }
  emit(name: string, data: unknown) {
    act(() => this.listeners.get(name)?.({ data: JSON.stringify(data) }))
  }
  close() {
    this.closed = true
    this.readyState = 2
  }
}

const status = (extra: Partial<OrderStatusJson> = {}): OrderStatusJson => ({
  order_id: 'call-1',
  stage: 'checking',
  placed_at: '2026-11-21T16:00:00+09:00',
  lines: [{ menu_id: 'm01', name: '메도빅', quantity: 1, options: [], request: '', subtotal: 7500 }],
  amount: { subtotal: 7500, total: 7500 },
  pickup: { time: '2026-11-21T18:30:00+09:00', label: '18:30', original_time: '', original_label: '' },
  outcome: null,
  reason: null,
  detail: null,
  sold_out: [],
  can_cancel: false,
  ...extra,
})

const STOCK_OUT = '죄송해요, 메도빅은 지금 재료가 떨어져 주문이 취소됐어요. 다른 메뉴로 다시 주문해 주세요.'
const EXPIRED = '가게에서 확인하지 못해 주문이 취소됐어요. 다시 전화 주세요.'

function show(first: OrderStatusJson, ...later: OrderStatusJson[]) {
  return showWith({}, first, ...later)
}

/** `post`: 손님 취소 API 가짜 (티켓 07) */
function showWith(
  { post }: { post?: (url: string, init?: RequestInit) => Promise<Response> },
  first: OrderStatusJson,
  ...later: OrderStatusJson[]
) {
  const reads = [first, ...later]
  const get = vi.fn(() => Promise.resolve(Response.json(reads.length > 1 ? reads.shift() : reads[0])))
  const sources: FakeSource[] = []
  const openSource = () => {
    const source = new FakeSource()
    sources.push(source)
    return source
  }
  const onReorder = vi.fn()
  render(
    <OrderStatusPanel orderId="call-1" onReorder={onReorder} get={get} openSource={openSource} post={post} />,
  )
  return { get, sources, onReorder, user: userEvent.setup() }
}

describe('order status screen', () => {
  it('changes by itself when the owner accepts with a later pickup', async () => {
    const { sources } = show(status())
    expect(await screen.findByText('가게에서 주문을 확인하고 있어요')).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: '새로 고침' })).not.toBeInTheDocument()

    sources[0].emit(
      'order.status',
      status({
        stage: 'confirmed',
        outcome: 'delayed',
        detail: '픽업 시각이 18:30에서 18:45로 바뀌었어요. 18:45에 준비돼요. 어려우시면 전화 주세요.',
        pickup: {
          time: '2026-11-21T18:45:00+09:00',
          label: '18:45',
          original_time: '2026-11-21T18:30:00+09:00',
          original_label: '18:30',
        },
      }),
    )

    expect(screen.getByRole('heading', { name: '주문이 확정됐어요' })).toBeInTheDocument()
    expect(screen.getByText(/18:30에서 18:45로 바뀌었어요/)).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: '다시 주문하기' })).not.toBeInTheDocument()
    expect(sources[0].closed).toBe(true) // 결정이 나면 더 듣지 않는다
  })

  it('shows the out-of-stock reject with the menu, and [다시 주문하기] goes back to the call', async () => {
    const { sources, onReorder, user } = show(status())
    await screen.findByText('가게에서 주문을 확인하고 있어요')
    sources[0].emit(
      'order.status',
      status({
        stage: 'cancelled',
        outcome: 'out_of_stock',
        reason: { code: 'out_of_stock', text: STOCK_OUT },
        detail: STOCK_OUT,
        sold_out: ['메도빅'],
      }),
    )

    expect(screen.getByRole('heading', { name: '주문이 취소됐어요' })).toBeInTheDocument()
    expect(screen.getByText(/메도빅은 지금 재료가 떨어져/)).toBeInTheDocument()
    expect(screen.getByText(/재료 소진/)).toBeInTheDocument()
    await user.click(screen.getByRole('button', { name: '다시 주문하기' }))
    expect(onReorder).toHaveBeenCalledOnce()
  })

  it('reads the order again when the stream drops, so a decision made meanwhile shows', async () => {
    const { get, sources } = show(status(), status({ stage: 'cancelled', outcome: 'expired', detail: EXPIRED }))
    await screen.findByText('가게에서 주문을 확인하고 있어요')
    expect(get).toHaveBeenCalledTimes(1)

    act(() => sources[0].onerror?.())

    expect(await screen.findByText(/가게에서 확인하지 못해 주문이 취소됐어요/)).toBeInTheDocument()
    expect(get).toHaveBeenCalledTimes(2)
    await waitFor(() => expect(sources[0].closed).toBe(true))
  })
})

describe('order lines and refused streams (PR #44 리뷰)', () => {
  it('shows the same menu twice when the requests differ, without duplicate keys', async () => {
    const errors = vi.spyOn(console, 'error').mockImplementation(() => {})
    try {
      const americano = { menu_id: 'm03', name: '아메리카노', quantity: 1, options: [], subtotal: 4500 }
      show(
        status({
          lines: [
            { ...americano, request: '아이스' },
            { ...americano, request: '따뜻하게' },
          ],
          amount: { subtotal: 9000, total: 9000 },
        }),
      )
      expect(await screen.findByText(/· 아이스/)).toBeInTheDocument()
      expect(screen.getByText(/· 따뜻하게/)).toBeInTheDocument()
      expect(errors.mock.calls.flat().join(' ')).not.toMatch(/same key/)
    } finally {
      errors.mockRestore()
    }
  })

  it('an order only in the record (stream refused, 404) still reads itself again until it is decided', async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true })
    try {
      const { get, sources } = show(status(), status(), status({ stage: 'cancelled', outcome: 'expired', detail: EXPIRED }))
      await screen.findByText('가게에서 주문을 확인하고 있어요')
      const refuse = (source: FakeSource) =>
        act(() => {
          source.readyState = 2 // 허브에 카드가 없다: 브라우저가 다시 붙지 않는다
          source.onerror?.()
        })
      refuse(sources[0])
      await waitFor(() => expect(get).toHaveBeenCalledTimes(2))
      act(() => vi.advanceTimersByTime(ORDER_STREAM_RETRY_MS))
      expect(sources).toHaveLength(2)
      refuse(sources[1])
      expect(await screen.findByText(/가게에서 확인하지 못해 주문이 취소됐어요/)).toBeInTheDocument()
      expect(get).toHaveBeenCalledTimes(3)
    } finally {
      vi.useRealTimers()
    }
  })
})

// 손님 취소 (티켓 07): [취소하기]는 화면 안에서 한 번 묻고(브라우저 대화상자 없음), 거부되면 이긴 결과를 보인다
describe('customer cancel', () => {
  const cancelled = status({ stage: 'cancelled', outcome: 'customer_cancelled', can_cancel: false })

  it('a checking order asks once on the page, then shows "주문을 취소했어요"', async () => {
    const confirm = vi.spyOn(window, 'confirm')
    const post = vi.fn(() => Promise.resolve(Response.json(cancelled)))
    const { user } = showWith({ post }, status({ can_cancel: true }))
    await screen.findByText('가게에서 주문을 확인하고 있어요')

    await user.click(screen.getByRole('button', { name: '취소하기' }))
    expect(post).not.toHaveBeenCalled() // 먼저 한 번 묻는다
    expect(screen.getByText('주문을 취소할까요?')).toBeInTheDocument()
    await user.click(screen.getByRole('button', { name: '아니요' }))
    expect(screen.queryByText('주문을 취소할까요?')).not.toBeInTheDocument()

    await user.click(screen.getByRole('button', { name: '취소하기' }))
    await user.click(screen.getByRole('button', { name: '네, 취소할게요' }))

    expect(await screen.findByRole('heading', { name: '주문을 취소했어요' })).toBeInTheDocument()
    expect(post).toHaveBeenCalledExactlyOnceWith('/api/phone/orders/call-1/cancel', { method: 'POST' })
    expect(screen.queryByRole('button', { name: '취소하기' })).not.toBeInTheDocument()
    expect(screen.getByRole('button', { name: '다시 주문하기' })).toBeInTheDocument()
    expect(confirm).not.toHaveBeenCalled()
  })

  it('a later pickup can be cancelled the same way', async () => {
    const post = vi.fn(() => Promise.resolve(Response.json(cancelled)))
    const delayed = status({
      stage: 'confirmed',
      outcome: 'delayed',
      can_cancel: true,
      pickup: {
        time: '2026-11-21T18:45:00+09:00',
        label: '18:45',
        original_time: '2026-11-21T18:30:00+09:00',
        original_label: '18:30',
      },
    })
    const { user } = showWith({ post }, delayed)
    await screen.findByRole('heading', { name: '주문이 확정됐어요' })

    await user.click(screen.getByRole('button', { name: '취소하기' }))
    await user.click(screen.getByRole('button', { name: '네, 취소할게요' }))

    expect(await screen.findByRole('heading', { name: '주문을 취소했어요' })).toBeInTheDocument()
  })

  it('an order accepted on time has no button, only "call the store"', async () => {
    show(status({ stage: 'confirmed', outcome: 'accepted', can_cancel: false }))
    await screen.findByRole('heading', { name: '주문이 확정됐어요' })

    expect(screen.queryByRole('button', { name: '취소하기' })).not.toBeInTheDocument()
    expect(screen.getByText('취소하려면 가게로 전화 주세요.')).toBeInTheDocument()
  })

  it('when the owner accepted first, the refused cancel shows the confirmed order and why', async () => {
    const message = '가게가 이미 주문을 확정했어요. 취소하려면 가게로 전화 주세요.'
    const accepted = status({ stage: 'confirmed', outcome: 'accepted', can_cancel: false })
    const post = vi.fn(() =>
      Promise.resolve(Response.json({ detail: { code: 'accepted', message, order: accepted } }, { status: 409 })),
    )
    const { user } = showWith({ post }, status({ can_cancel: true }))
    await screen.findByText('가게에서 주문을 확인하고 있어요')

    await user.click(screen.getByRole('button', { name: '취소하기' }))
    await user.click(screen.getByRole('button', { name: '네, 취소할게요' }))

    expect(await screen.findByRole('heading', { name: '주문이 확정됐어요' })).toBeInTheDocument()
    expect(screen.getByRole('alert')).toHaveTextContent(message)
    expect(screen.queryByRole('button', { name: '취소하기' })).not.toBeInTheDocument()
  })

  it('a cancel that could not reach the server can be tried again', async () => {
    const post = vi
      .fn<(url: string, init?: RequestInit) => Promise<Response>>()
      .mockRejectedValueOnce(new TypeError('offline'))
      .mockResolvedValueOnce(Response.json(cancelled))
    const { user } = showWith({ post }, status({ can_cancel: true }))
    await screen.findByText('가게에서 주문을 확인하고 있어요')

    await user.click(screen.getByRole('button', { name: '취소하기' }))
    await user.click(screen.getByRole('button', { name: '네, 취소할게요' }))
    expect(await screen.findByRole('alert')).toHaveTextContent('취소하지 못했어요')
    expect(screen.getByRole('heading', { name: '가게에서 주문을 확인하고 있어요' })).toBeInTheDocument()

    await user.click(screen.getByRole('button', { name: '취소하기' }))
    await user.click(screen.getByRole('button', { name: '네, 취소할게요' }))
    expect(await screen.findByRole('heading', { name: '주문을 취소했어요' })).toBeInTheDocument()
  })
})
