// Part C 사장님 화면 (4단계). 이 폴더(src/screens/owner/)는 Part C 몫이다.
// `/owner?key=…&lang=ru`로 연다(언어 기본 ru). "가게 열기"를 누르면 소리 잠금 풀기·화면 꺼짐 방지·SSE 연결을 한 번에 한다.
// 카드(티켓 03): 메뉴명은 사장님 원어 이름을 크게, 한국어를 작게. 옵션은 서버 정적 사전, 시각·금액은 서버 고정 표기.
// 손님 요청은 번역(Papago) + 한국어 원문 + 배지(한국어 원문 규칙). 번역이 오면 같은 카드가 다시 와서 덮어쓴다.
// 고정 문구는 `ownerText.ts` 사전. 화면 아래에 Papago 표기(이용 조건).
// 통화 카드(티켓 02): 맨 위 줄에 통화 중 카드, 그 아래 "손님 연락 필요"(넘김)·회색 미완료 카드(닫기 버튼).
// 알림 소리·깜빡임·리마인드·카드 색·화면 꺼짐 방지 다시 잡기는 티켓 08(`orderAlerts`·`pickupBand`·`screenWakeLock`).
// 질문 카드(티켓 05): 통화 중 줄 아래 맨 앞에. 메뉴·손님 질문·남은 초·버튼 3개(돼요/안 돼요/모르겠어요).
// 티켓 06: 시간 초과·통화 끝 카드도 남아 답을 받는다. 화면이 보이는 동안 뜬 카드는 열람으로 알린다.
// 답을 못 받은 질문이 있던 확정 주문 카드에는 "사장님 확인 필요".
// 통화 자막(티켓 10): 통화 중 카드 아래 최근 4줄. 손님 말은 번역 + 작은 한국어 원문(번역 전·실패면
// 원문만), AI 말은 한국어 원문 회색 한 줄.
// 티켓 07: 질문 카드에 접힌 자유 입력(손님에게는 번역 문자로만 감). 카드 안에 "손님에게 보낸 문자" 한 줄씩.
import { useEffect, useReducer, useRef, useState } from 'react'
import { ALERT_SETTINGS } from './alertSettings.ts'
import { OrderAlerts } from './orderAlerts.ts'
import { OrderChime } from './orderChime.ts'
import {
  type CallCard,
  type CaptionLine,
  type OrderCardLine,
  type OwnerAnswer,
  type QuestionCard,
  type QuestionClock,
  type SentText,
  WRITTEN_ANSWER_MAX,
  canAccept,
  captionLines,
  followUpCalls,
  initialOwnerState,
  liveCalls,
  needsSeen,
  openQuestions,
  ownerReducer,
  questionClock,
  questionsWaiting,
  writtenAnswer,
} from './ownerState.ts'
import {
  acceptOrder,
  answerQuestion,
  connectOwnerStream,
  dismissCall,
  ownerKey,
  seeQuestion,
  wakeDevice,
} from './ownerStream.ts'
import {
  OWNER_TEXT,
  type OwnerLang,
  type OwnerText,
  PAPAGO_URL,
  captionView,
  lineView,
  ownerLang,
  questionView,
  sentTextView,
} from './ownerText.ts'
import { pickupBand } from './pickupBand.ts'
import { keepScreenAwake } from './screenWakeLock.ts'
import './owner.css'

/** 질문 카드 버튼. 순서가 화면 순서다. 문구는 `ownerText.ts` */
const ANSWERS: OwnerAnswer[] = ['yes', 'no', 'unsure']

/** 질문 카드 카운트다운을 다시 그리는 간격 */
const QUESTION_TICK_MS = 1_000

/** locked: 가게 열기 전(브라우저가 사용자 동작 전 소리를 막는다). on: 알림음 켜짐. unavailable: 켤 수 없음 */
type Sound = 'locked' | 'on' | 'unavailable'

export default function OwnerPage() {
  const [state, dispatch] = useReducer(ownerReducer, initialOwnerState)
  const key = useRef(ownerKey(location.search))
  const [lang] = useState<OwnerLang>(() => ownerLang(location.search))
  const text = OWNER_TEXT[lang]
  const close = useRef<(() => void) | null>(null)
  const chime = useRef<OrderChime | null>(null)
  const releaseWake = useRef<(() => void) | null>(null)
  const [sound, setSound] = useState<Sound>('locked')
  /** 리마인드가 울린 주문 id. 그 카드를 한 번 더 깜빡인다 */
  const [reminded, setReminded] = useState<ReadonlySet<string>>(new Set())
  const [now, setNow] = useState(() => Date.now())
  const alerts = useRef<OrderAlerts | null>(null)
  /** 열람을 알린 질문 카드 id (카드마다 한 번) */
  const seenSent = useRef(new Set<string>())

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

  // 화면이 보이는 동안 뜬 질문 카드는 사장님이 봤다고 알린다(티켓 06). 통화가 기다리는 카드만,
  // 카드마다 한 번. 탭이 가려져 있으면 다시 보일 때 알린다
  useEffect(() => {
    const report = () => {
      if (document.visibilityState !== 'visible') return
      for (const q of state.questions) {
        if (!needsSeen(q) || seenSent.current.has(q.id)) continue
        seenSent.current.add(q.id)
        void seeQuestion(key.current, q.id).then((card) => {
          if (card) dispatch({ type: 'owner.question.update', data: card })
        })
      }
    }
    report()
    document.addEventListener('visibilitychange', report)
    return () => document.removeEventListener('visibilitychange', report)
  }, [state.questions])

  // 통화가 답을 기다리는 질문 카드가 있는 동안만 1초마다 다시 그린다(카운트다운)
  const waiting = questionsWaiting(state)
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

  async function answer(id: string, value: OwnerAnswer | 'free', written = '') {
    const card = await answerQuestion(key.current, id, value, written)
    if (card) dispatch({ type: 'owner.question.update', data: card })
  }

  const showOpen = state.connection === 'closed' || state.connection === 'failed'
  const live = liveCalls(state)
  const followUps = followUpCalls(state)
  const questions = openQuestions(state)

  return (
    <main className="owner" lang={lang}>
      <header className="owner-head">
        <h1>{text.title}</h1>
        <p className={`owner-status owner-status-${state.connection}`} role="status">
          {text.connection[state.connection]}
        </p>
        <p className={`owner-sound owner-sound-${sound}`}>{text.sound[sound]}</p>
        {showOpen && (
          <button type="button" className="owner-btn owner-btn-open" onClick={openStore}>
            {text.openStore}
          </button>
        )}
      </header>

      {live.length > 0 && (
        <section className="owner-calls owner-calls-live" aria-label={text.sectionLive}>
          {live.map((call) => (
            <article key={call.id} className="owner-card owner-call owner-call-live">
              <header className="owner-card-head">
                <span className="owner-call-title">{text.liveCall}</span>
                <span className="owner-call-time">{call.started_label}</span>
              </header>
              {call.lines.length > 0 ? (
                <CardLines lines={call.lines} lang={lang} text={text} />
              ) : (
                <p className="owner-call-empty">{text.liveEmpty}</p>
              )}
              {call.lines.length > 0 && <strong className="owner-total">{call.total_label}</strong>}
              <Captions lines={captionLines(state, call.id)} lang={lang} text={text} />
            </article>
          ))}
        </section>
      )}

      {questions.length > 0 && (
        <section className="owner-questions" aria-label={text.sectionQuestions}>
          {questions.map((q) => (
            <QuestionCardView
              key={q.id}
              question={q}
              clock={questionClock(q, now)}
              lang={lang}
              text={text}
              onAnswer={(value) => void answer(q.id, value)}
              onWrite={(written) => void answer(q.id, 'free', written)}
            />
          ))}
        </section>
      )}

      {followUps.length > 0 && (
        <section className="owner-calls" aria-label={text.sectionFollowUp}>
          {followUps.map((call) => (
            <FollowUpCard
              key={call.id}
              call={call}
              lang={lang}
              text={text}
              onDismiss={() => void dismiss(call.id)}
            />
          ))}
        </section>
      )}

      <section className="owner-orders" aria-label={text.sectionOrders}>
        {state.connection === 'live' && state.orders.length === 0 && (
          <p className="owner-empty">{text.noOrders}</p>
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
              <span className="owner-pickup">
                <small className="owner-pickup-label">{text.pickup}</small> {card.pickup_label}
              </span>
              <strong className="owner-total">{card.total_label}</strong>
            </header>
            <CardLines lines={card.lines} lang={lang} text={text} />
            {card.owner_check && <p className="owner-check">{text.ownerCheck}</p>}
            <SentTexts texts={card.sms} lang={lang} />
            {canAccept(card) ? (
              <button type="button" className="owner-btn owner-btn-accept" onClick={() => void accept(card.id)}>
                {text.accept}
              </button>
            ) : (
              <p className="owner-accepted">{text.accepted}</p>
            )}
          </article>
        ))}
      </section>

      <footer className="owner-foot">
        <a href={PAPAGO_URL} target="_blank" rel="noreferrer">
          {text.papago}
        </a>
      </footer>
    </main>
  )
}

function CardLines({ lines, lang, text }: { lines: OrderCardLine[]; lang: OwnerLang; text: OwnerText }) {
  return (
    <ul className="owner-lines">
      {lines.map((line, i) => {
        const view = lineView(line, lang)
        return (
          <li key={i}>
            <span className="owner-qty">{line.quantity}×</span> <span className="owner-name">{view.name}</span>
            {view.nameKo && (
              <small className="owner-name-ko" lang="ko">
                {view.nameKo}
              </small>
            )}
            {view.options.length > 0 && <small className="owner-options"> ({view.options.join(', ')})</small>}
            {line.request && (
              <div className="owner-request">
                {line.badges.length > 0 && (
                  <span className="owner-badges">
                    {line.badges.map((badge) => (
                      <span key={badge} className={`owner-badge owner-badge-${badge}`}>
                        {text.badge[badge]}
                      </span>
                    ))}
                  </span>
                )}
                {view.request && <p className="owner-request-owner">{view.request}</p>}
                <p className="owner-request-ko" lang="ko">
                  {view.requestKo}
                </p>
              </div>
            )}
          </li>
        )
      })}
    </ul>
  )
}

/** 통화 중 카드 아래 자막 (티켓 10). 줄이 없으면 아무것도 그리지 않는다 */
function Captions({ lines, lang, text }: { lines: CaptionLine[]; lang: OwnerLang; text: OwnerText }) {
  if (lines.length === 0) return null
  return (
    <ol className="owner-captions" aria-label={text.captions} aria-live="polite">
      {lines.map((line) => {
        const view = captionView(line, lang)
        return (
          <li key={line.n} className={`owner-caption owner-caption-${view.who}`}>
            <span className="owner-caption-who">{text.speaker[view.who]}</span>{' '}
            <span className="owner-caption-main" lang={view.mainKo ? 'ko' : undefined}>
              {view.main}
            </span>
            {view.original && (
              <small className="owner-caption-ko" lang="ko">
                {view.original}
              </small>
            )}
          </li>
        )
      })}
    </ol>
  )
}

/** "손님 연락 필요"(넘김) 또는 회색 "미완료 통화" 카드. 사장님이 닫을 때까지 남는다 */
function FollowUpCard({
  call,
  lang,
  text,
  onDismiss,
}: {
  call: CallCard
  lang: OwnerLang
  text: OwnerText
  onDismiss: () => void
}) {
  const handoff = call.status === 'handoff'
  return (
    <article className={`owner-card owner-call owner-call-${call.status}`}>
      <header className="owner-card-head">
        <span className="owner-call-title">{handoff ? text.handoff : text.missed}</span>
        <span className="owner-call-time">{call.started_label}</span>
      </header>
      {!handoff && call.reason && <p className="owner-call-reason">{text.endReason[call.reason]}</p>}
      {call.lines.length > 0 ? (
        <CardLines lines={call.lines} lang={lang} text={text} />
      ) : (
        <p className="owner-call-empty">{text.endedEmpty}</p>
      )}
      {handoff && call.heard.length > 0 && (
        <ol className="owner-heard" aria-label={text.heard}>
          {call.heard.map((korean, i) => {
            const translated = lang === 'ko' ? '' : (call.heard_owner[i] ?? '')
            return translated ? (
              <li key={i}>
                “{translated}”
                <details className="owner-heard-ko">
                  <summary>{text.korean}</summary>
                  <span lang="ko">{korean}</span>
                </details>
              </li>
            ) : (
              <li key={i} lang="ko">
                “{korean}”
              </li>
            )
          })}
        </ol>
      )}
      <SentTexts texts={call.sms} lang={lang} />
      <button type="button" className="owner-btn owner-btn-dismiss" onClick={onDismiss}>
        {text.dismiss}
      </button>
    </article>
  )
}

/** 카드 안 "손님에게 보낸 문자" (티켓 07). 한 통에 한 줄: 종류·시각 + 손님이 받은 한국어 문장 */
function SentTexts({ texts, lang }: { texts: SentText[] | undefined; lang: OwnerLang }) {
  if (!texts || texts.length === 0) return null
  return (
    <ul className="owner-sms">
      {texts.map((t) => {
        const view = sentTextView(t, lang)
        return (
          <li key={t.id}>
            <span className="owner-sms-label">{view.label}</span>{' '}
            <span className="owner-sms-text" lang="ko">
              {view.text}
            </span>
            {view.failed && <span className="owner-sms-failed">{view.failed}</span>}
          </li>
        )
      })}
    </ul>
  )
}

/**
 * 손님 질문 카드. 버튼 하나로 답하거나, 접힌 칸에서 글로 답한다(티켓 07, 손님에게는 번역 문자로만 감).
 * 시간이 지나거나 통화가 끝나도 카드는 남는다(늦은 답은 손님 문자로 간다)
 */
function QuestionCardView({
  question,
  clock,
  lang,
  text,
  onAnswer,
  onWrite,
}: {
  question: QuestionCard
  /** 남은 초 · 시간 지남 · 통화 끝 (`questionClock`) */
  clock: QuestionClock
  lang: OwnerLang
  text: OwnerText
  onAnswer: (answer: OwnerAnswer) => void
  onWrite: (written: string) => void
}) {
  const view = questionView(question, lang)
  const late = typeof clock !== 'number'
  const [draft, setDraft] = useState('')
  const written = writtenAnswer(draft)
  return (
    <article className="owner-card owner-question">
      <header className="owner-card-head">
        <span className="owner-question-title">
          {text.question} · {view.menu}
        </span>
        <span className={`owner-question-left${late ? ' owner-question-late' : ''}`} role="timer">
          {late ? (clock === 'ended' ? text.callEnded : text.late) : text.secondsLeft(clock)}
        </span>
      </header>
      {view.question && <p className="owner-question-text">“{view.question}”</p>}
      <p className={view.question ? 'owner-question-ko' : 'owner-question-text'} lang="ko">
        “{view.questionKo}”
      </p>
      <div className="owner-question-answers">
        {ANSWERS.map((answer) => (
          <button
            key={answer}
            type="button"
            className={`owner-btn owner-btn-answer owner-btn-${answer}`}
            onClick={() => onAnswer(answer)}
          >
            {text.answer[answer]}
          </button>
        ))}
      </div>
      <details className="owner-written">
        <summary>{text.written}</summary>
        <p className="owner-written-hint">{text.writtenHint}</p>
        <textarea
          className="owner-written-input"
          value={draft}
          rows={2}
          maxLength={WRITTEN_ANSWER_MAX}
          onChange={(e) => setDraft(e.target.value)}
        />
        <button
          type="button"
          className="owner-btn owner-btn-written"
          disabled={written === null}
          onClick={() => written !== null && onWrite(written)}
        >
          {text.writtenSend}
        </button>
      </details>
    </article>
  )
}
