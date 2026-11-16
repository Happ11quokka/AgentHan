import { useState } from "react";
import { ratingText, REPLY_TEXT, reviewDate, unansweredText } from "../labels";
import type { Action, ReviewRow, ReviewsView } from "../types";
import type { Loaded } from "../useLoad";
import { LoadFailed } from "./LoadFailed";

function Stars({ rating }: { rating: number }) {
  return (
    <span className="stars" aria-label={`별점 ${rating}점`}>
      {"★★★★★".slice(0, rating)}
      <span className="stars-off">{"★★★★★".slice(rating)}</span>
    </span>
  );
}

function Review({ review, busy, onAction }: { review: ReviewRow; busy: boolean; onAction: (action: Action) => void }) {
  const [showOriginal, setShowOriginal] = useState(false);
  return (
    <li className="review" data-replied={review.has_owner_reply}>
      <div className="review-head">
        <Stars rating={review.rating} />
        <time>{reviewDate(review.visit_date)}</time>
        <span className="chip" data-tone={review.has_owner_reply ? "same" : "need"}>
          {review.has_owner_reply ? REPLY_TEXT.done : REPLY_TEXT.todo}
        </span>
      </div>
      <p className="review-text">{review.text}</p>
      {review.original && (
        <>
          <button type="button" className="link" aria-expanded={showOriginal} onClick={() => setShowOriginal((s) => !s)}>
            {showOriginal ? "원문 접기" : "원문 보기"}
          </button>
          {showOriginal && <p className="review-original">{review.original}</p>}
        </>
      )}
      <button type="button" className="ghost small" disabled={busy} onClick={() => onAction({ action_id: "review.draft_reply", args: { review_id: review.id }, label: "답글 초안 받기" })}>
        답글 초안 받기
      </button>
    </li>
  );
}

type Props = { loaded: Loaded<ReviewsView>; busy: boolean; onAction: (action: Action) => void };

export function ReviewsTab({ loaded, busy, onAction }: Props) {
  const { data, failed, reload } = loaded;
  if (failed) return <LoadFailed message="리뷰를 불러오지 못했어요." onRetry={reload} />;
  if (!data) return <p className="loading">불러오는 중…</p>;
  const { summary, reviews } = data;
  return (
    <div className="reviews">
      <section className="review-summary">
        <div>
          <strong className="big">{ratingText(summary.average_rating)}</strong>
          <span>평균 별점 · 리뷰 {summary.count}개</span>
        </div>
        <p data-todo={summary.unanswered > 0}>{unansweredText(summary.unanswered)}</p>
      </section>
      {reviews.length === 0 ? (
        <p className="empty">아직 리뷰가 없어요.</p>
      ) : (
        <ul className="review-list">
          {reviews.map((r) => (
            <Review key={r.id} review={r} busy={busy} onAction={onAction} />
          ))}
        </ul>
      )}
    </div>
  );
}
