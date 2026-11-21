// 전화 주문 서랍: 왼쪽 [전화 주문]을 누르면 도구함처럼 사이드바 옆에 열린다(Esc·바깥·닫기로 닫힘).
// 위에는 확인할 주문을 마감이 급한 순서로 주방 전표처럼 쌓고, 확인한 주문과 취소·거절은 아래에 접어 둔다.
// 확인·늦춰 확인·거절은 사장님 화면과 같은 POST이고, 바뀐 카드는 SSE로만 받는다.
import { useEffect, useRef } from "react";
import type { Orders } from "../orders/useOrders";
import { RejectButton, RejectedResult, SoldOutMark } from "../screens/owner/OrderReject";
import { type OrderCard, approvalClock, effectivePickup, orderOutcome } from "../screens/owner/ownerState";
import { ACCEPT_DELAYS } from "../screens/owner/ownerStream";
import { OWNER_TEXT, approvalLeft, discountLine, lineView } from "../screens/owner/ownerText";
import { Icon } from "./Icon";
import { agoText, ownerScreenLink, won } from "../orders/orderText";

const TEXT = OWNER_TEXT.ko;

/** 전표 왼쪽 띠 색: 대기, 마감 1분 안(또는 손님이 전화로 기다림), 마감 지남, 잠시 멈춤(손님이 고치는 중), 끝 */
type TicketTone = "waiting" | "soon" | "late" | "paused" | "done";

function ticketTone(card: OrderCard, nowMs: number): TicketTone {
  const outcome = orderOutcome(card);
  if (outcome === "changing" || outcome === "cancelled") return "paused";
  if (outcome !== "waiting") return "done";
  const clock = approvalClock(card, nowMs);
  if (clock === "late") return "late";
  return card.stage === "in_call" || clock <= 60 ? "soon" : "waiting";
}

const deadlineOf = (card: OrderCard) => (card.deadline_at ? Date.parse(card.deadline_at) : Infinity);

export function OrderDrawer({ orders, onClose }: { orders: Orders; onClose: () => void }) {
  const panel = useRef<HTMLDivElement>(null);
  useEffect(() => {
    panel.current?.focus();
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") onClose();
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [onClose]);

  const { state, waiting, confirmed } = orders;
  const active = [...state.orders.filter((o) => o.status === "new")].sort((a, b) => deadlineOf(a) - deadlineOf(b));
  const closed = state.orders.filter((o) => o.status !== "new" && o.status !== "accepted").reverse();
  const done = [...confirmed].reverse();

  return (
    <>
      <button type="button" className="toolbox-scrim" aria-label="전화 주문 닫기" tabIndex={-1} onClick={onClose} />
      <div id="orders-panel" ref={panel} tabIndex={-1} className="toolbox-panel orders-panel" role="dialog" aria-label="전화 주문">
        <header className="orders-head">
          <h2>전화 주문</h2>
          <p className="orders-count">
            <span data-urgent={waiting.length > 0}>
              대기 <b>{waiting.length}</b>건
            </span>
            <span>
              확인 <b>{confirmed.length}</b>건
            </span>
          </p>
          <button type="button" className="toolbox-close" aria-label="전화 주문 닫기" onClick={onClose}>
            <Icon name="close" />
          </button>
        </header>

        <div className="orders-body">
          <Status orders={orders} empty={active.length === 0} />
          {active.length > 0 && (
            <ol className="tickets" aria-label="확인할 주문">
              {active.map((card) => (
                <li key={card.id}>
                  <Ticket card={card} orders={orders} />
                </li>
              ))}
            </ol>
          )}
          {done.length > 0 && (
            <details className="orders-fold">
              <summary>확인한 주문 {done.length}건</summary>
              <ol className="tickets">
                {done.map((card) => (
                  <li key={card.id}>
                    <Ticket card={card} orders={orders} />
                  </li>
                ))}
              </ol>
            </details>
          )}
          {closed.length > 0 && (
            <details className="orders-fold">
              <summary>취소·거절된 주문 {closed.length}건</summary>
              <ol className="tickets">
                {closed.map((card) => (
                  <li key={card.id}>
                    <Ticket card={card} orders={orders} />
                  </li>
                ))}
              </ol>
            </details>
          )}
        </div>

        <footer className="orders-foot">
          <a href={ownerScreenLink(orders.key)} target="_blank" rel="noreferrer">
            주방 태블릿용 주문 화면 열기
          </a>
        </footer>
      </div>
    </>
  );
}

function Status({ orders, empty }: { orders: Orders; empty: boolean }) {
  if (orders.noKey)
    return <p className="orders-empty">전화 주문 서버에 아직 연결하지 않았어요. 사장님 화면 주소(key가 붙은 주소)로 이 화면을 한 번 열어 주세요.</p>;
  const { connection } = orders.state;
  if (connection === "failed") return <p className="orders-empty">{TEXT.connection.failed}</p>;
  if (connection !== "live") return <p className="orders-empty">주문 서버에 연결하는 중이에요.</p>;
  return empty ? <p className="orders-empty">확인할 주문이 없어요. 새 전화 주문은 여기에 쌓여요.</p> : null;
}

function Ticket({ card, orders }: { card: OrderCard; orders: Orders }) {
  const { nowMs } = orders;
  const pickup = effectivePickup(card).pickup_label;
  const discount = discountLine(card, "ko");
  return (
    <article className="ticket" data-tone={ticketTone(card, nowMs)} aria-label={`${pickup} 픽업 주문`}>
      <header className="ticket-head">
        <p className="ticket-pickup">
          <b>{pickup}</b> 픽업
        </p>
        <p className="ticket-total">{won(card.total)}</p>
      </header>
      <p className="ticket-meta">
        {card.received_label ? `${card.received_label} 주문 ` : ""}({agoText(card.received_at, nowMs)})
        {discount && <span className="ticket-discount">{discount}</span>}
      </p>
      <ol className="ticket-lines">
        {card.lines.map((line, i) => {
          const view = lineView(line, "ko");
          return (
            <li key={i}>
              <span className="ticket-qty">{line.quantity}</span>
              <span className="ticket-item">
                {view.name}
                {view.options.length > 0 && <small> {view.options.join(", ")}</small>}
                {line.request && (
                  <span className="ticket-request">
                    {line.badges.map((badge) => (
                      <em key={badge} data-badge={badge}>
                        {TEXT.badge[badge]}
                      </em>
                    ))}
                    {line.request}
                  </span>
                )}
              </span>
            </li>
          );
        })}
      </ol>
      {card.owner_check && <p className="ticket-check">손님 질문에 답하지 못한 주문이에요. 확인해 주세요.</p>}
      <Decision card={card} orders={orders} />
      <SoldOutMark card={card} soldOut={orders.state.soldOut} lang="ko" text={TEXT} onUndo={orders.undoSoldOut} />
    </article>
  );
}

function Decision({ card, orders }: { card: OrderCard; orders: Orders }) {
  const outcome = orderOutcome(card);
  if (outcome === "waiting") {
    const clock = approvalClock(card, orders.nowMs);
    const busy = orders.busy.has(card.id);
    const wait =
      clock === "late"
        ? "확인 시간이 지났어요"
        : card.stage === "in_call"
          ? `손님이 전화로 기다리는 중 ${approvalLeft(clock, "ko")}`
          : `${approvalLeft(clock, "ko")} 안에 확인해 주세요`;
    return (
      <footer className="ticket-foot">
        <p className="ticket-clock" data-late={clock === "late"}>
          {wait}
        </p>
        {orders.failed === card.id && <p className="ticket-error">처리하지 못했어요. 다시 눌러 주세요.</p>}
        <div className="ticket-actions" aria-busy={busy}>
          {ACCEPT_DELAYS.map((minutes) => (
            <button
              key={minutes}
              type="button"
              className={minutes ? "ticket-later" : "ticket-accept"}
              disabled={busy}
              onClick={() => orders.accept(card.id, minutes)}
            >
              {minutes ? `${minutes}분 늦춰 확인` : "확인"}
            </button>
          ))}
          <RejectButton text={TEXT} lang="ko" lines={card.lines} onReject={(body) => orders.reject(card.id, body)} />
        </div>
      </footer>
    );
  }
  const result = (text: string, tone: "ok" | "off" = "off") => (
    <p className="ticket-result" data-tone={tone}>
      {text}
    </p>
  );
  switch (outcome) {
    case "accepted":
      return result("확인했어요", "ok");
    case "auto":
      return result("자동 확정됐어요 (확인 화면이 꺼져 있을 때 온 주문)", "ok");
    case "delayed":
      return (
        <>
          {result(`${card.owner_pickup_label} 픽업으로 ${card.delay_min}분 늦춰 확인했어요`, "ok")}
          {card.delay_reply === "asking" && result(TEXT.delayAsking)}
        </>
      );
    case "declined":
      return result("손님이 늦춘 시각을 거절해 주문이 취소됐어요");
    case "expired":
      return result("시간 안에 확인하지 못해 자동으로 취소됐어요");
    case "rejected":
      return <RejectedResult card={card} lang="ko" />;
    case "changing":
      return result("손님이 주문을 고치는 중이에요");
    case "cancelled":
    case "customer_cancelled":
      return result("손님이 주문을 취소했어요");
    default:
      return null;
  }
}
