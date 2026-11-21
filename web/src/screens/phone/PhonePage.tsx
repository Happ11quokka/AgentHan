// Part C 전화 주문 통화 화면. 이 폴더(src/screens/phone/)는 Part C 몫이다.
// WS 클라이언트·마이크 워클릿·재생 스케줄러도 src/api/가 아니라 이 폴더에 둔다.
// 통화가 끝나면 "받은 문자" 카드(4단계 티켓 07): 확정 안내와 사장님의 늦은 답. 통화 뒤 서버가 알린 창(기본 6분) 동안 스트림으로 받는다.
// 통화가 확정 주문으로 끝나면 자막·배지·주문서 대신 주문 현황을 보인다(전화 메뉴·주문 현황 티켓 04, `orderStatus.ts`).
// 주문 현황은 주소(`/phone/orders/<주문 번호>`)와 브라우저 저장소에 남아 새로 고치거나 다시 열어도 보인다. `/phone`을
// 다시 열면 끝나지 않은 주문으로 가고, [새로 전화하기]는 새 통화 화면으로 돌아간다(티켓 06, `orderRoute.ts`).
import { useEffect, useLayoutEffect, useRef, useState, useSyncExternalStore } from 'react'
import { approvalBadge, awaitingText, cancelledByStore } from './approvalBadge.ts'
import { type CallState, canEndSpeech, canInterrupt } from './callState.ts'
import { nearBottom } from './follow.ts'
import { endReasonLabel, pickupLabel } from './format.ts'
import {
  browserOrderMemory,
  initialPhoneView,
  orderPath,
  PHONE_PATH,
  type PhoneView,
  resumedView,
} from './orderRoute.ts'
import { callOrderId, fetchOrderStatus } from './orderStatus.ts'
import OrderStatusPanel from './OrderStatusPanel.tsx'
import { PhoneCall, phoneOptions } from './phoneCall.ts'
import './phone.css'

const PHASE_LABEL: Record<string, string> = {
  listening: '듣는 중',
  thinking: '생각 중',
  speaking: '말하는 중',
  ended: '통화 마무리 중',
}

function statusLabel(s: CallState): string {
  switch (s.status) {
    case 'idle':
      return '전화가 왔어요'
    case 'starting':
      return s.mic ? '연결 중…' : '마이크 켜는 중…'
    case 'live':
      return s.phase ? PHASE_LABEL[s.phase] : '연결됨'
    case 'ended': {
      // 주문으로 끝난 통화: 확정 여부는 아래 주문 현황이 보인다(아직 가게 확인 중일 수 있다, 티켓 04)
      if (callOrderId(s)) return '통화가 끝났어요 · 주문을 넣었어요'
      // 서버가 끝냈으면 이유를 붙인다(`call.ended`, 리뷰 S13). 문자로 넘긴 확인 요청은 아직 확정 전이다
      const why = awaitingText(s) ? '가게 확인 중이에요' : s.endReason ? endReasonLabel(s.endReason) : null
      return why ? `통화가 끝났어요 · ${why}` : '통화가 끝났어요'
    }
    case 'failed':
      return '통화가 끊겼어요'
  }
}

const won = (n: number) => `${n.toLocaleString('ko-KR')}원`

const memory = browserOrderMemory()

/** 주소만 바꾼다(기록을 쌓지 않는다). 개발용 쿼리(`?mic_first=1` 등)는 그대로 둔다 */
function showPath(path: string) {
  history.replaceState(history.state, '', path + location.search)
}

export default function PhonePage() {
  const [view, setView] = useState<PhoneView>(() => initialPhoneView(location.pathname, memory.recall()))
  const [call, setCall] = useState(() => new PhoneCall(phoneOptions(location.search)))
  const state = useSyncExternalStore(call.subscribe, call.getState)

  useEffect(() => {
    call.attach()
    return () => call.detach()
  }, [call])

  // [새로 전화하기]: 남긴 주문 번호를 지우고 새 통화 화면(전화 받기)으로. 지난 통화의 자막·주문서는 버린다
  const newCall = () => {
    memory.forget()
    showPath(PHONE_PATH)
    setCall(new PhoneCall(phoneOptions(location.search)))
    setView({ kind: 'call' })
  }

  // 통화가 주문으로 끝났으면 같은 화면이 그 주문 현황이 된다(티켓 04)
  const placed = callOrderId(state)
  const shown: PhoneView = view.kind === 'call' && placed ? { kind: 'order', orderId: placed } : view
  const shownOrder = shown.kind === 'order' ? shown.orderId : null

  // 주문 현황이면 주소가 주문 현황 주소가 되고 주문 번호를 남긴다(새로 고침·새 탭·`/phone` 다시 열기, 티켓 06).
  // 주문 현황 주소로 바로 연 주문도 남긴다
  useEffect(() => {
    if (!shownOrder) return
    memory.remember(shownOrder)
    showPath(orderPath(shownOrder))
  }, [shownOrder])

  // `/phone`을 다시 열었는데 남긴 주문이 있다: 끝나지 않았으면 주문 현황으로, 아니면 통화 화면으로
  useEffect(() => {
    if (view.kind !== 'resuming') return
    let alive = true
    void fetchOrderStatus(view.orderId).then((action) => {
      if (!alive) return
      const next = resumedView(view.orderId, action, new Date())
      if (next.forget) memory.forget()
      setView(next.view)
    })
    return () => {
      alive = false
    }
  }, [view])

  // 자막 따라가기(5단계 티켓 09): 맨 아래를 보고 있으면 새 글이 올 때 같이 내려간다
  const captionsRef = useRef<HTMLElement>(null)
  const follow = useRef(true)
  useLayoutEffect(() => {
    const el = captionsRef.current
    if (el && follow.current) el.scrollTop = el.scrollHeight
  }, [state.captions])

  const inCall = state.status === 'starting' || state.status === 'live'
  const cart = state.order?.cart ?? state.cart
  const total = state.order?.total ?? state.total
  const badge = approvalBadge(state)
  const notice = (
    // AI 응대·개인정보 안내 한 줄 (6단계). 보관 기한은 서버 `PHONE_LOG_KEEP_DAYS` 기본값과 같다
    <p className="phone-notice">
      이 전화는 <strong>AI가 받아요</strong>. 말씀은 주문 처리를 위해 네이버클라우드(CLOVA)에서 글로 바뀌고,
      기록은 14일 뒤 지워요. 체험용이라 실제 결제는 없어요. 이름·전화번호 같은 개인정보는 말하지 말아 주세요.
    </p>
  )

  if (shown.kind !== 'call') {
    // 주문 현황(주문 번호가 주소에 있다)과 `/phone`에서 남긴 주문을 확인하는 잠깐
    const fromCall = shown.kind === 'order' && placed === shown.orderId
    return (
      <main className="phone">
        <header className="phone-head">
          <p className="phone-store">가게 전화 주문</p>
          <p className={`phone-status phone-status-${fromCall ? state.status : 'idle'}`} role="status">
            {fromCall ? statusLabel(state) : '주문 현황'}
          </p>
        </header>
        {shown.kind === 'order' ? (
          <OrderStatusPanel key={shown.orderId} orderId={shown.orderId} />
        ) : (
          <p className="phone-empty" role="status">
            주문 현황을 확인하고 있어요.
          </p>
        )}
        {shown.kind === 'order' && (
          <div className="phone-actions">
            <button type="button" className="phone-btn phone-btn-answer" onClick={newCall}>
              새로 전화하기
            </button>
          </div>
        )}
        {notice}
      </main>
    )
  }

  return (
    <main className="phone">
      <header className="phone-head">
        <p className="phone-store">가게 전화 주문</p>
        <p className={`phone-status phone-status-${state.status} phone-phase-${state.phase ?? 'none'}`} role="status">
          {statusLabel(state)}
        </p>
      </header>

      {state.audioBlocked && inCall && (
        <button type="button" className="phone-btn phone-btn-warn" onClick={() => call.resumeAudio()}>
          소리가 멈췄어요 · 눌러서 다시 켜기
        </button>
      )}

      {state.slowLink && inCall && (
        <p className="phone-warn" role="status">
          회선이 느려요. 와이파이나 신호가 좋은 곳으로 옮겨 주세요.
        </p>
      )}

      {state.error && (
        <div className="phone-error" role="alert">
          <p>{state.error.message}</p>
        </div>
      )}

      <section
        ref={captionsRef}
        className="phone-captions"
        aria-label="자막"
        aria-live="polite"
        onScroll={(e) => {
          const el = e.currentTarget
          follow.current = nearBottom(el.scrollHeight, el.scrollTop, el.clientHeight)
        }}
      >
        {state.captions.length === 0 && <p className="phone-empty">통화가 연결되면 대화가 여기에 보여요.</p>}
        {state.captions.map((c, i) => (
          <p key={i} className={`phone-line phone-line-${c.who}`}>
            <span className="phone-who">{c.who === 'bot' ? 'AI' : '손님'}</span>
            {c.text}
          </p>
        ))}
      </section>

      {badge && (
        // 사장님 확인 요청 상태와 결과 (주문 승인 티켓 09)
        <section className={`phone-approval phone-approval-${badge.tone}`} aria-label="사장님 확인" role="status">
          <strong>{badge.label}</strong>
          {badge.detail && <p>{badge.detail}</p>}
        </section>
      )}

      {(state.order || cart.length > 0) && (
        <section className={`phone-order ${state.order ? 'phone-order-final' : ''}`} aria-label="주문서">
          <h2>
            {state.order
              ? state.order.order.status === 'confirmed'
                ? awaitingText(state)
                  ? '가게 확인 중'
                  : cancelledByStore(state) // 문자로 넘긴 뒤 거절·자동 취소 (티켓 05)
                    ? '주문 취소'
                    : '주문 확정'
                : '사장님께 넘김'
              : '담은 메뉴'}
          </h2>
          <ul>
            {cart.map((line) => (
              <li key={line.menu_id + line.options.join(',')}>
                <span>
                  {line.name} × {line.qty}
                  {line.options.length > 0 && <small> ({line.options.join(', ')})</small>}
                  {line.request && <small> · {line.request}</small>}
                </span>
                <span>{won(line.subtotal)}</span>
              </li>
            ))}
          </ul>
          <p className="phone-total">
            <span>합계</span>
            <strong>{won(total)}</strong>
          </p>
          {state.order?.order.pickup_time && (
            <p className="phone-pickup">
              픽업 {pickupLabel(state.order.order.pickup_time)}
            </p>
          )}
        </section>
      )}

      {state.texts.length > 0 && (
        // 통화 끝 안내와 사장님의 늦은 답(티켓 07). 실제 문자는 보내지 않는다
        <section className="phone-texts" aria-label="받은 문자" aria-live="polite">
          <h2>받은 문자</h2>
          <ul>
            {state.texts.map((t) => (
              <li key={t.id} className="phone-text">
                <time dateTime={t.sent_at}>{t.sent_label}</time>
                <p>{t.text}</p>
              </li>
            ))}
          </ul>
          <p className="phone-texts-note">시연용 미리보기예요. 실제 문자는 보내지 않아요.</p>
        </section>
      )}

      <div className="phone-actions">
        {inCall ? (
          <>
            {canInterrupt(state) && (
              // 봇이 말하는 동안만 보인다. 최종 복창·마무리·사장님 연결 안내 중에는 서버가 막는다(티켓 05)
              <button type="button" className="phone-btn phone-btn-interrupt" onClick={() => call.interrupt()}>
                말 끊기
              </button>
            )}
            <button
              type="button"
              className="phone-btn"
              disabled={!canEndSpeech(state)}
              onClick={() => call.endOfSpeech()}
            >
              말 끝
            </button>
            <button type="button" className="phone-btn phone-btn-hangup" onClick={() => call.hangup()}>
              끊기
            </button>
          </>
        ) : state.status === 'failed' && state.error && !state.error.retry ? null : (
          <button type="button" className="phone-btn phone-btn-answer" onClick={() => call.dial()}>
            {state.status === 'idle' ? '전화 받기' : '다시 걸기'}
          </button>
        )}
      </div>

      {notice}

      {state.mic && (
        <details className="phone-mic">
          <summary>마이크 설정값</summary>
          <dl>
            {[
              'echoCancellation',
              'noiseSuppression',
              'autoGainControl',
              'sampleRate',
              'channelCount',
              'contextSampleRate',
              'capture',
            ].map((k) => (
              <div key={k}>
                <dt>{k}</dt>
                <dd>{String(state.mic?.[k] ?? '—')}</dd>
              </div>
            ))}
            <div>
              <dt>음성 끼어들기</dt>
              <dd>{state.bargeIn ?? 'off'}</dd>
            </div>
          </dl>
        </details>
      )}
    </main>
  )
}
