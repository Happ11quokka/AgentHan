// 주문 승인 티켓 06: 재료 소진 거절과 오늘 품절 (서버 `hub.py` `reject`·`undo_sold_out`, `owner.sold_out`).
// 메뉴 고르기·보낼 본문은 `rejectSheet.ts`, 오늘 품절 목록은 리듀서, 카드의 품절 표시는 사전(`ownerText.ts`).
import { afterEach, describe, expect, it, vi } from 'vitest'
import {
  type OrderCard,
  type OrderCardLine,
  type OwnerAction,
  type OwnerState,
  type SoldOutItem,
  initialOwnerState,
  orderOutcome,
  ownerReducer,
  stillSoldOut,
} from './ownerState.ts'
import { rejectOrder, undoSoldOut } from './ownerStream.ts'
import { OWNER_TEXT, soldOutMarkView } from './ownerText.ts'
import { rejectBody, stockOutChoices, togglePicked } from './rejectSheet.ts'

const REQUESTED = '2026-11-08T16:02:00+09:00'
const MEDOVIK: SoldOutItem = { menu_id: 'm01', name_ko: '메도빅', name_owner: 'Медовик' }
const NAPOLEON: SoldOutItem = { menu_id: 'm02', name_ko: '나폴레옹', name_owner: 'Наполеон' }

function run(...actions: OwnerAction[]): OwnerState {
  return actions.reduce(ownerReducer, initialOwnerState)
}

function line(menu: SoldOutItem, quantity = 1, options: string[] = []): OrderCardLine {
  return {
    menu_id: menu.menu_id,
    name_ko: menu.name_ko,
    name_owner: menu.name_owner,
    quantity,
    options,
    options_owner: options,
    request: '',
    request_owner: '',
    badges: [],
    subtotal: 7000 * quantity,
  }
}

function card(over: Partial<OrderCard> = {}): OrderCard {
  return {
    id: 'call-1',
    status: 'new',
    received_at: REQUESTED,
    pickup_time: '2026-11-08T18:30:00+09:00',
    pickup_label: '18:30',
    lines: [line(MEDOVIK), line(NAPOLEON)],
    total: 14500,
    total_label: '14,500₩',
    request: 1,
    stage: 'in_call',
    requested_at: REQUESTED,
    deadline_at: '2026-11-08T16:02:20+09:00',
    decided_at: '',
    delay_min: 0,
    auto_accepted: false,
    withdrawn: '',
    reject_reason: '',
    reject_menu_ids: [],
    reject_text: '',
    reject_text_ko: '',
    sold_out: [],
    ...over,
  }
}

afterEach(() => vi.unstubAllGlobals())

describe('out of stock menu picking (order approval ticket 06)', () => {
  it('offers each ordered menu once, in order', () => {
    const lines = [line(MEDOVIK), line(NAPOLEON), line(MEDOVIK, 1, ['샤워 크림 빼기'])]
    expect(stockOutChoices(lines).map((l) => l.menu_id)).toEqual(['m01', 'm02'])
  })

  it('picks several menus and keeps the order of the lines', () => {
    const lines = [line(MEDOVIK), line(NAPOLEON)]
    let picked = togglePicked([], 'm02', lines)
    picked = togglePicked(picked, 'm01', lines)
    expect(picked).toEqual(['m01', 'm02'])
    expect(togglePicked(picked, 'm02', lines)).toEqual(['m01'])
  })

  it('sends out of stock only with at least one picked menu', () => {
    expect(rejectBody('out_of_stock', '', [])).toBeNull()
    expect(rejectBody('out_of_stock', 'ignored', ['m01', 'm02'])).toEqual({
      reason: 'out_of_stock',
      menu_ids: ['m01', 'm02'],
    })
    expect(rejectBody('busy', '', ['m01'])).toEqual({ reason: 'busy' }) // 다른 사유면 메뉴를 안 보낸다
  })

  it('posts the picked menus and the undo to the server paths', async () => {
    const urls: string[] = []
    const bodies: unknown[] = []
    vi.stubGlobal('fetch', async (url: string, init: RequestInit) => {
      urls.push(url)
      bodies.push(init.body === undefined ? undefined : JSON.parse(String(init.body)))
      return new Response('{}', { status: urls.length === 3 ? 404 : 200 })
    })
    await expect(rejectOrder('k', 'call-1', { reason: 'out_of_stock', menu_ids: ['m01'] })).resolves.toBe(true)
    await expect(undoSoldOut('k', 'm01')).resolves.toBe(true)
    await expect(undoSoldOut('k', 'm99')).resolves.toBe(false)
    expect(urls.slice(0, 2)).toEqual(['/api/owner/orders/call-1/reject?key=k', '/api/owner/sold-out/m01/undo?key=k'])
    expect(bodies[0]).toEqual({ reason: 'out_of_stock', menu_ids: ['m01'] })
  })
})

describe('today sold out list (order approval ticket 06)', () => {
  it('comes with the snapshot and is replaced by each sold out event', () => {
    let s = run({
      type: 'snapshot',
      data: { orders: [], calls: [], sold_out: { date: '2026-11-08', items: [MEDOVIK] } },
    })
    expect(s.soldOut).toEqual([MEDOVIK])
    expect(stillSoldOut(s, 'm01')).toBe(true)
    s = ownerReducer(s, { type: 'owner.sold_out', data: { date: '2026-11-08', items: [MEDOVIK, NAPOLEON] } })
    expect(s.soldOut.map((i) => i.menu_id)).toEqual(['m01', 'm02'])
    s = ownerReducer(s, { type: 'owner.sold_out', data: { date: '2026-11-09', items: [] } }) // 자정
    expect(stillSoldOut(s, 'm01')).toBe(false)
  })

  it('is empty with a server that does not send it yet', () => {
    expect(run({ type: 'snapshot', data: { orders: [], calls: [] } }).soldOut).toEqual([])
  })
})

describe('sold out mark on the card (order approval ticket 06)', () => {
  const rejected = card({
    status: 'rejected',
    reject_reason: 'out_of_stock',
    reject_menu_ids: ['m01'],
    sold_out: [MEDOVIK],
  })

  it('says the menu was marked sold out and offers undo while it still is', () => {
    expect(soldOutMarkView(rejected, [MEDOVIK], 'ru')).toEqual({
      label: OWNER_TEXT.ru.soldOutMarked,
      items: [{ menuId: 'm01', name: 'Медовик', still: true }],
    })
    expect(soldOutMarkView(rejected, [], 'ko')).toEqual({
      label: '품절로 바꿨어요',
      items: [{ menuId: 'm01', name: '메도빅', still: false }], // 되돌렸다 → 버튼 없음
    })
  })

  it('shows nothing on a card that marked nothing', () => {
    expect(soldOutMarkView(card(), [MEDOVIK], 'ko')).toBeNull()
  })

  it('keeps the mark when the customer drops the menu and the card turns accepted', () => {
    const accepted = card({
      status: 'accepted',
      lines: [line(NAPOLEON)],
      decided_at: '2026-11-08T16:02:30+09:00',
      sold_out: [MEDOVIK],
    })
    const s = run({ type: 'snapshot', data: { orders: [rejected], calls: [] } }, { type: 'order.update', data: accepted })
    expect(orderOutcome(s.orders[0])).toBe('accepted')
    expect(soldOutMarkView(s.orders[0], [MEDOVIK], 'ko')?.items.map((i) => i.menuId)).toEqual(['m01'])
  })

  it('has every sold out label in both dictionaries (ru is a draft for a human check)', () => {
    for (const lang of ['ru', 'ko'] as const) {
      const text = OWNER_TEXT[lang]
      expect(text.stockOutPick && text.soldOutMarked && text.soldOutUndo && text.soldOutUndone).toBeTruthy()
    }
  })
})
