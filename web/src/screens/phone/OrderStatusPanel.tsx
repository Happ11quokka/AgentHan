// 손님 주문 현황 (전화 메뉴·주문 현황·쿠폰 티켓 04·05). 통화가 확정 주문으로 끝나면 통화 화면 대신 보인다.
// 상태·문구는 순수 모듈 `orderStatus.ts`가 정한다. 열 때 조회 API로 한 번 읽고, 결정이 날 때까지 주문 번호별
// 스트림(`orderStream.ts`)으로 스스로 바뀐다. 스트림이 끊기면 조회 API로 다시 읽는다. 손님은 새로 고침을 누르지
// 않는다. 취소된 주문에는 [다시 주문하기]가 있다(통화 화면으로).
// 손님 취소(티켓 07): 취소할 수 있는 주문에는 [취소하기]. 누르면 화면 안에서 한 번 묻고(브라우저 대화상자 없음)
// 취소 API를 부른다. 거부되면 응답의 지금 현황(이긴 결과)과 한 줄을 보인다. 제시간 수락은 "가게로 전화" 안내만.
import { useEffect, useReducer, useState } from 'react'
import { CANCEL_FAILED, cancelHint, cancelOrder } from './orderCancel.ts'
import {
  canReorder,
  fetchOrderStatus,
  initialOrderStatus,
  orderHeadline,
  orderSettled,
  orderStatusReducer,
  orderSteps,
  pickupMoved,
} from './orderStatus.ts'
import { type OrderSource, openOrderStream } from './orderStream.ts'

const won = (n: number) => `${n.toLocaleString('ko-KR')}원`

interface Props {
  orderId: string
  /** [다시 주문하기]: 통화 화면으로 돌아간다 */
  onReorder: () => void
  /** 시험용: 조회 API와 스트림을 가짜로 바꿔 끼운다 */
  get?: (url: string) => Promise<Response>
  openSource?: (url: string) => OrderSource
  /** 시험용: 손님 취소 API를 가짜로 바꿔 끼운다 (티켓 07) */
  post?: (url: string, init?: RequestInit) => Promise<Response>
}

/** 손님 취소 단계: idle 버튼만 · asking 화면 안에서 한 번 묻는 중 · sending 보내는 중 */
type CancelStep = 'idle' | 'asking' | 'sending'

export default function OrderStatusPanel({ orderId, onReorder, get, openSource, post }: Props) {
  const [state, dispatch] = useReducer(orderStatusReducer, orderId, initialOrderStatus)
  const [cancelStep, setCancelStep] = useState<CancelStep>('idle')
  /** 취소가 거부됐거나 닿지 못했을 때 한 줄 */
  const [cancelNotice, setCancelNotice] = useState<string | null>(null)
  // 다시 읽기 요청마다 늘린다. 효과가 이 값을 보고 조회를 다시 부른다
  const [round, reload] = useReducer((n: number) => n + 1, 0)
  const settled = orderSettled(state.order)

  useEffect(() => {
    let alive = true
    dispatch({ type: 'load' })
    void fetchOrderStatus(orderId, get).then((action) => {
      if (alive) dispatch(action)
    })
    return () => {
      alive = false
    }
  }, [orderId, round, get])

  // 결정이 날 때까지 스트림을 열어 둔다. 끊기면 그 사이의 결정을 조회 API로 다시 읽는다
  useEffect(() => {
    if (settled) return
    return openOrderStream(
      orderId,
      (order) => dispatch({ type: 'streamed', order }),
      reload,
      openSource ? { open: openSource } : {},
    )
  }, [orderId, settled, openSource])

  const order = state.order

  async function sendCancel() {
    setCancelStep('sending')
    const result = await cancelOrder(orderId, post)
    setCancelStep('idle')
    switch (result.type) {
      case 'cancelled':
        setCancelNotice(null)
        dispatch({ type: 'loaded', order: result.order })
        return
      case 'refused': // 사장님이 먼저 결정했다: 이긴 결과를 보인다
        setCancelNotice(result.message)
        dispatch({ type: 'loaded', order: result.order })
        return
      case 'missing':
        dispatch({ type: 'missing' })
        return
      case 'failed':
        setCancelNotice(CANCEL_FAILED)
    }
  }

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
            <button type="button" className="phone-btn" onClick={reload}>
              다시 불러오기
            </button>
          </>
        ) : (
          <p className="phone-empty">주문 현황을 불러오는 중이에요.</p>
        )}
      </section>
    )
  }

  const headline = orderHeadline(order)
  const moved = pickupMoved(order)
  const soldOut = new Set(order.sold_out)
  return (
    <section className={`phone-status-card phone-status-${headline.tone}`} aria-label="주문 현황" aria-live="polite">
      <h2>{headline.title}</h2>
      {headline.detail && <p className="phone-status-detail">{headline.detail}</p>}
      {headline.quote && <blockquote className="phone-status-quote">사장님 말씀: «{headline.quote}»</blockquote>}
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
      {moved && (
        <p className="phone-pickup">
          픽업 <s>{moved.from}</s> → <strong>{moved.to}</strong>
        </p>
      )}
      <ul className="phone-status-lines">
        {order.lines.map((line) => (
          <li key={line.menu_id + line.options.join(',')}>
            <span>
              {line.name} × {line.quantity}
              {line.options.length > 0 && <small> ({line.options.join(', ')})</small>}
              {line.request && <small> · {line.request}</small>}
              {soldOut.has(line.name) && <small className="phone-sold-out"> · 재료 소진</small>}
            </span>
            <span>{won(line.subtotal)}</span>
          </li>
        ))}
      </ul>
      <p className="phone-total">
        <span>합계</span>
        <strong>{won(order.amount.total)}</strong>
      </p>
      {cancelNotice && (
        <p className="phone-cancel-notice" role="alert">
          {cancelNotice}
        </p>
      )}
      {order.can_cancel &&
        (cancelStep === 'idle' ? (
          <button type="button" className="phone-btn phone-btn-cancel" onClick={() => setCancelStep('asking')}>
            취소하기
          </button>
        ) : (
          <div className="phone-cancel-ask" role="group" aria-label="주문 취소 확인">
            <p>주문을 취소할까요?</p>
            <button
              type="button"
              className="phone-btn phone-btn-cancel-yes"
              disabled={cancelStep === 'sending'}
              onClick={() => void sendCancel()}
            >
              네, 취소할게요
            </button>
            <button
              type="button"
              className="phone-btn"
              disabled={cancelStep === 'sending'}
              onClick={() => setCancelStep('idle')}
            >
              아니요
            </button>
          </div>
        ))}
      {cancelHint(order) && <p className="phone-cancel-hint">{cancelHint(order)}</p>}
      {canReorder(order) && (
        <button type="button" className="phone-btn phone-btn-answer" onClick={onReorder}>
          다시 주문하기
        </button>
      )}
    </section>
  )
}
