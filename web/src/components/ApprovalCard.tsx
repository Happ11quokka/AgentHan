import { useState } from "react";
import { approval, BUNDLED_TEXT, draftStatus, featureName, noteBadges, pendingLines, REVIEW_CARD_TEXT, reviewDate, won } from "../labels";
import type { Action, DraftRow, Pending, Place, ReviewRow } from "../types";
import { Stars } from "./ReviewsTab";

/** 카드의 이름 입력칸이 비서에게 보내는 말. 서버(owner_assistant.FOOD_NAME_INPUT)가 모델 없이 이름으로 쓴다. */
const foodNameAnswer = (name: string) => `메뉴 이름: ${name.trim()}`;
/** 입력한 이름이 기존 메뉴와 맞을 때 붙이지 않고 새 메뉴로 올리는 말(owner_assistant.NEW_MENU). */
const NEW_MENU_ANSWER = "새 메뉴로 올려 줘";

/**
 * 음식 사진 초안의 메뉴 이름 입력칸. 사진만으로는 이름을 맞히기 어려워 사장님이 입력한다(#93).
 * AI 추측 이름은 확정값이 아니라 자리표시자로만 보인다.
 */
function FoodName({ draft, onAnswer }: { draft: DraftRow; onAnswer: (text: string) => void }) {
  const [name, setName] = useState(draft.name_ko);
  const ready = name.trim() !== "" && name.trim() !== draft.name_ko.trim();
  return (
    <form
      className="draft-name-input"
      onSubmit={(e) => {
        e.preventDefault();
        if (ready) onAnswer(foodNameAnswer(name));
      }}
    >
      <input
        aria-label="메뉴 이름"
        value={name}
        placeholder={draft.name_guess ? `메뉴 이름 (AI 추측: ${draft.name_guess})` : "메뉴 이름"}
        onChange={(e) => setName(e.target.value)}
      />
      <button type="submit" className="ghost" disabled={!ready}>
        이름 넣기
      </button>
    </form>
  );
}

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
  const drafts = pending.drafts ?? [];
  // 사장님이 입력한 이름이 기존 메뉴와 맞았다: 그 메뉴에 붙일지, 새 메뉴로 올릴지 고른다.
  const attachTo = photo && drafts.length === 1 && drafts[0].is_food && drafts[0].change === "photo_attach" ? drafts[0].menu_name : null;
  const approveLabel = isReview ? REVIEW_CARD_TEXT.approve : attachTo ? `${attachTo}에 붙이기` : "저장";
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
          {drafts.map((d, i) => {
            const status = draftStatus(d);
            return (
              <li key={d.photo_url ?? `${i}-${d.name_ko}`} className="draft">
                {d.photo_url ? <img src={d.photo_url} alt="" /> : <span className="draft-no-photo">{i + 1}</span>}
                <div className="draft-body">
                  {d.is_food ? (
                    <FoodName key={d.name_ko} draft={d} onAnswer={onAnswer} />
                  ) : (
                    <div className="draft-name">
                      <strong>{d.name_ko || "이름 없음"}</strong>
                      {d.name_native && <small>{d.name_native}</small>}
                    </div>
                  )}
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
      {attachTo && <p className="approval-question">기존 메뉴 {attachTo}에 이 사진을 붙일까요?</p>}

      {bundled ? (
        <p className="approval-bundled">{BUNDLED_TEXT}</p>
      ) : (
        <footer>
          <button type="button" className="ghost" onClick={() => act(false)}>
            {isReview ? REVIEW_CARD_TEXT.cancel : "취소"}
          </button>
          {attachTo && (
            <button type="button" className="ghost" onClick={() => onAnswer(NEW_MENU_ANSWER)}>
              새 메뉴로 올리기
            </button>
          )}
          <button type="button" className="primary" disabled={blocked || (!photo && pending.version === undefined)} onClick={() => act(true)}>
            {approveLabel}
          </button>
        </footer>
      )}
    </section>
  );
}
