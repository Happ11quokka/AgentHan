import { approval, BUNDLED_TEXT, draftStatus, featureName, noteBadges, pendingLines, REVIEW_CARD_TEXT, reviewDate, won } from "../labels";
import type { Action, Pending, Place, ReviewRow } from "../types";
import { Stars } from "./ReviewsTab";

type Props = {
  place: Place;
  pending: Pending;
  /** 다른 카드와 함께 말풍선의 [모두 저장]·[모두 취소]로만 저장한다(카드 버튼 없음). */
  bundled?: boolean;
  /** 리뷰 답글 카드가 가리키는 원래 리뷰(못 찾으면 없음). */
  review?: ReviewRow;
  onAnswer: (text: string) => void;
  onAction: (action: Action) => void;
};

/** 리뷰 답글 카드 본문: 원래 리뷰(회색 박스)와 올릴 답글. 답글 수정은 대화로 한다. */
function ReviewReply({ reply, review }: { reply: string; review?: ReviewRow }) {
  return (
    <div className="review-reply">
      {review && (
        <figure className="review-source" aria-label={REVIEW_CARD_TEXT.source}>
          <div className="review-head">
            <Stars rating={review.rating} />
            <time>{reviewDate(review.visit_date)}</time>
          </div>
          <blockquote>{review.text}</blockquote>
        </figure>
      )}
      <p className="reply-body" aria-label={REVIEW_CARD_TEXT.reply}>
        {reply}
      </p>
      <p className="reply-hint">{REVIEW_CARD_TEXT.hint}</p>
    </div>
  );
}

/** 저장 전 변경. [저장]·[취소]는 비서에게 그 답을 말한 것과 같다. */
export function ApprovalCard({ place, pending, bundled = false, review, onAnswer, onAction }: Props) {
  const photo = pending.kind === "photo";
  const isReview = pending.kind === "review";
  const { blocked, answer, need } = approval(pending);
  const approveLabel = isReview ? REVIEW_CARD_TEXT.approve : "저장";
  const act = (approve: boolean) => {
    if (photo) return onAnswer(approve ? answer : "취소");
    onAction({ action_id: approve ? "task.approve" : "task.cancel", label: approve ? approveLabel : "취소", args: approve ? { task_id: pending.task_id, version: pending.version } : { task_id: pending.task_id } });
  };
  const title = isReview ? REVIEW_CARD_TEXT.title : photo ? "사진에서 읽은 메뉴" : `${featureName(pending.feature)} 바꾸기`;

  return (
    <section className="approval" aria-label="저장 전 확인">
      <header>
        <span className="approval-tag">저장 전 확인</span>
        <strong>{title}</strong>
      </header>

      {photo ? (
        <ol className="drafts">
          {(pending.drafts ?? []).map((d, i) => {
            const status = draftStatus(d);
            return (
              <li key={d.photo_url ?? `${i}-${d.name_ko}`} className="draft">
                {d.photo_url ? <img src={d.photo_url} alt="" /> : <span className="draft-no-photo">{i + 1}</span>}
                <div className="draft-body">
                  <div className="draft-name">
                    <strong>{d.name_ko || "이름 없음"}</strong>
                    {d.name_native && <small>{d.name_native}</small>}
                  </div>
                  <div className="draft-meta">
                    {d.price !== null && <span className="draft-price">{won(d.price)}</span>}
                    <span className="chip" data-tone={status.tone}>
                      {status.text}
                    </span>
                    {noteBadges(d.notes).map((b) => (
                      <span key={b} className="chip" data-tone="note">
                        {b}
                      </span>
                    ))}
                  </div>
                </div>
              </li>
            );
          })}
        </ol>
      ) : isReview ? (
        <ReviewReply reply={pending.reply ?? ""} review={review} />
      ) : (
        <ul className="changes">
          {pendingLines(place, pending).map((line, i) => (
            <li key={`${i}-${line}`}>{line}</li>
          ))}
        </ul>
      )}

      {need && <p className="approval-question">{need}</p>}

      {bundled ? (
        <p className="approval-bundled">{BUNDLED_TEXT}</p>
      ) : (
        <footer>
          <button type="button" className="ghost" onClick={() => act(false)}>
            {isReview ? REVIEW_CARD_TEXT.cancel : "취소"}
          </button>
          <button type="button" className="primary" disabled={blocked || (!photo && pending.version === undefined)} onClick={() => act(true)}>
            {approveLabel}
          </button>
        </footer>
      )}
    </section>
  );
}
