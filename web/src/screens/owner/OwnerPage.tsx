// Part C 사장님 화면 (4단계). 이 폴더(src/screens/owner/)는 Part C 몫이다.
// `/owner?key=…`로 연다. "가게 열기"를 누르면 소리 잠금 풀기·화면 꺼짐 방지·SSE 연결을 한 번에 한다.
// 이 티켓(01)의 카드는 한국어 메뉴명·수량·옵션·픽업 시각·총액만 보인다(사장님 언어는 티켓 03).
import { useEffect, useReducer, useRef } from 'react'
import { type Connection, canAccept, initialOwnerState, ownerReducer } from './ownerState.ts'
import { acceptOrder, connectOwnerStream, ownerKey, wakeDevice } from './ownerStream.ts'
import './owner.css'

const CONNECTION_LABEL: Record<Connection, string> = {
  closed: '가게 문을 열면 주문을 받아요',
  connecting: '연결 중…',
  live: '주문 받는 중',
  failed: '연결할 수 없어요. 주소의 토큰을 확인해 주세요',
}

export default function OwnerPage() {
  const [state, dispatch] = useReducer(ownerReducer, initialOwnerState)
  const key = useRef(ownerKey(location.search))
  const close = useRef<(() => void) | null>(null)
  const audio = useRef<AudioContext | null>(null) // 알림 소리(티켓 08)가 쓴다

  useEffect(() => () => close.current?.(), [])

  function openStore() {
    close.current?.()
    dispatch({ type: 'open' })
    close.current = connectOwnerStream(key.current, dispatch)
    void wakeDevice().then((ctx) => {
      audio.current = ctx
    })
  }

  async function accept(id: string) {
    const card = await acceptOrder(key.current, id)
    if (card) dispatch({ type: 'order.update', data: card })
  }

  const showOpen = state.connection === 'closed' || state.connection === 'failed'

  return (
    <main className="owner">
      <header className="owner-head">
        <h1>사장님 화면</h1>
        <p className={`owner-status owner-status-${state.connection}`} role="status">
          {CONNECTION_LABEL[state.connection]}
        </p>
        {showOpen && (
          <button type="button" className="owner-btn owner-btn-open" onClick={openStore}>
            가게 열기
          </button>
        )}
      </header>

      <section className="owner-orders" aria-label="주문">
        {state.connection === 'live' && state.orders.length === 0 && (
          <p className="owner-empty">아직 들어온 주문이 없어요.</p>
        )}
        {state.orders.map((card) => (
          <article key={card.id} className={`owner-card owner-card-${card.status}`}>
            <header className="owner-card-head">
              <span className="owner-pickup">{card.pickup_label}</span>
              <strong className="owner-total">{card.total_label}</strong>
            </header>
            <ul className="owner-lines">
              {card.lines.map((line, i) => (
                <li key={i}>
                  <span className="owner-qty">{line.quantity}×</span> {line.name_ko}
                  {line.options.length > 0 && <small> ({line.options.join(', ')})</small>}
                  {line.request && <p className="owner-request">{line.request}</p>}
                </li>
              ))}
            </ul>
            {canAccept(card) ? (
              <button type="button" className="owner-btn owner-btn-accept" onClick={() => void accept(card.id)}>
                접수
              </button>
            ) : (
              <p className="owner-accepted">접수함</p>
            )}
          </article>
        ))}
      </section>
    </main>
  )
}
