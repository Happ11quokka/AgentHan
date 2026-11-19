import { useEffect, useRef, useState, type RefObject } from "react";
import { REPLY_NOTICE_VIEW, RESULT_TEXT } from "../labels";
import type { Action, Message } from "../types";
import type { Conversation } from "../useConversation";
import { ApprovalCard } from "./ApprovalCard";
import { Composer } from "./Composer";
import { CouponForm } from "./CouponForm";
import { Icon } from "./Icon";
import { ReplyText } from "./ReplyText";

const SUGGESTIONS = ["메뉴판 사진으로 메뉴 올리고 싶어요", "일요일에도 10시부터 6시까지 열래요", "요즘 리뷰 어때?"];

type Props = {
  chat: Conversation;
  photoInput: RefObject<HTMLInputElement | null>;
  sideOpen: boolean;
  onToggleNav: () => void;
  onToggleSide: () => void;
  /** 화면 알림의 [리뷰 탭에서 보기]: 사이드카 리뷰 탭을 열고 그 카드로 스크롤. */
  onShowReview: (reviewId: string) => void;
};

type BubbleProps = {
  message: Message;
  onAction: (action: Action) => void;
  onShowReview: (reviewId: string) => void;
  busy: boolean;
  /** 이 답이 띄운 승인 카드가 아직 떠 있다: 카드와 겹치는 인용·질문은 빼고 첫 줄만 보인다. */
  lead: boolean;
};

function Bubble({ message, onAction, onShowReview, busy, lead }: BubbleProps) {
  if (message.role === "notice") {
    return (
      <div className="notice" role="status">
        <Icon name="close" size={14} />
        <p>{message.text}</p>
        <button type="button" className="ghost small" onClick={() => onShowReview(message.reviewId)}>
          {REPLY_NOTICE_VIEW}
        </button>
      </div>
    );
  }
  if (message.role === "result") {
    return (
      <div className="result" data-saved={message.saved}>
        <span className="result-badge">
          <Icon name={message.saved ? "check" : "close"} size={14} />
          {message.saved ? RESULT_TEXT.saved : RESULT_TEXT.skipped}
        </span>
        {message.lines.length > 0 && (
          <ul>
            {message.lines.map((line, i) => (
              <li key={`${i}-${line}`}>{line}</li>
            ))}
          </ul>
        )}
      </div>
    );
  }
  if (message.role === "owner") {
    return (
      <div className="row owner">
        <div className="bubble owner">
          {message.image && <img className="sent-photo" src={message.image} alt="보낸 사진" />}
          {!message.image && message.hadImage && <span className="sent-photo-note">사진</span>}
          {message.text && <p>{message.text}</p>}
        </div>
      </div>
    );
  }
  return (
    <div className="row agent">
      <img className="avatar" src="/agent.png" alt="" />
      <div className="bubble agent">
        <ReplyText text={message.text} lead={lead} previews={message.previews} />
        {message.nextActions && message.nextActions.length > 0 && (
          <div className="suggestions">
            {message.nextActions.map((action, i) => (
              <button key={i} type="button" disabled={busy} onClick={() => onAction(action)}>{action.label}</button>
            ))}
          </div>
        )}
      </div>
    </div>
  );
}

/**
 * 한 번에 저장되는 변경 카드들. 변경 카드가 둘 이상이고 마지막 비서 답의 승인 칩([모두 저장])이
 * 그중 하나를 가리키면, 그 칩 하나가 묶음 전체를 저장·취소한다. 이때 카드마다 [저장]을 두면
 * 카드 하나만 저장되는 것처럼 보이므로 카드 버튼은 숨긴다.
 */
function bundledCards(chat: Conversation): Set<string> {
  const cards = chat.pending.filter((p) => !p.kind);
  if (cards.length < 2) return new Set();
  const last = [...chat.messages].reverse().find((m) => m.role === "agent");
  const ids = new Set(cards.map((p) => p.task_id));
  const chip = last?.role === "agent" && last.nextActions?.some((a) => a.action_id === "task.approve" && ids.has(String(a.args.task_id)));
  return chip ? ids : new Set();
}

export function ChatPane({ chat, photoInput, sideOpen, onToggleNav, onToggleSide, onShowReview }: Props) {
  const end = useRef<HTMLDivElement>(null);
  const [dragging, setDragging] = useState(false);
  const [dropped, setDropped] = useState<File | null>(null);
  const open = new Set(chat.pending.map((p) => p.task_id));
  const bundled = bundledCards(chat);

  useEffect(() => {
    end.current?.scrollIntoView?.({ block: "end", behavior: "smooth" });
  }, [chat.messages.length, chat.busy, chat.pending.length, chat.error, chat.couponForm === null]);

  return (
    <main
      className="chat"
      aria-label="비서와 대화"
      data-dragging={dragging}
      onDragOver={(e) => {
        if (!e.dataTransfer.types.includes("Files")) return;
        e.preventDefault();
        setDragging(true);
      }}
      onDragLeave={(e) => {
        if (e.currentTarget === e.target) setDragging(false);
      }}
      onDrop={(e) => {
        e.preventDefault();
        setDragging(false);
        const file = [...e.dataTransfer.files].find((f) => f.type.startsWith("image/"));
        if (file) setDropped(file);
      }}
    >
      <header className="chat-head">
        <button type="button" className="icon-button only-narrow-nav" aria-label="대화 목록 열기" onClick={onToggleNav}>
          <Icon name="menu" />
        </button>
        <div className="chat-title">
          <strong>비서와 대화</strong>
          <span>말하거나 사진을 올리면 가게 정보를 대신 바꿔 드려요</span>
        </div>
        <button
          type="button"
          className="icon-button only-narrow-side"
          aria-label="내 가게 보기"
          aria-expanded={sideOpen}
          onClick={onToggleSide}
        >
          <Icon name="store" />
        </button>
      </header>

      <div className="messages">
        <div className="messages-inner">
          {chat.messages.length === 0 && !chat.busy && (
            <section className="welcome">
              <img src="/agent.png" alt="" className="welcome-face" />
              <h1>사장님, 무엇을 도와드릴까요?</h1>
              <p>메뉴 등록, 영업시간, 쿠폰, 리뷰까지 말로 하면 제가 대신 해 드려요.</p>
              <div className="suggestions">
                {SUGGESTIONS.map((s) => (
                  <button key={s} type="button" onClick={() => void (s === "요즘 리뷰 어때?" ? chat.act({ action_id: "review.summary", args: {}, label: s }) : chat.send(s))} disabled={!chat.sessionId}>
                    {s}
                  </button>
                ))}
              </div>
            </section>
          )}
          {chat.messages.map((m) => (
            <Bubble
              key={m.id}
              message={m}
              onAction={(action) => void chat.act(action)}
              onShowReview={onShowReview}
              busy={chat.busy}
              lead={m.role === "agent" && Boolean(m.cardFor) && open.has(m.cardFor!)}
            />
          ))}
          {!chat.busy &&
            chat.place &&
            chat.pending.map((p) => (
              <ApprovalCard
                key={p.task_id}
                place={chat.place!}
                pending={p}
                bundled={bundled.has(p.task_id)}
                review={chat.reviewOf(p.review_id)}
                onAnswer={(text) => void chat.send(text)}
                onAction={(action) => void chat.act(action)}
              />
            ))}
          {chat.couponForm && (
            <CouponForm
              key={JSON.stringify(chat.couponForm)}
              initial={chat.couponForm}
              busy={chat.busy}
              onCancel={chat.closeCoupon}
              onSubmit={(values) => void chat.submitCoupon(values)}
            />
          )}
          {chat.busy && (
            <div className="row agent" role="status" aria-label="비서가 답을 쓰는 중">
              <img className="avatar working" src="/agent.png" alt="" />
              <div className="bubble agent typing">
                <span />
                <span />
                <span />
              </div>
            </div>
          )}
          {chat.error && (
            <div className="error" role="alert">
              <p>{chat.error}</p>
              <button type="button" onClick={chat.retry}>
                다시 시도
              </button>
            </div>
          )}
          <div ref={end} />
        </div>
      </div>

      <Composer
        disabled={!chat.sessionId || chat.busy}
        inputRef={photoInput}
        dropped={dropped}
        onDroppedTaken={() => setDropped(null)}
        onSend={(text, image) => void chat.send(text, image)}
      />
      {dragging && (
        <div className="drop-hint" aria-hidden>
          <Icon name="camera" size={28} />
          사진을 여기에 놓으세요
        </div>
      )}
    </main>
  );
}
