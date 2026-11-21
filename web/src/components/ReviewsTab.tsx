import { useEffect, useState } from "react";
import {
  draftChip,
  INBOX_TEXT,
  INBOX_WORKING_TEXT,
  issuesText,
  preparingText,
  ratingText,
  REDRAFT_HINTS,
  REPORT_TEXT,
  replyErrorText,
  reviewDate,
  unansweredText,
  waitingText,
} from "../labels";
import type { ReplyError, ReplyOp, ReplyOpName, ReviewReport, ReviewRow, ReviewsView } from "../types";
import type { Loaded } from "../useLoad";
import { LoadFailed } from "./LoadFailed";

export function Stars({ rating }: { rating: number | null }) {
  if (rating === null) return null;
  return (
    <span className="stars" aria-label={`별점 ${rating}점`}>
      {"★★★★★".slice(0, rating)}
      <span className="stars-off">{"★★★★★".slice(rating)}</span>
    </span>
  );
}

type Filter = "waiting" | "all";

/** 별점 1~2점에 아직 답글이 없으면 맨 위로 올리고 '꼭 읽어 보세요'를 붙인다. */
const mustRead = (r: ReviewRow) => r.rating !== null && r.rating <= 2 && !r.has_owner_reply;
/** 사장님이 고를 초안이 있는 카드(바로 고쳐 올리기·건너뛰기). */
const decidable = (r: ReviewRow) => !r.has_owner_reply && Boolean(r.draft?.text) && r.draft?.status !== "failed";
/** [기다림]에 보일 카드: 답글 없는 리뷰(건너뛴 것 빼고)와 이번에 올린 답글. */
const inWaiting = (r: ReviewRow) =>
  (!r.has_owner_reply && r.draft?.status !== "skipped") || r.draft?.status === "posted";

/** 카드 버튼 하나를 서버에 보낸다. 실패하면 카드에 보일 이유, 성공하면 null. */
export type OnReply = (review: ReviewRow, op: ReplyOp) => Promise<ReplyError | null>;

type ReviewProps = {
  review: ReviewRow;
  preparing: boolean;
  focused: boolean;
  onReply: OnReply;
};

function Review({ review, preparing, focused, onReply }: ReviewProps) {
  const [showOriginal, setShowOriginal] = useState(false);
  // 사장님이 입력칸에서 고친 글. null이면 초안 그대로 보인다(새 초안이 오면 따라 바뀐다).
  const [edited, setEdited] = useState<string | null>(null);
  // 카드 단위 로딩·오류: 대화 입력(busy)은 잠그지 않는다.
  const [working, setWorking] = useState<ReplyOpName | null>(null);
  const [error, setError] = useState<ReplyError | null>(null);
  const { draft } = review;
  const chip = draftChip(review);
  const reply = async (op: ReplyOp) => {
    if (working) return;
    setWorking(op.op);
    setError(null);
    const failed = await onReply(review, op);
    setWorking(null);
    setError(failed);
    // 다시 쓰기는 사장님이 새 초안을 달라고 한 것이라 고치던 글보다 새 초안을 보인다.
    if (!failed && op.op !== "skip") setEdited(null);
  };
  const text = edited ?? draft?.text ?? "";
  const trimmed = text.trim();
  const changed = trimmed !== (draft?.text ?? "").trim();
  const open = decidable(review);
  const postedText = review.has_owner_reply && draft?.status === "posted" ? draft.text : null;
  const errorLine = error && (
    <p className="inbox-error" role="alert">
      {replyErrorText(error)}
    </p>
  );
  const workingLine = working && (
    <p className="inbox-working" role="status">
      {INBOX_WORKING_TEXT[working]}
    </p>
  );

  return (
    <li
      className="review"
      id={`review-${review.id}`}
      data-replied={review.has_owner_reply}
      data-must-read={mustRead(review)}
      data-focused={focused || undefined}
      aria-busy={Boolean(working)}
    >
      <div className="review-head">
        <Stars rating={review.rating} />
        <time>{reviewDate(review.visit_date)}</time>
        {mustRead(review) && (
          <span className="chip must-read" data-tone="need">
            {INBOX_TEXT.mustRead}
          </span>
        )}
        <span className="chip" data-tone={chip.tone}>
          {chip.text}
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

      {postedText && (
        <section className="inbox-draft" data-status="posted" aria-label={INBOX_TEXT.posted}>
          <h4>{INBOX_TEXT.posted}</h4>
          <p>{postedText}</p>
        </section>
      )}

      {open && draft && (
        <section className="inbox-draft" data-status={draft.status} aria-label={INBOX_TEXT.draft}>
          <h4>{INBOX_TEXT.draft}</h4>
          <textarea aria-label={INBOX_TEXT.editBox} value={text} rows={4} onChange={(e) => setEdited(e.target.value)} />
          {draft.status === "needs_check" && draft.issues.length > 0 && <p className="inbox-issues">{issuesText(draft.issues)}</p>}
          <div className="inbox-redraft" role="group" aria-label={INBOX_TEXT.redraft}>
            {REDRAFT_HINTS.map(([hint, label]) => (
              <button key={hint} type="button" className="chip-button" disabled={Boolean(working)} onClick={() => void reply({ op: "redraft", hint })}>
                {label}
              </button>
            ))}
          </div>
          <div className="inbox-buttons">
            <button
              type="button"
              className="primary"
              disabled={Boolean(working) || !trimmed || (!changed && draft.status === "needs_check")}
              onClick={() => void reply(changed ? { op: "post", text: trimmed } : { op: "post" })}
            >
              {INBOX_TEXT.post}
            </button>
            {draft.status !== "skipped" && (
              <button type="button" className="ghost" disabled={Boolean(working)} onClick={() => void reply({ op: "skip" })}>
                {INBOX_TEXT.skip}
              </button>
            )}
          </div>
          {workingLine}
          {errorLine}
        </section>
      )}
      {!open && errorLine}

      {/* 초안 받기는 대화 턴이 아니라 대기함 경로(op: draft)다: 채팅에는 아무것도 더하지 않는다. */}
      {!open && working === "draft" && (
        <p className="inbox-writing" role="status">
          {INBOX_WORKING_TEXT.draft}
        </p>
      )}
      {!open && !working && !review.has_owner_reply && !draft && preparing && <p className="inbox-writing">{INBOX_TEXT.writing}</p>}
      {!open && !working && !review.has_owner_reply && !(preparing && !draft) && (
        <button type="button" className="ghost small" onClick={() => void reply({ op: "draft" })}>
          {INBOX_TEXT.getDraft}
        </button>
      )}
    </li>
  );
}

/** 정기 보고 줄: 지금 설정 한 줄과 [지금 받아 보기](설정과 상관없이 보고 한 회분을 대화로). */
function ReportRoutine({ report, busy, onRun }: { report: ReviewReport | null; busy: boolean; onRun: () => Promise<string | null> }) {
  const [running, setRunning] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const run = async () => {
    if (running) return;
    setRunning(true);
    setError(null);
    setError(await onRun());
    setRunning(false);
  };
  const enabled = Boolean(report?.enabled);
  return (
    <section className="report-routine" aria-label={REPORT_TEXT.section}>
      <div>
        <p data-on={enabled}>{report?.text ?? REPORT_TEXT.off}</p>
        {!enabled && <p className="report-howto">{REPORT_TEXT.howTo}</p>}
      </div>
      <button type="button" className="ghost small" disabled={busy || running} onClick={() => void run()}>
        {running ? REPORT_TEXT.running : REPORT_TEXT.runNow}
      </button>
      {error && (
        <p className="inbox-error" role="alert">
          {error}
        </p>
      )}
    </section>
  );
}

type Props = {
  loaded: Loaded<ReviewsView>;
  busy: boolean;
  onReply: OnReply;
  /** 채팅 알림의 [리뷰 탭에서 보기]가 가리키는 카드. 그 카드로 스크롤한다. */
  focus?: { id: string; at: number } | null;
  /** 개발 모드에서만: [새 리뷰 도착 (데모)]. */
  onSimulate?: () => void;
  /** 리뷰 정기 보고 설정 한 줄(#98). */
  report?: ReviewReport | null;
  /** [지금 받아 보기]. 없으면 정기 보고 줄도 없다. 실패하면 보일 문장을 돌려준다. */
  onRunReport?: () => Promise<string | null>;
};

export function ReviewsTab({ loaded, busy, onReply, focus, onSimulate, report = null, onRunReport }: Props) {
  const { data, failed, reload } = loaded;
  const [chosen, setChosen] = useState<Filter | null>(null);
  const focusId = focus?.id;
  const focusAt = focus?.at;
  const hasData = Boolean(data);
  // 가리킨 카드가 지금 필터에 없으면 [전체]로 바꾼 뒤 그 카드로 스크롤한다.
  useEffect(() => {
    if (!focusId || !data) return;
    const row = data.reviews.find((r) => r.id === focusId);
    if (row && !inWaiting(row)) setChosen("all");
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [focusId, focusAt, hasData]);
  useEffect(() => {
    if (!focusId) return;
    document.getElementById(`review-${focusId}`)?.scrollIntoView?.({ block: "center", behavior: "smooth" });
  }, [focusId, focusAt, hasData, chosen]);
  if (failed) return <LoadFailed message="리뷰를 불러오지 못했어요." onRetry={reload} />;
  if (!data) return <p className="loading">불러오는 중…</p>;
  const { summary, reviews } = data;
  // 사장님이 고르기 전에는 기다리는 게 있을 때만 [기다림]으로 연다.
  const filter: Filter = chosen ?? (summary.waiting > 0 || summary.preparing > 0 ? "waiting" : "all");
  const shown = (filter === "waiting" ? reviews.filter(inWaiting) : reviews)
    .map((r, i) => ({ r, i }))
    .sort((a, b) => Number(mustRead(b.r)) - Number(mustRead(a.r)) || a.i - b.i)
    .map(({ r }) => r);
  return (
    <div className="reviews">
      <section className="review-summary">
        <div>
          <strong className="big">{ratingText(summary.average_rating)}</strong>
          <span>평균 별점 · 리뷰 {summary.count}개</span>
        </div>
        <p data-todo={summary.unanswered > 0}>{unansweredText(summary.unanswered)}</p>
      </section>
      {onRunReport && <ReportRoutine report={report} busy={busy} onRun={onRunReport} />}
      <section className="inbox-bar" aria-label={waitingText(summary.waiting)}>
        <div>
          <h3>{waitingText(summary.waiting)}</h3>
          {summary.preparing > 0 && (
            <p className="inbox-preparing" role="status">
              {preparingText(summary.preparing)}
            </p>
          )}
        </div>
        <div className="segmented" role="radiogroup" aria-label={INBOX_TEXT.filter}>
          {(
            [
              ["waiting", INBOX_TEXT.filterWaiting],
              ["all", INBOX_TEXT.filterAll],
            ] as Array<[Filter, string]>
          ).map(([key, label]) => (
            <button key={key} type="button" role="radio" aria-checked={filter === key} onClick={() => setChosen(key)}>
              {label}
            </button>
          ))}
        </div>
      </section>
      {reviews.length === 0 ? (
        <p className="empty">아직 리뷰가 없어요.</p>
      ) : shown.length === 0 ? (
        <p className="empty">{INBOX_TEXT.empty}</p>
      ) : (
        <ul className="review-list">
          {shown.map((r) => (
            <Review
              key={r.id}
              review={r}
              preparing={summary.preparing > 0}
              focused={r.id === focusId}
              onReply={onReply}
            />
          ))}
        </ul>
      )}
      {onSimulate && (
        <button type="button" className="ghost small inbox-simulate" disabled={busy} onClick={onSimulate}>
          {INBOX_TEXT.simulate}
        </button>
      )}
    </div>
  );
}
