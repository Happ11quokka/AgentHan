// Part C 사장님 화면 (4단계). 이 폴더(src/screens/owner/)는 Part C 몫이다.
// `/owner?key=…&lang=ru`로 연다(언어 기본 ru). "가게 열기"를 누르면 소리 잠금 풀기·화면 꺼짐 방지·SSE 연결을 한 번에 한다.
// 카드(티켓 03): 메뉴명은 사장님 원어 이름을 크게, 한국어를 작게. 옵션은 서버 정적 사전, 시각·금액은 서버 고정 표기.
// 손님 요청은 번역(Papago) + 한국어 원문 + 배지(한국어 원문 규칙). 번역이 오면 같은 카드가 다시 와서 덮어쓴다.
// 고정 문구는 `ownerText.ts` 사전. 화면 아래에 Papago 표기(이용 조건).
// 통화 카드(티켓 02): 맨 위 줄에 통화 중 카드, 그 아래 "손님 연락 필요"(넘김)·회색 미완료 카드(닫기 버튼).
// 알림 소리·깜빡임·카드 색·화면 꺼짐 방지 다시 잡기는 티켓 08(`orderAlerts`·`pickupBand`·`screenWakeLock`).
// 확인 대기 카드가 남아 있으면 몇 초마다 다시 울리고 깜빡인다(주문 승인 티켓 08, 예전 1분 리마인드 대체).
// 질문 카드(티켓 05): 통화 중 줄 아래 맨 앞에. 메뉴·손님 질문·남은 초·버튼 3개(돼요/안 돼요/모르겠어요).
// 티켓 06: 시간 초과·통화 끝 카드도 남아 답을 받는다. 질문 카드는 접혀 오고, 사장님이 눌러 펼칠 때만
// 열람을 알린다(리뷰 P4: 화면이 켜져 있는 것만으로는 "사장님이 보고 계세요"를 보내지 않는다).
// 버튼·저장 POST 응답 카드는 화면에 넣지 않고 SSE로만 받는다(리뷰 C8: 늦은 응답이 새 카드를 덮지 않게).
// 답을 못 받은 질문이 있던 확정 주문 카드에는 "사장님 확인 필요".
// 통화 자막(티켓 10): 통화 중 카드 아래 최근 4줄. 손님 말은 번역 + 작은 한국어 원문(번역 전·실패면
// 원문만), AI 말은 한국어 원문 회색 한 줄.
// 티켓 07: 질문 카드에 접힌 자유 입력(손님에게는 번역 문자로만 감). 카드 안에 "손님에게 보낸 문자" 한 줄씩.
// 티켓 13: 주문 카드 아래 접힌 "손님이 궁금해한 것"(AI가 답 못 한 메뉴 질문, 메뉴별 카드·횟수).
// 사장님 글 → "번역 보기"(한국어 미리보기, 고칠 수 있음) → "저장"을 눌러야 다음 손님부터 AI가 쓴다.
// 주문 승인 티켓 02: 주문 카드는 확인 요청 카드다. 확인 대기면 대기 단계·카운트다운·[수락], 수락하면
// 초록 "수락 · 픽업 시각", 확인 요청 없이 확정된 주문은 "자동 확정(사장님 화면 꺼짐)". 손님이 기다리는
// 중에 고치거나 취소하면 버튼을 숨긴다.
// 주문 승인 티켓 03: 통화 뒤 대기는 분:초 카운트다운(요청 연 때부터 5분), 자동 취소 카드는 회색에 버튼 없음.
// 주문 승인 티켓 05: 확인 대기 카드의 [거절] → 사유 시트(`OrderReject.tsx`), 거절 카드는 회색 "거절 · 사유".
// 주문 승인 티켓 06: 재료 소진은 주문 메뉴 다중 선택. 품절을 건 카드에 "품절로 바꿨어요 [되돌리기]".
import { useEffect, useReducer, useRef, useState } from 'react'
import type { CustomerText } from '../customerText.ts'
import { ALERT_SETTINGS } from './alertSettings.ts'
import { RejectButton, RejectedResult, SoldOutMark } from './OrderReject.tsx'
import { OrderAlerts } from './orderAlerts.ts'
import { OrderChime } from './orderChime.ts'
import {
  type CallCard,
  type CaptionLine,
  type KnowledgeCard,
  type KnowledgePreview,
  type OrderCard,
  type OrderCardLine,
  type OwnerAnswer,
  type QuestionCard,
  type QuestionClock,
  SeenReports,
  WRITTEN_ANSWER_MAX,
  approvalClock,
  approvalsWaiting,
  captionLines,
  effectivePickup,
  followUpCalls,
  initialOwnerState,
  knowledgeCards,
  liveCalls,
  openQuestions,
  orderOutcome,
  ownerReducer,
  questionClock,
  questionsWaiting,
  storeNow,
  unansweredKnowledge,
  writtenAnswer,
} from './ownerState.ts'
import {
  ACCEPT_DELAYS,
  type AcceptDelay,
  acceptOrder,
  answerQuestion,
  connectOwnerStream,
  dismissCall,
  ownerKey,
  previewKnowledge,
  rejectOrder,
  saveKnowledge,
  seeQuestion,
  undoSoldOut,
  wakeDevice,
} from './ownerStream.ts'
import {
  OWNER_TEXT,
  type OwnerLang,
  type OwnerText,
  PAPAGO_URL,
  answerLabel,
  approvalLeft,
  captionView,
  knowledgeView,
  lineView,
  orderMeta,
  ownerLang,
  questionView,
  sentTextView,
} from './ownerText.ts'
import { pickupBand } from './pickupBand.ts'
import type { RejectBody } from './rejectSheet.ts'
import { keepScreenAwake } from './screenWakeLock.ts'
import './owner.css'

/** 질문 카드 버튼. 순서가 화면 순서다. 문구는 `ownerText.ts` */
const ANSWERS: OwnerAnswer[] = ['yes', 'no', 'unsure']

/**
 * 주문 카드 아래 결과 줄 (주문 승인 티켓 02). 확인 대기: 대기 단계 + 남은 초 + [수락] [+15분]
 * [+30분](티켓 04). 수락: 초록 "수락 · 18:30". 늦춘 수락: 초록 "18:45로 수락 (+15분)"(통화 중이면
 * 손님이 답하는 중 표시). 손님이 늦춘 시각을 거절: 회색. 자동 확정: 그 표시. 손님이 고치는 중·
 * 취소·주문 현황에서 손님 취소(전화 주문 현황 티켓 07, "손님 취소"): 버튼 없이 그 표시
 */
function OrderDecision({
  card,
  nowMs,
  lang,
  text,
  onAccept,
  onReject,
}: {
  card: OrderCard
  nowMs: number
  lang: OwnerLang
  text: OwnerText
  onAccept: (delayMin: AcceptDelay) => void
  /** 거절 사유 시트 보내기 (티켓 05) */
  onReject: (body: RejectBody) => void
}) {
  const outcome = orderOutcome(card)
  if (outcome === 'waiting') {
    const clock = approvalClock(card, nowMs)
    return (
      <div className="owner-approval">
        <p className="owner-approval-wait">
          {card.stage && <span>{text.approvalStage[card.stage]}</span>}{' '}
          <span className={clock === 'late' ? 'owner-question-late' : 'owner-approval-clock'}>
            {clock === 'late' ? text.late : approvalLeft(clock, lang)}
          </span>
        </p>
        <button type="button" className="owner-btn owner-btn-accept" onClick={() => onAccept(0)}>
          {text.accept}
        </button>
        {ACCEPT_DELAYS.filter((m) => m > 0).map((minutes) => (
          <button
            key={minutes}
            type="button"
            className="owner-btn owner-btn-later"
            onClick={() => onAccept(minutes)}
          >
            {text.acceptLater(minutes)}
          </button>
        ))}
        <RejectButton text={text} lang={lang} lines={card.lines} onReject={onReject} />
      </div>
    )
  }
  if (outcome === 'rejected') return <RejectedResult card={card} lang={lang} />
  if (outcome === 'delayed') {
    return (
      <>
        <p className="owner-accepted owner-accepted-yes">
          {text.acceptedLater(card.owner_pickup_label ?? '', card.delay_min ?? 0)}
        </p>
        {card.delay_reply === 'asking' && <p className="owner-delay-asking">{text.delayAsking}</p>}
      </>
    )
  }
  if (outcome === 'declined') {
    return <p className="owner-withdrawn">{text.delayDeclined(card.owner_pickup_label ?? '')}</p>
  }
  if (outcome === 'expired') return <p className="owner-expired">{text.expired}</p>
  if (outcome === 'customer_cancelled') return <p className="owner-withdrawn">{text.customerCancelled}</p>
  if (outcome === 'changing' || outcome === 'cancelled') {
    return <p className="owner-withdrawn">{text.withdrawn[outcome === 'changing' ? 'changed' : 'cancelled']}</p>
  }
  if (outcome === 'accepted' || outcome === 'auto') {
    return (
      <>
        <p className="owner-accepted owner-accepted-yes">{text.acceptedAt(card.pickup_label)}</p>
        {outcome === 'auto' && <p className="owner-auto">{text.autoAccepted}</p>}
      </>
    )
  }
  return null
}

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
  /** 반복 알림 때 다시 깜빡일 확인 대기 주문 id (반복마다 비었다 찼다 한다, 주문 승인 티켓 08) */
  const [reminded, setReminded] = useState<ReadonlySet<string>>(new Set())
  const [now, setNow] = useState(() => Date.now())
  const alerts = useRef<OrderAlerts | null>(null)
  /** 사장님이 펼친 질문 카드의 열람 알림 (카드마다 한 번, 리뷰 P4) */
  const seen = useRef(new SeenReports())

  useEffect(() => {
    const current = new OrderAlerts({
      ring: () => chime.current?.play(),
      soundOn: () => chime.current !== null,
      // 반복마다 대기 카드 깜빡임을 다시 튼다(클래스를 켰다 껐다 하면 애니메이션이 처음부터 돈다)
      onRepeat: (ids) => setReminded((prev) => (prev.size > 0 ? new Set() : new Set(ids))),
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

  // 통화가 답을 기다리는 질문 카드나 확인 대기 주문 카드가 있는 동안만 1초마다 다시 그린다(카운트다운)
  const waiting = questionsWaiting(state) || approvalsWaiting(state)
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

  // 버튼 POST. 바뀐 카드는 SSE로 온다(응답 카드는 넣지 않는다, 리뷰 C8)
  function accept(id: string, delayMin: AcceptDelay = 0) {
    void acceptOrder(key.current, id, delayMin)
  }

  function reject(id: string, body: RejectBody) {
    void rejectOrder(key.current, id, body)
  }

  function dismiss(id: string) {
    void dismissCall(key.current, id)
  }

  function answer(id: string, value: OwnerAnswer | 'free', written = '') {
    void answerQuestion(key.current, id, value, written)
  }

  function save(id: string, textKo: string, written: string) {
    void saveKnowledge(key.current, id, textKo, written)
  }

  /** 사장님이 질문 카드를 눌러 펼쳤다. 통화가 기다리는 카드면 한 번 알린다 (티켓 06, 리뷰 P4) */
  function open(q: QuestionCard) {
    if (seen.current.opened(q)) void seeQuestion(key.current, q.id)
  }

  const showOpen = state.connection === 'closed' || state.connection === 'failed'
  const live = liveCalls(state)
  const followUps = followUpCalls(state)
  const questions = openQuestions(state)
  const knowledge = knowledgeCards(state)
  const waitingKnowledge = unansweredKnowledge(state).length
  // 카드의 시각(마감·픽업)은 허브 시계다. 기기 시계를 그만큼 보정해 센다 (리뷰 C2)
  const hubNow = storeNow(state, now)

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
              clock={questionClock(q, hubNow)}
              lang={lang}
              text={text}
              onOpen={() => open(q)}
              onAnswer={(value) => answer(q.id, value)}
              onWrite={(written) => answer(q.id, 'free', written)}
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
              onDismiss={() => dismiss(call.id)}
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
              `owner-band-${pickupBand(effectivePickup(card), hubNow)}`,
              reminded.has(card.id) ? 'owner-card-remind' : '',
              // 통화 중 손님 취소·주문 현황 손님 취소(전화 주문 현황 티켓 07)·늦춘 시각 거절(티켓 04): 회색
              ['cancelled', 'customer_cancelled', 'declined'].includes(orderOutcome(card)) ? 'owner-card-cancelled' : '',
            ].join(' ')}
          >
            <header className="owner-card-head">
              <span className="owner-pickup">
                <small className="owner-pickup-label">{text.pickup}</small> {effectivePickup(card).pickup_label}
              </span>
              <strong className="owner-total">{card.total_label}</strong>
            </header>
            {orderMeta(card, lang) && <p className="owner-order-meta">{orderMeta(card, lang)}</p>}
            <CardLines lines={card.lines} lang={lang} text={text} />
            {card.owner_check && <p className="owner-check">{text.ownerCheck}</p>}
            <SentTexts texts={card.sms} lang={lang} />
            <OrderDecision
              card={card}
              nowMs={hubNow}
              lang={lang}
              text={text}
              onAccept={(delayMin) => accept(card.id, delayMin)}
              onReject={(body) => reject(card.id, body)}
            />
            <SoldOutMark
              card={card}
              soldOut={state.soldOut}
              lang={lang}
              text={text}
              onUndo={(menuId) => void undoSoldOut(key.current, menuId)}
            />
          </article>
        ))}
      </section>

      {knowledge.length > 0 && (
        <details className="owner-knowledge">
          <summary>
            {text.knowledge}
            {waitingKnowledge > 0 && <span className="owner-knowledge-waiting"> {waitingKnowledge}</span>}
          </summary>
          {knowledge.map((card) => (
            <KnowledgeCardView
              key={card.id}
              card={card}
              lang={lang}
              text={text}
              onPreview={(written) => previewKnowledge(key.current, card.id, written)}
              onSave={(textKo, written) => save(card.id, textKo, written)}
            />
          ))}
        </details>
      )}

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
function SentTexts({ texts, lang }: { texts: CustomerText[] | undefined; lang: OwnerLang }) {
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
 * 손님 질문 카드. 접혀 와서(메뉴·남은 초) 사장님이 눌러 펼치면 질문·버튼이 보이고 열람을 알린다
 * (리뷰 P4). 버튼 하나로 답하거나, 접힌 칸에서 글로 답한다(티켓 07, 손님에게는 번역 문자로만 감).
 * 시간이 지나거나 통화가 끝나도 카드는 남는다(늦은 답은 손님 문자로 간다)
 */
function QuestionCardView({
  question,
  clock,
  lang,
  text,
  onOpen,
  onAnswer,
  onWrite,
}: {
  question: QuestionCard
  /** 남은 초 · 시간 지남 · 통화 끝 (`questionClock`) */
  clock: QuestionClock
  lang: OwnerLang
  text: OwnerText
  /** 사장님이 카드를 눌러 펼쳤다 */
  onOpen: () => void
  onAnswer: (answer: OwnerAnswer) => void
  onWrite: (written: string) => void
}) {
  const view = questionView(question, lang)
  const late = typeof clock !== 'number'
  const [draft, setDraft] = useState('')
  const written = writtenAnswer(draft)
  return (
    <details
      className="owner-card owner-question"
      onToggle={(e) => {
        if (e.currentTarget.open) onOpen()
      }}
    >
      <summary className="owner-card-head owner-question-head">
        <span className="owner-question-title">
          {text.question} · {view.menu}
        </span>
        <span className={`owner-question-left${late ? ' owner-question-late' : ''}`} role="timer">
          {late ? (clock === 'ended' ? text.callEnded : text.late) : text.secondsLeft(clock)}
        </span>
        <span className="owner-question-open">{text.questionOpen}</span>
      </summary>
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
            {answerLabel(question.yes_no, answer, lang)}
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
    </details>
  )
}

/**
 * "손님이 궁금해한 것" 카드 (티켓 13). 사장님 글 → "번역 보기"(한국어 미리보기, 고칠 수 있음) →
 * "저장". 저장 전에는 AI가 쓰지 않는다(HITL). 채운 카드는 저장한 한국어를 보여 준다
 */
function KnowledgeCardView({
  card,
  lang,
  text,
  onPreview,
  onSave,
}: {
  card: KnowledgeCard
  lang: OwnerLang
  text: OwnerText
  onPreview: (written: string) => Promise<KnowledgePreview | null>
  onSave: (textKo: string, written: string) => void
}) {
  const view = knowledgeView(card, lang)
  const [draft, setDraft] = useState('')
  const [preview, setPreview] = useState<KnowledgePreview | null>(null)
  const [korean, setKorean] = useState('')
  const written = writtenAnswer(draft)
  const toSave = writtenAnswer(korean)

  async function translate() {
    if (written === null) return
    const got = await onPreview(written)
    if (!got) return
    setPreview(got)
    setKorean(got.text_ko)
  }

  return (
    <article className={`owner-card owner-knowledge-card owner-knowledge-${card.status}`}>
      <header className="owner-card-head">
        <span className="owner-question-title">
          {view.menu} · {view.kind}
        </span>
        <span className="owner-knowledge-count">{view.count}</span>
      </header>
      {view.question && <p className="owner-question-text">“{view.question}”</p>}
      <p className={view.question ? 'owner-question-ko' : 'owner-question-text'} lang="ko">
        “{view.questionKo}”
      </p>
      {card.status === 'filled' ? (
        <p className="owner-knowledge-saved">
          <span className="owner-knowledge-saved-label">{text.knowledgeSaved}</span>{' '}
          <span lang="ko">{card.answer_ko}</span>
        </p>
      ) : (
        <div className="owner-knowledge-answer">
          <p className="owner-written-hint">{text.knowledgeHint}</p>
          <textarea
            className="owner-written-input"
            value={draft}
            rows={2}
            maxLength={WRITTEN_ANSWER_MAX}
            onChange={(e) => {
              setDraft(e.target.value)
              setPreview(null)
            }}
          />
          <button
            type="button"
            className="owner-btn owner-btn-written"
            disabled={written === null}
            onClick={() => void translate()}
          >
            {text.knowledgeTranslate}
          </button>
          {preview && (
            <>
              <p className="owner-written-hint">
                {preview.translate_failed ? text.knowledgeNoTranslation : text.knowledgePreview}
              </p>
              <textarea
                className="owner-written-input"
                lang="ko"
                value={korean}
                rows={2}
                maxLength={WRITTEN_ANSWER_MAX}
                onChange={(e) => setKorean(e.target.value)}
              />
              <button
                type="button"
                className="owner-btn owner-btn-written"
                disabled={toSave === null}
                onClick={() => toSave !== null && onSave(toSave, preview.text)}
              >
                {text.knowledgeSave}
              </button>
            </>
          )}
        </div>
      )}
    </article>
  )
}
