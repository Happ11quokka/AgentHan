// Part C 사장님 화면 (4단계). 이 폴더(src/screens/owner/)는 Part C 몫이다.
// `/owner?key=…`로 연다. "가게 열기"를 누르면 소리 잠금 풀기·화면 꺼짐 방지·SSE 연결을 한 번에 한다.
// 이 티켓(01)의 카드는 한국어 메뉴명·수량·옵션·픽업 시각·총액만 보인다(사장님 언어는 티켓 03).
// 알림 소리·깜빡임·리마인드·카드 색·화면 꺼짐 방지 다시 잡기는 티켓 08(`orderAlerts`·`pickupBand`·`screenWakeLock`).
import { useEffect, useReducer, useRef, useState } from 'react'
import { ALERT_SETTINGS } from './alertSettings.ts'
import { OrderAlerts } from './orderAlerts.ts'
import { OrderChime } from './orderChime.ts'
import { type Connection, canAccept, initialOwnerState, ownerReducer } from './ownerState.ts'
import { acceptOrder, connectOwnerStream, ownerKey, wakeDevice } from './ownerStream.ts'
import { pickupBand } from './pickupBand.ts'
import { keepScreenAwake } from './screenWakeLock.ts'
import './owner.css'

const CONNECTION_LABEL: Record<Connection, string> = {
  closed: '가게 문을 열면 주문을 받아요',
  connecting: '연결 중…',
  live: '주문 받는 중',
  failed: '연결할 수 없어요. 주소의 토큰을 확인해 주세요',
}

/** locked: 가게 열기 전(브라우저가 사용자 동작 전 소리를 막는다). on: 알림음 켜짐. unavailable: 켤 수 없음 */
type Sound = 'locked' | 'on' | 'unavailable'

const SOUND_LABEL: Record<Sound, string> = {
  locked: '소리 꺼짐 · 가게 열기를 누르면 켜져요',
  on: '알림 소리 켜짐',
  unavailable: '알림 소리를 켤 수 없어요',
}

export default function OwnerPage() {
  const [state, dispatch] = useReducer(ownerReducer, initialOwnerState)
  const key = useRef(ownerKey(location.search))
  const close = useRef<(() => void) | null>(null)
  const chime = useRef<OrderChime | null>(null)
  const releaseWake = useRef<(() => void) | null>(null)
  const [sound, setSound] = useState<Sound>('locked')
  /** 리마인드가 울린 주문 id. 그 카드를 한 번 더 깜빡인다 */
  const [reminded, setReminded] = useState<ReadonlySet<string>>(new Set())
  const [now, setNow] = useState(() => Date.now())
  const alerts = useRef<OrderAlerts | null>(null)

  useEffect(() => {
    const current = new OrderAlerts({
      onArrive: () => chime.current?.play(),
      onRemind: (id) => {
        chime.current?.play()
        setReminded((ids) => new Set(ids).add(id))
      },
    })
    alerts.current = current
    return () => {
      close.current?.()
      releaseWake.current?.()
      current.close()
    }
  }, [])

  useEffect(() => alerts.current?.sync(state.orders), [state.orders])

  useEffect(() => {
    const timer = setInterval(() => setNow(Date.now()), ALERT_SETTINGS.clockTickMs)
    return () => clearInterval(timer)
  }, [])

  function openStore() {
    close.current?.()
    dispatch({ type: 'open' })
    close.current = connectOwnerStream(key.current, dispatch)
    releaseWake.current?.()
    releaseWake.current = keepScreenAwake(document, navigator.wakeLock)
    void wakeDevice().then((ctx) => {
      chime.current?.close()
      chime.current = ctx ? new OrderChime(ctx) : null
      setSound(ctx ? 'on' : 'unavailable')
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
        <p className={`owner-sound owner-sound-${sound}`}>{SOUND_LABEL[sound]}</p>
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
          <article
            key={card.id}
            className={[
              'owner-card',
              `owner-card-${card.status}`,
              `owner-band-${pickupBand(card, now)}`,
              reminded.has(card.id) ? 'owner-card-remind' : '',
            ].join(' ')}
          >
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
