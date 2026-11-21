import { DiscountRows } from './DiscountRows.tsx'
import type { Discount } from './discount.ts'
import { won } from './format.ts'

/** 통화 화면 주문서와 주문 현황이 같이 보이는 줄 하나 */
export interface OrderLineView {
  menu_id: string
  name: string
  quantity: number
  options: string[]
  /** 손님 요청 사항. 없으면 '' 또는 null */
  request: string | null
  subtotal: number
  /** 재료 소진 거절의 떨어진 메뉴 (주문 현황만) */
  soldOut?: boolean
}

/**
 * 줄 열쇠. 같은 메뉴라도 옵션이나 요청 사항이 다르면 다른 줄이다(서버 `Cart.same_kind`). 비서 메뉴는 옵션 말을
 * 요청 사항으로 적어 "아이스 아메리카노 하나, 따뜻한 아메리카노 하나"가 같은 메뉴 두 줄이 된다 (PR #44 리뷰)
 */
function lineKey(line: Pick<OrderLineView, 'menu_id' | 'options' | 'request'>): string {
  return [line.menu_id, line.options.join(','), line.request ?? ''].join('|')
}

/** 주문 줄 · (쿠폰이면) 할인 전·쿠폰 줄 · 합계(낼 금액). 통화 화면 주문서와 주문 현황이 같이 쓴다 */
export function OrderLines({
  lines,
  discount,
  total,
  className,
}: {
  lines: OrderLineView[]
  discount: Discount | null
  total: number
  className?: string
}) {
  return (
    <>
      <ul className={className}>
        {lines.map((line) => (
          <li key={lineKey(line)}>
            <span>
              {line.name} × {line.quantity}
              {line.options.length > 0 && <small> ({line.options.join(', ')})</small>}
              {line.request && <small> · {line.request}</small>}
              {line.soldOut && <small className="phone-sold-out"> · 재료 소진</small>}
            </span>
            <span>{won(line.subtotal)}</span>
          </li>
        ))}
      </ul>
      <DiscountRows discount={discount} />
      <p className="phone-total">
        <span>합계</span>
        <strong>{won(total)}</strong>
      </p>
    </>
  )
}
