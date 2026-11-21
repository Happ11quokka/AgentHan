// 주문 카드의 거절 (주문 승인 티켓 05·06). 확인 대기 카드의 [거절] → 한 단계 아래 사유 시트(재료 소진
// / 바빠서 못 맞춤 / 영업·가게 사정 / 직접 쓰기) → 보내기. 재료 소진은 주문 메뉴 중 떨어진 것을 고른다
// (여러 개 가능, 티켓 06). 직접 쓰기 글은 손님에게 읽지 않고 번역해 문자로만 간다. 거절한 카드는 회색에
// "거절 · 사유"와, 직접 쓴 글이면 그 글과 손님에게 간 한국어 번역을 남긴다. 재료 소진으로 품절을 건
// 카드에는 "품절로 바꿨어요"와 메뉴마다 [되돌리기]가 남는다(`SoldOutMark`). 본문 규칙은 `rejectSheet.ts`.
import { useState } from 'react'
import { type OrderCard, type OrderCardLine, type RejectReason, type SoldOutItem, WRITTEN_ANSWER_MAX } from './ownerState.ts'
import { type OwnerLang, type OwnerText, lineView, rejectedView, soldOutMarkView } from './ownerText.ts'
import { REJECT_REASONS, type RejectBody, rejectBody, stockOutChoices, togglePicked } from './rejectSheet.ts'

/** [거절] 버튼. 누르면 그 자리에서 사유 시트를 펼친다(돌아가기로 접는다). `lines`는 재료 소진에서 고를 주문 줄 */
export function RejectButton({
  text,
  lang,
  lines,
  onReject,
}: {
  text: OwnerText
  lang: OwnerLang
  lines: readonly OrderCardLine[]
  onReject: (body: RejectBody) => void
}) {
  const [open, setOpen] = useState(false)
  if (!open) {
    return (
      <button type="button" className="owner-btn owner-btn-reject" onClick={() => setOpen(true)}>
        {text.reject}
      </button>
    )
  }
  return <RejectSheet text={text} lang={lang} lines={lines} onSend={onReject} onBack={() => setOpen(false)} />
}

function RejectSheet({
  text,
  lang,
  lines,
  onSend,
  onBack,
}: {
  text: OwnerText
  lang: OwnerLang
  lines: readonly OrderCardLine[]
  onSend: (body: RejectBody) => void
  onBack: () => void
}) {
  const [reason, setReason] = useState<RejectReason | null>(null)
  const [draft, setDraft] = useState('')
  const [picked, setPicked] = useState<string[]>([])
  const body = rejectBody(reason, draft, picked)
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
            onClick={() => setReason(r)}
          >
            {text.rejectReason[r]}
          </button>
        ))}
      </div>
      {reason === 'out_of_stock' && (
        <>
          <p className="owner-written-hint">{text.stockOutPick}</p>
          <div className="owner-stockout-menus">
            {stockOutChoices(lines).map((line) => (
              <label key={line.menu_id} className="owner-stockout-menu">
                <input
                  type="checkbox"
                  checked={picked.includes(line.menu_id)}
                  onChange={() => setPicked(togglePicked(picked, line.menu_id, lines))}
                />{' '}
                {lineView(line, lang).name}
              </label>
            ))}
          </div>
        </>
      )}
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

/**
 * 카드의 품절 표시 (티켓 06): "품절로 바꿨어요"와 메뉴마다 [되돌리기]. 이미 되돌렸거나 날짜가 바뀌어
 * 오늘 품절이 아니면 "되돌렸어요"만. 그 카드에서 품절을 건 적이 없으면 아무것도 안 그린다
 */
export function SoldOutMark({
  card,
  soldOut,
  lang,
  text,
  onUndo,
}: {
  card: OrderCard
  soldOut: readonly SoldOutItem[]
  lang: OwnerLang
  text: OwnerText
  onUndo: (menuId: string) => void
}) {
  const view = soldOutMarkView(card, soldOut, lang)
  if (view === null) return null
  return (
    <div className="owner-soldout">
      <p className="owner-soldout-label">{view.label}</p>
      <ul className="owner-soldout-items">
        {view.items.map((item) => (
          <li key={item.menuId}>
            {item.name}{' '}
            {item.still ? (
              <button type="button" className="owner-btn owner-btn-dismiss" onClick={() => onUndo(item.menuId)}>
                {text.soldOutUndo}
              </button>
            ) : (
              <small className="owner-soldout-undone">{text.soldOutUndone}</small>
            )}
          </li>
        ))}
      </ul>
    </div>
  )
}
