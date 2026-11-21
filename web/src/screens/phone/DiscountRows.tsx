import { type Discount, discountRows } from './discount.ts'

/** 쿠폰 주문의 "할인 전"·쿠폰 줄 (쿠폰 티켓 09). 합계(낼 금액) 바로 위에 둔다. 할인이 없으면 없음 */
export function DiscountRows({ discount }: { discount: Discount | null }) {
  if (!discount) return null
  return (
    <>
      {discountRows(discount).map(([label, amount]) => (
        <p key={label} className="phone-discount">
          <span>{label}</span>
          <span>{amount}</span>
        </p>
      ))}
    </>
  )
}
