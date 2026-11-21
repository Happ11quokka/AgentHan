import { describe, expect, it } from 'vitest'
import { discountOf, discountRows } from './discount.ts'

describe('coupon discount on the phone screens (coupon ticket 09)', () => {
  it('reads the discount fields the server sends only for coupon orders', () => {
    const amount = {
      subtotal: 12000,
      discount: 1000,
      coupon_name: '전화 주문 1,000원 할인',
      coupon_label: '전화 주문 1,000원 할인 쿠폰',
    }
    expect(discountOf(amount)).toEqual({ subtotal: 12000, discount: 1000, coupon_label: '전화 주문 1,000원 할인 쿠폰' })
    expect(discountOf({ subtotal: 7500 })).toBeNull() // 쿠폰 없는 주문 현황
    expect(discountOf({})).toBeNull() // 쿠폰 없는 통화 이벤트
    expect(discountOf(null)).toBeNull()
  })

  it("uses the server's coupon line name (no copy of the call's wording here)", () => {
    expect(discountOf({ subtotal: 9000, discount: 900, coupon_name: '단골', coupon_label: '단골 쿠폰' })?.coupon_label).toBe(
      '단골 쿠폰',
    )
    // 쿠폰 줄 이름을 보내지 않던 서버
    expect(discountOf({ subtotal: 9000, discount: 900, coupon_name: '단골 쿠폰' })?.coupon_label).toBe('단골 쿠폰')
  })

  it('shows the amount before the discount and the coupon line', () => {
    expect(discountRows({ subtotal: 12000, discount: 1000, coupon_label: '전화 주문 1,000원 할인 쿠폰' })).toEqual([
      ['할인 전', '12,000원'],
      ['전화 주문 1,000원 할인 쿠폰', '−1,000원'],
    ])
  })
})
