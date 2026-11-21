import { describe, expect, it } from 'vitest'
import { discountOf, discountRows } from './discount.ts'

describe('coupon discount on the phone screens (coupon ticket 09)', () => {
  it('reads the discount fields the server sends only for coupon orders', () => {
    expect(discountOf({ subtotal: 12000, discount: 1000, coupon_name: '전화 주문 1,000원 할인' })).toEqual({
      subtotal: 12000,
      discount: 1000,
      coupon_name: '전화 주문 1,000원 할인',
    })
    expect(discountOf({ subtotal: 7500 })).toBeNull() // 쿠폰 없는 주문 현황
    expect(discountOf({})).toBeNull() // 쿠폰 없는 통화 이벤트
    expect(discountOf(null)).toBeNull()
  })

  it('shows the amount before the discount and the coupon line', () => {
    expect(discountRows({ subtotal: 12000, discount: 1000, coupon_name: '전화 주문 1,000원 할인' })).toEqual([
      ['할인 전', '12,000원'],
      ['전화 주문 1,000원 할인 쿠폰', '−1,000원'],
    ])
    expect(discountRows({ subtotal: 9000, discount: 900, coupon_name: '단골 쿠폰' })[1][0]).toBe('단골 쿠폰')
  })
})
