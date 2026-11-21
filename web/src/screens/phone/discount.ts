/**
 * 쿠폰 할인 (쿠폰 티켓 09). 서버 금액 칸(통화 `state`·`order` 이벤트, 주문 현황 `amount`)에서 할인을
 * 읽는다. 금액은 서버의 쿠폰 계산 결과 그대로다: `total`이 낼 금액이고 화면은 다시 셈하지 않는다.
 * 쿠폰 줄 이름(`coupon_label`, "… 쿠폰")도 서버가 통화가 부르는 이름 그대로 준다(PR #44 리뷰).
 * 쿠폰을 쓰지 않은 주문은 할인 칸이 없다(화면도 쿠폰 전과 같다).
 */
import { won } from './format.ts'

export interface Discount {
  /** 할인 전 금액 */
  subtotal: number
  discount: number
  /** 화면의 쿠폰 줄 이름 ("전화 주문 1,000원 할인 쿠폰") */
  coupon_label: string
}

/** 서버가 보낸 금액 칸들 (할인 칸은 쿠폰을 쓴 주문만) */
export interface AmountFields {
  subtotal?: number
  discount?: number
  coupon_name?: string
  coupon_label?: string
}

/** 할인 칸이 있으면 할인, 없거나 0이면 null. 쿠폰 줄 이름이 없는 낡은 서버면 쿠폰 이름 그대로 */
export function discountOf(amount: AmountFields | null | undefined): Discount | null {
  if (!amount?.discount || amount.subtotal === undefined) return null
  return {
    subtotal: amount.subtotal,
    discount: amount.discount,
    coupon_label: amount.coupon_label || amount.coupon_name || '할인 쿠폰',
  }
}

/** 합계 위에 붙이는 두 줄: ["할인 전", "12,000원"], ["전화 주문 1,000원 할인 쿠폰", "−1,000원"] */
export function discountRows(discount: Discount): [string, string][] {
  return [
    ['할인 전', won(discount.subtotal)],
    [discount.coupon_label, `−${won(discount.discount)}`],
  ]
}
