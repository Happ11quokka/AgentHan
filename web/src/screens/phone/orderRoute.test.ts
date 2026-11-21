// 주문 현황 다시 열기 (전화 메뉴·주문 현황·쿠폰 티켓 06): 주소 ↔ 주문 번호, 브라우저 저장소, `/phone`을 다시 열
// 때 어느 화면으로 가는지.
import { describe, expect, it } from 'vitest'
import type { OrderStatusJson } from './orderStatus.ts'
import {
  browserOrderMemory,
  initialPhoneView,
  isPhonePath,
  isUnfinished,
  orderIdFromPath,
  orderPath,
  resumedView,
} from './orderRoute.ts'

const NOW = new Date('2026-11-21T17:00:00+09:00')

const order = (extra: Partial<OrderStatusJson> = {}): OrderStatusJson => ({
  order_id: 'call-abc',
  stage: 'checking',
  placed_at: '2026-11-21T16:00:00+09:00',
  lines: [],
  amount: { subtotal: 7500, total: 7500 },
  pickup: { time: '2026-11-21T18:30:00+09:00', label: '18:30', original_time: '', original_label: '' },
  ...extra,
})

function fakeStorage(): Storage {
  const items = new Map<string, string>()
  return {
    get length() {
      return items.size
    },
    clear: () => items.clear(),
    getItem: (k) => items.get(k) ?? null,
    key: (i) => [...items.keys()][i] ?? null,
    removeItem: (k) => void items.delete(k),
    setItem: (k, v) => void items.set(k, v),
  }
}

describe('주문 현황 주소', () => {
  it('주문 번호를 주소에 넣고 다시 꺼낸다', () => {
    expect(orderPath('call-abc')).toBe('/phone/orders/call-abc')
    expect(orderIdFromPath('/phone/orders/call-abc')).toBe('call-abc')
    expect(orderIdFromPath('/phone/orders/call-abc/')).toBe('call-abc')
    expect(orderIdFromPath(orderPath('a b/c'))).toBe('a b/c')
  })

  it('주문 현황 주소가 아니면 주문 번호가 없다', () => {
    for (const path of ['/phone', '/phone/orders', '/phone/orders/', '/owner', '/phone/orders/a/b', '/phone/orders/%E0']) {
      expect(orderIdFromPath(path)).toBeNull()
    }
  })

  it('손님 전화 화면 주소는 /phone과 주문 현황 주소다', () => {
    expect(isPhonePath('/phone')).toBe(true)
    expect(isPhonePath('/phone/')).toBe(true)
    expect(isPhonePath('/phone/orders/call-abc')).toBe(true)
    expect(isPhonePath('/owner')).toBe(false)
    expect(isPhonePath('/phone/other')).toBe(false)
  })
})

describe('브라우저 저장소', () => {
  it('마지막 주문 번호를 남기고 지운다', () => {
    const storage = fakeStorage()
    const memory = browserOrderMemory(() => storage)
    expect(memory.recall()).toBeNull()
    memory.remember('call-abc')
    expect(browserOrderMemory(() => storage).recall()).toBe('call-abc') // 새로 연 화면도 읽는다
    memory.forget()
    expect(memory.recall()).toBeNull()
  })

  it('저장소를 못 쓰는 브라우저(비공개 창)에서도 던지지 않는다', () => {
    const memory = browserOrderMemory(() => {
      throw new DOMException('막힘', 'SecurityError')
    })
    expect(() => memory.remember('call-abc')).not.toThrow()
    expect(memory.recall()).toBeNull()
    expect(() => memory.forget()).not.toThrow()
  })
})

describe('끝나지 않은 주문', () => {
  it('가게 확인 중이거나 확정됐고 픽업 시각 전이면 끝나지 않았다', () => {
    expect(isUnfinished(order(), NOW)).toBe(true)
    expect(isUnfinished(order({ stage: 'confirmed' }), NOW)).toBe(true)
    expect(isUnfinished(order({ stage: 'confirmed' }), new Date('2026-11-21T18:30:00+09:00'))).toBe(false)
    expect(isUnfinished(order({ stage: 'cancelled' }), NOW)).toBe(false)
  })
})

describe('처음 열 때의 화면', () => {
  it('주문 현황 주소면 그 주문을 보인다(저장소와 상관없이)', () => {
    expect(initialPhoneView('/phone/orders/call-abc', null)).toEqual({ kind: 'order', orderId: 'call-abc' })
    expect(initialPhoneView('/phone/orders/call-abc', 'call-old')).toEqual({ kind: 'order', orderId: 'call-abc' })
  })

  it('/phone은 남겨 둔 주문이 있으면 확인부터, 없으면 통화 화면', () => {
    expect(initialPhoneView('/phone', 'call-abc')).toEqual({ kind: 'resuming', orderId: 'call-abc' })
    expect(initialPhoneView('/phone', null)).toEqual({ kind: 'call' })
  })

  it('끝나지 않은 주문이면 주문 현황으로, 끝났거나 없으면 통화 화면으로 가고 저장소를 비운다', () => {
    expect(resumedView('call-abc', { type: 'loaded', order: order() }, NOW)).toEqual({
      view: { kind: 'order', orderId: 'call-abc' },
      forget: false,
    })
    expect(resumedView('call-abc', { type: 'loaded', order: order({ stage: 'cancelled' }) }, NOW)).toEqual({
      view: { kind: 'call' },
      forget: true,
    })
    expect(resumedView('call-abc', { type: 'missing' }, NOW)).toEqual({ view: { kind: 'call' }, forget: true })
  })

  it('읽지 못했으면(회선) 통화 화면으로 가되 주문 번호는 남겨 둔다', () => {
    expect(resumedView('call-abc', { type: 'failed' }, NOW)).toEqual({ view: { kind: 'call' }, forget: false })
  })
})
