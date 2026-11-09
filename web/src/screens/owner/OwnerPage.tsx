// Part C 사장님 화면 (4단계). 이 폴더(src/screens/owner/)는 Part C 몫이다.
// `/owner?key=…`로 연다. "가게 열기"를 누르면 소리 잠금 풀기·화면 꺼짐 방지·SSE 연결을 한 번에 한다.
// 이 티켓(01)의 카드는 한국어 메뉴명·수량·옵션·픽업 시각·총액만 보인다(사장님 언어는 티켓 03).
// 통화 카드(티켓 02): 맨 위 줄에 통화 중 카드, 그 아래 "손님 연락 필요"(넘김)·회색 미완료 카드(닫기 버튼).
// 알림 소리·깜빡임·리마인드·카드 색·화면 꺼짐 방지 다시 잡기는 티켓 08(`orderAlerts`·`pickupBand`·`screenWakeLock`).
// 질문 카드(티켓 05): 통화 중 줄 아래 맨 앞에. 메뉴·손님 질문 원문·남은 초·버튼 3개(돼요/안 돼요/모르겠어요).
import { useEffect, useReducer, useRef, useState } from 'react'
import { ALERT_SETTINGS } from './alertSettings.ts'
import { OrderAlerts } from './orderAlerts.ts'
import { OrderChime } from './orderChime.ts'
import {
  type CallCard,
  type CallEndReason,
  type Connection,
  type OrderCardLine,
  type OwnerAnswer,
  type QuestionCard,
  canAccept,
  followUpCalls,
  initialOwnerState,
  liveCalls,
  openQuestions,
  ownerReducer,
  secondsLeft,
} from './ownerState.ts'
import {
  acceptOrder,
  answerQuestion,
  connectOwnerStream,
  dismissCall,
  ownerKey,
  wakeDevice,
} from './ownerStream.ts'
import { pickupBand } from './pickupBand.ts'
import { keepScreenAwake } from './screenWakeLock.ts'
import './owner.css'

const CONNECTION_LABEL: Record<Connection, string> = {
  closed: '가게 문을 열면 주문을 받아요',
  connecting: '연결 중…',
  live: '주문 받는 중',
  failed: '연결할 수 없어요. 주소의 토큰을 확인해 주세요',
}

/** 미완료 카드의 끝난 이유 (사장님 언어는 티켓 03) */
const END_REASON_LABEL: Record<CallEndReason, string> = {
  confirmed: '주문 확정',
  handoff: '사장님 연결 요청',
  cancelled: '손님이 주문 취소',
  no_input: '손님 응답 없음',
  hangup: '손님이 끊음',
  error: '연결 오류',
}

/** 질문 카드 버튼 (사장님 언어는 티켓 03). 순서가 화면 순서다 */
const ANSWER_BUTTONS: { answer: OwnerAnswer; label: string }[] = [
  { answer: 'yes', label: '돼요' },
  { answer: 'no', label: '안 돼요' },
  { answer: 'unsure', label: '모르겠어요' },
]

/** 질문 카드 카운트다운을 다시 그리는 간격 */
const QUESTION_TICK_MS = 1_000

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

  // 답을 기다리는 질문 카드가 있는 동안만 1초마다 다시 그린다(카운트다운)
  const waiting = openQuestions(state).length > 0
  useEffect(() => {
    if (!waiting) return
    const tick = () => setNow(Date.now())
    const first = setTimeout(tick, 0) // 15초 시계가 늦어 있을 수 있어 바로 한 번 맞춘다
    const timer = setInterval(tick, QUESTION_TICK_MS)
    return () => {
      clearTimeout(first)
      clearInterval(timer)
    }
  }, [waiting])

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

  async function dismiss(id: string) {
    const card = await dismissCall(key.current, id)
    if (card) dispatch({ type: 'call.update', data: card })
  }

  async function answer(id: string, value: OwnerAnswer) {
    const card = await answerQuestion(key.current, id, value)
    if (card) dispatch({ type: 'owner.question.update', data: card })
  }

  const showOpen = state.connection === 'closed' || state.connection === 'failed'
  const live = liveCalls(state)
  const followUps = followUpCalls(state)
  const questions = openQuestions(state)

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

      {live.length > 0 && (
        <section className="owner-calls owner-calls-live" aria-label="통화 중">
          {live.map((call) => (
            <article key={call.id} className="owner-card owner-call owner-call-live">
              <header className="owner-card-head">
                <span className="owner-call-title">통화 중</span>
                <span className="owner-call-time">{call.started_label}</span>
              </header>
              {call.lines.length > 0 ? (
                <CardLines lines={call.lines} />
              ) : (
                <p className="owner-call-empty">아직 담은 메뉴가 없어요</p>
              )}
              {call.lines.length > 0 && <strong className="owner-total">{call.total_label}</strong>}
            </article>
          ))}
        </section>
      )}

      {questions.length > 0 && (
        <section className="owner-questions" aria-label="손님 질문">
          {questions.map((q) => (
            <QuestionCardView
              key={q.id}
              question={q}
              left={secondsLeft(q, now)}
              onAnswer={(value) => void answer(q.id, value)}
            />
          ))}
        </section>
      )}

      {followUps.length > 0 && (
        <section className="owner-calls" aria-label="확인할 통화">
          {followUps.map((call) => (
            <FollowUpCard key={call.id} call={call} onDismiss={() => void dismiss(call.id)} />
          ))}
        </section>
      )}

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
            <CardLines lines={card.lines} />
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

function CardLines({ lines }: { lines: OrderCardLine[] }) {
  return (
    <ul className="owner-lines">
      {lines.map((line, i) => (
        <li key={i}>
          <span className="owner-qty">{line.quantity}×</span> {line.name_ko}
          {line.options.length > 0 && <small> ({line.options.join(', ')})</small>}
          {line.request && <p className="owner-request">{line.request}</p>}
        </li>
      ))}
    </ul>
  )
}

/** "손님 연락 필요"(넘김) 또는 회색 "미완료 통화" 카드. 사장님이 닫을 때까지 남는다 */
function FollowUpCard({ call, onDismiss }: { call: CallCard; onDismiss: () => void }) {
  const handoff = call.status === 'handoff'
  return (
    <article className={`owner-card owner-call owner-call-${call.status}`}>
      <header className="owner-card-head">
        <span className="owner-call-title">{handoff ? '손님 연락 필요' : '미완료 통화'}</span>
        <span className="owner-call-time">{call.started_label}</span>
      </header>
      {!handoff && call.reason && <p className="owner-call-reason">{END_REASON_LABEL[call.reason]}</p>}
      {call.lines.length > 0 ? (
        <CardLines lines={call.lines} />
      ) : (
        <p className="owner-call-empty">담은 메뉴 없음</p>
      )}
      {handoff && call.heard.length > 0 && (
        <ol className="owner-heard" aria-label="마지막 손님 말">
          {call.heard.map((text, i) => (
            <li key={i}>“{text}”</li>
          ))}
        </ol>
      )}
      <button type="button" className="owner-btn owner-btn-dismiss" onClick={onDismiss}>
        닫기
      </button>
    </article>
  )
}

/** 손님 질문 카드. 버튼 하나로 답한다. 시간이 지나도 카드는 남는다(늦은 답은 티켓 07) */
function QuestionCardView({
  question,
  left,
  onAnswer,
}: {
  question: QuestionCard
  left: number
  onAnswer: (answer: OwnerAnswer) => void
}) {
  return (
    <article className="owner-card owner-question">
      <header className="owner-card-head">
        <span className="owner-question-title">손님 질문 · {question.menu_name}</span>
        <span className={`owner-question-left${left === 0 ? ' owner-question-late' : ''}`} role="timer">
          {left > 0 ? `${left}초` : '시간 지남'}
        </span>
      </header>
      <p className="owner-question-text">“{question.text}”</p>
      <div className="owner-question-answers">
        {ANSWER_BUTTONS.map(({ answer, label }) => (
          <button
            key={answer}
            type="button"
            className={`owner-btn owner-btn-answer owner-btn-${answer}`}
            onClick={() => onAnswer(answer)}
          >
            {label}
          </button>
        ))}
      </div>
    </article>
  )
}
