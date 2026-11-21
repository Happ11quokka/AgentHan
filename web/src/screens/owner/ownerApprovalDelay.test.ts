// 주문 승인 티켓 04: 늦춘 수락(+15분·+30분) 카드 (서버 `hub.py` `accept(delay_min)`).
import { afterEach, describe, expect, it, vi } from 'vitest'
import { pickupBand } from './pickupBand.ts'
import {
  type OrderCard,
  approvalsWaiting,
  canAccept,
  effectivePickup,
  initialOwnerState,
  orderOutcome,
  ownerReducer,
} from './ownerState.ts'
import { ACCEPT_DELAYS, acceptOrder } from './ownerStream.ts'
import { OWNER_TEXT } from './ownerText.ts'

const REQUESTED = '2026-11-08T16:02:00+09:00'

function card(over: Partial<OrderCard> = {}): OrderCard {
  return {
    id: 'call-1',
    status: 'new',
    received_at: REQUESTED,
    pickup_time: '2026-11-08T18:30:00+09:00',
    pickup_label: '18:30',
    lines: [],
    total: 7500,
    total_label: '7,500₩',
    request: 1,
    stage: 'in_call',
    requested_at: REQUESTED,
    deadline_at: '2026-11-08T16:02:20+09:00',
    decided_at: '',
    delay_min: 0,
    owner_pickup_time: '',
    owner_pickup_label: '',
    delay_reply: '',
    auto_accepted: false,
    withdrawn: '',
    ...over,
  }
}

const delayed = (over: Partial<OrderCard> = {}) =>
  card({
    status: 'accepted',
    delay_min: 15,
    decided_at: '2026-11-08T16:02:09+09:00',
    owner_pickup_time: '2026-11-08T18:45:00+09:00',
    owner_pickup_label: '18:45',
    ...over,
  })

afterEach(() => {
  vi.unstubAllGlobals()
})

describe('delayed accept (order approval ticket 04)', () => {
  it('offers +15 and +30 next to accept', () => {
    expect(ACCEPT_DELAYS).toEqual([0, 15, 30])
    expect(OWNER_TEXT.ko.acceptLater(15)).toBe('+15분')
    expect(OWNER_TEXT.ru.acceptLater(30)).toBe('+30 мин')
  })

  it('posts the delay in the body, and nothing for a plain accept', async () => {
    const bodies: (BodyInit | null | undefined)[] = []
    vi.stubGlobal('fetch', async (_url: string, init: RequestInit) => {
      bodies.push(init.body)
      return new Response('{}', { status: 200 })
    })
    await acceptOrder('k', 'call-1', 30)
    await acceptOrder('k', 'call-1')
    expect(bodies).toEqual([JSON.stringify({ delay_min: 30 }), undefined])
  })

  it('shows the new time once accepted later and stops waiting', () => {
    const s = [card(), delayed({ delay_reply: 'asking' })].reduce(
      (state, data) => ownerReducer(state, { type: 'order.update', data }),
      initialOwnerState,
    )
    const [shown] = s.orders
    expect(orderOutcome(shown)).toBe('delayed')
    expect(canAccept(shown)).toBe(false)
    expect(approvalsWaiting(s)).toBe(false)
    expect(OWNER_TEXT.ko.acceptedLater(shown.owner_pickup_label ?? '', shown.delay_min ?? 0)).toBe(
      '18:45로 수락 (+15분)',
    )
  })

  it('uses the new pickup time for the card head and its colour', () => {
    expect(effectivePickup(delayed())).toEqual({
      pickup_time: '2026-11-08T18:45:00+09:00',
      pickup_label: '18:45',
    })
    expect(effectivePickup(card()).pickup_label).toBe('18:30')
    const at1835 = Date.parse('2026-11-08T18:35:00+09:00')
    expect(pickupBand(card({ status: 'accepted' }), at1835)).toBe('late')
    expect(pickupBand(effectivePickup(delayed()), at1835)).not.toBe('late')
  })

  it('greys out a card whose new time the customer turned down', () => {
    const declined = delayed({ status: 'cancelled', delay_reply: 'no' })
    expect(orderOutcome(declined)).toBe('declined')
    expect(effectivePickup(declined).pickup_label).toBe('18:30') // 확정되지 않았다
    expect(OWNER_TEXT.ko.delayDeclined('18:45')).toBe('손님이 18:45를 거절 · 주문 취소')
  })

  it('names the delayed-accept text sent to the customer', () => {
    expect(OWNER_TEXT.ko.smsKind.delayed).toBe('늦춘 시각 확정 안내')
  })
})
