// 손님 주문 현황 (전화 메뉴·주문 현황·쿠폰 티켓 04). 통화가 확정 주문으로 끝나면 통화 화면 대신 보인다.
// 상태·문구는 순수 모듈 `orderStatus.ts`가 정한다. 이 티켓에서는 열 때 한 번 읽고 [새로 고침]으로 다시 읽는다.
import { useEffect, useReducer } from 'react'
import { fetchOrderStatus, initialOrderStatus, orderHeadline, orderStatusReducer, orderSteps } from './orderStatus.ts'

const won = (n: number) => `${n.toLocaleString('ko-KR')}원`

export default function OrderStatusPanel({ orderId }: { orderId: string }) {
  const [state, dispatch] = useReducer(orderStatusReducer, orderId, initialOrderStatus)
  // 다시 읽기 요청마다 늘린다. 효과가 이 값을 보고 조회를 다시 부른다
  const [round, reload] = useReducer((n: number) => n + 1, 0)

  useEffect(() => {
    let alive = true
    dispatch({ type: 'load' })
    void fetchOrderStatus(orderId).then((action) => {
      if (alive) dispatch(action)
    })
    return () => {
      alive = false
    }
  }, [orderId, round])

  const order = state.order
  const refresh = (
    <button type="button" className="phone-btn" disabled={state.phase === 'loading'} onClick={reload}>
      새로 고침
    </button>
  )

  if (state.phase === 'missing') {
    return (
      <section className="phone-status-card" aria-label="주문 현황" role="status">
        <h2>주문을 찾을 수 없어요</h2>
        <p className="phone-status-detail">다시 걸어서 주문해 주세요.</p>
      </section>
    )
  }
  if (!order) {
    return (
      <section className="phone-status-card" aria-label="주문 현황" role="status">
        {state.phase === 'failed' ? (
          <>
            <h2>주문 현황을 불러오지 못했어요</h2>
            {refresh}
          </>
        ) : (
          <p className="phone-empty">주문 현황을 불러오는 중이에요.</p>
        )}
      </section>
    )
  }

  const headline = orderHeadline(order)
  return (
    <section className={`phone-status-card phone-status-${headline.tone}`} aria-label="주문 현황" aria-live="polite">
      <h2>{headline.title}</h2>
      {headline.detail && <p className="phone-status-detail">{headline.detail}</p>}
      <ol className="phone-steps">
        {orderSteps(order).map((step) => (
          <li
            key={step.key}
            className={`phone-step phone-step-${step.state}`}
            aria-current={step.state === 'current' ? 'step' : undefined}
          >
            {step.label}
          </li>
        ))}
      </ol>
      <ul className="phone-status-lines">
        {order.lines.map((line) => (
          <li key={line.menu_id + line.options.join(',')}>
            <span>
              {line.name} × {line.quantity}
              {line.options.length > 0 && <small> ({line.options.join(', ')})</small>}
              {line.request && <small> · {line.request}</small>}
            </span>
            <span>{won(line.subtotal)}</span>
          </li>
        ))}
      </ul>
      <p className="phone-total">
        <span>합계</span>
        <strong>{won(order.amount.total)}</strong>
      </p>
      {state.phase === 'failed' && <p className="phone-status-detail">최신 상태를 불러오지 못했어요.</p>}
      {refresh}
    </section>
  )
}
