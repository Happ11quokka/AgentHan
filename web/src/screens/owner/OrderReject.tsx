// 주문 카드의 거절 (주문 승인 티켓 05). 확인 대기 카드의 [거절] → 한 단계 아래 사유 시트(재료 소진 /
// 바빠서 못 맞춤 / 영업·가게 사정 / 직접 쓰기) → 보내기. 재료 소진은 티켓 06 전까지 막혀 있다.
// 직접 쓰기 글은 손님에게 읽지 않고 번역해 문자로만 간다. 거절한 카드는 회색에 "거절 · 사유"와,
// 직접 쓴 글이면 그 글과 손님에게 간 한국어 번역을 남긴다. 고를 수 있는 사유·본문은 `rejectSheet.ts`.
import { useState } from 'react'
import { type OrderCard, type RejectReason, WRITTEN_ANSWER_MAX } from './ownerState.ts'
import { type OwnerLang, type OwnerText, rejectedView } from './ownerText.ts'
import { REJECT_REASONS, type RejectBody, reasonOpen, rejectBody } from './rejectSheet.ts'

/** [거절] 버튼. 누르면 그 자리에서 사유 시트를 펼친다(돌아가기로 접는다) */
export function RejectButton({ text, onReject }: { text: OwnerText; onReject: (body: RejectBody) => void }) {
  const [open, setOpen] = useState(false)
  if (!open) {
    return (
      <button type="button" className="owner-btn owner-btn-reject" onClick={() => setOpen(true)}>
        {text.reject}
      </button>
    )
  }
  return <RejectSheet text={text} onSend={onReject} onBack={() => setOpen(false)} />
}

function RejectSheet({
  text,
  onSend,
  onBack,
}: {
  text: OwnerText
  onSend: (body: RejectBody) => void
  onBack: () => void
}) {
  const [reason, setReason] = useState<RejectReason | null>(null)
  const [draft, setDraft] = useState('')
  const body = rejectBody(reason, draft)
  return (
    <div className="owner-reject" role="group" aria-label={text.rejectTitle}>
      <p className="owner-reject-title">{text.rejectTitle}</p>
      <div className="owner-reject-reasons">
        {REJECT_REASONS.map((r) => (
          <button
            key={r}
            type="button"
            className={`owner-btn owner-btn-reason${reason === r ? ' owner-btn-picked' : ''}`}
            aria-pressed={reason === r}
            disabled={!reasonOpen(r)}
            onClick={() => setReason(r)}
          >
            {text.rejectReason[r]}
            {!reasonOpen(r) && <small className="owner-reject-soon"> · {text.rejectSoon}</small>}
          </button>
        ))}
      </div>
      {reason === 'free' && (
        <>
          <p className="owner-written-hint">{text.writtenHint}</p>
          <textarea
            className="owner-written-input"
            value={draft}
            rows={2}
            maxLength={WRITTEN_ANSWER_MAX}
            onChange={(e) => setDraft(e.target.value)}
          />
        </>
      )}
      <div className="owner-reject-actions">
        <button type="button" className="owner-btn owner-btn-dismiss" onClick={onBack}>
          {text.rejectBack}
        </button>
        <button
          type="button"
          className="owner-btn owner-btn-reject"
          disabled={body === null}
          onClick={() => body !== null && onSend(body)}
        >
          {text.rejectSend}
        </button>
      </div>
    </div>
  )
}

/** 거절한 카드의 결과 줄: "거절 · 사유", 직접 쓴 글과 손님에게 간 한국어 번역 */
export function RejectedResult({ card, lang }: { card: OrderCard; lang: OwnerLang }) {
  const view = rejectedView(card, lang)
  return (
    <div className="owner-rejected">
      <p className="owner-rejected-label">{view.label}</p>
      {view.written && <p className="owner-rejected-written">“{view.written}”</p>}
      {view.writtenKo && (
        <p className="owner-rejected-ko" lang="ko">
          “{view.writtenKo}”
        </p>
      )}
    </div>
  )
}
