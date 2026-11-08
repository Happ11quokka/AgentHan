// Part C 전화 주문 통화 화면. 이 폴더(src/screens/phone/)는 Part C 몫이다.
// WS 클라이언트·마이크 워클릿·재생 스케줄러도 src/api/가 아니라 이 폴더에 둔다.
import { useEffect, useState, useSyncExternalStore } from 'react'
import { type CallState, canEndSpeech, canInterrupt } from './callState.ts'
import { pickupLabel } from './format.ts'
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
    case 'ended':
      return '통화가 끝났어요'
    case 'failed':
      return '통화가 끊겼어요'
  }
}

const won = (n: number) => `${n.toLocaleString('ko-KR')}원`

export default function PhonePage() {
  const [call] = useState(() => new PhoneCall(phoneOptions(location.search)))
  const state = useSyncExternalStore(call.subscribe, call.getState)

  useEffect(() => {
    call.attach()
    return () => call.detach()
  }, [call])

  const inCall = state.status === 'starting' || state.status === 'live'
  const cart = state.order?.cart ?? state.cart
  const total = state.order?.total ?? state.total

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

      <section className="phone-captions" aria-label="자막" aria-live="polite">
        {state.captions.length === 0 && <p className="phone-empty">통화가 연결되면 대화가 여기에 보여요.</p>}
        {state.captions.map((c, i) => (
          <p key={i} className={`phone-line phone-line-${c.who}`}>
            <span className="phone-who">{c.who === 'bot' ? 'AI' : '손님'}</span>
            {c.text}
          </p>
        ))}
      </section>

      {(state.order || cart.length > 0) && (
        <section className={`phone-order ${state.order ? 'phone-order-final' : ''}`} aria-label="주문서">
          <h2>
            {state.order
              ? state.order.order.status === 'confirmed'
                ? '주문 확정'
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
          </dl>
        </details>
      )}
    </main>
  )
}
