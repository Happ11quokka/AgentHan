import { useEffect, useRef, useState, type RefObject } from "react";
import { RESULT_TEXT } from "../labels";
import type { Action, Message } from "../types";
import type { Conversation } from "../useConversation";
import { ApprovalCard } from "./ApprovalCard";
import { Composer } from "./Composer";
import { Icon } from "./Icon";
import { ReplyText } from "./ReplyText";

const SUGGESTIONS = ["메뉴판 사진으로 메뉴 올리고 싶어요", "일요일에도 10시부터 6시까지 열래요", "요즘 리뷰 어때?"];

type Props = {
  chat: Conversation;
  photoInput: RefObject<HTMLInputElement | null>;
  sideOpen: boolean;
  onToggleNav: () => void;
  onToggleSide: () => void;
};

function Bubble({ message, onAction, busy }: { message: Message; onAction: (action: Action) => void; busy: boolean }) {
  if (message.role === "result") {
    return (
      <div className="result" data-saved={message.saved}>
        <span className="result-badge">
          <Icon name={message.saved ? "check" : "close"} size={14} />
          {message.saved ? RESULT_TEXT.saved : RESULT_TEXT.skipped}
        </span>
        {message.lines.length > 0 && (
          <ul>
            {message.lines.map((line) => (
              <li key={line}>{line}</li>
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
        <ReplyText text={message.text} />
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

export function ChatPane({ chat, photoInput, sideOpen, onToggleNav, onToggleSide }: Props) {
  const end = useRef<HTMLDivElement>(null);
  const [dragging, setDragging] = useState(false);
  const [dropped, setDropped] = useState<File | null>(null);

  useEffect(() => {
    end.current?.scrollIntoView?.({ block: "end", behavior: "smooth" });
  }, [chat.messages.length, chat.busy, chat.pending.length, chat.error]);

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
            <Bubble key={m.id} message={m} onAction={(action) => void chat.act(action)} busy={chat.busy} />
          ))}
          {!chat.busy &&
            chat.place &&
            chat.pending.map((p) => (
              <ApprovalCard
                key={p.task_id}
                place={chat.place!}
                pending={p}
                onAnswer={(text) => void chat.send(text)}
                onAction={(action) => void chat.act(action)}
              />
            ))}
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
