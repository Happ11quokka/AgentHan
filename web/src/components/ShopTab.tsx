import type { ReactNode } from "react";
import { approval, clockText, DAYS, discountText, hoursText, MARK_TEXT, periodText, sameDay, won, type RowMark } from "../labels";
import type { LogEntry, Pending, Place, SectionChanges } from "../types";

type Mark = RowMark | undefined;

/** 승인 대기 변경을 덮어 그린 화면과, 줄마다 표시할 강조. */
function overlay(place: Place, pending: Pending[]) {
  let view = place;
  const marks: Record<keyof Place, Map<string, Mark>> = {
    menu: new Map(),
    business_hours: new Map(),
    coupon: new Map(),
  };
  const removed: Place = { menu: [], business_hours: {}, coupon: [] };
  for (const p of pending) {
    if (!p.after) continue;
    for (const [section, diff] of Object.entries(p.changes ?? {}) as Array<[keyof Place, SectionChanges]>) {
      view = { ...view, [section]: p.after[section] };
      for (const k of diff.added) marks[section].set(k, "added");
      for (const k of diff.updated) marks[section].set(k, "updated");
      for (const k of diff.removed) marks[section].set(k, "removed");
    }
  }
  for (const m of place.menu) if (marks.menu.get(m.id) === "removed") removed.menu.push(m);
  for (const c of place.coupon) if (marks.coupon.get(c.id) === "removed") removed.coupon.push(c);
  return { view, marks, removed };
}

function Row({ mark, children }: { mark: Mark; children: ReactNode }) {
  return (
    <li className="shop-row" data-mark={mark}>
      {children}
      {mark && <span className="mark">{MARK_TEXT[mark]}</span>}
    </li>
  );
}

function Section({ title, count, children }: { title: string; count?: number; children: ReactNode }) {
  return (
    <section className="shop-section">
      <h3>
        {title}
        {count !== undefined && <span>{count}</span>}
      </h3>
      {children}
    </section>
  );
}

export function ShopTab({ place, pending, log }: { place: Place; pending: Pending[]; log: LogEntry[] }) {
  const { view, marks, removed } = overlay(place, pending);
  const waitingPhoto = pending.some((p) => p.kind === "photo" && approval(p).blocked);
  const today = log.filter((entry) => sameDay(entry.at, Date.now()));

  return (
    <div className="shop">
      {pending.length > 0 && (
        <p className="shop-banner">
          {waitingPhoto ? "사진에서 읽은 메뉴를 확인하고 있어요." : "저장 전 변경을 미리 보여 드려요."} 대화에서 저장을 눌러야 반영돼요.
        </p>
      )}

      <Section title="메뉴" count={view.menu.length}>
        <ul className="menu-list">
          {[...view.menu, ...removed.menu].map((m) => (
            <Row key={m.id} mark={marks.menu.get(m.id)}>
              {m.photo_url ? (
                <img className="menu-photo" src={m.photo_url} alt="" />
              ) : (
                <span className="menu-photo empty" aria-hidden>
                  {m.name.slice(0, 1)}
                </span>
              )}
              <div className="menu-body">
                <strong>{m.name}</strong>
                {m.description && <small>{m.description}</small>}
              </div>
              <span className="menu-price">{m.price !== null ? won(m.price) : "가격 없음"}</span>
            </Row>
          ))}
        </ul>
      </Section>

      <Section title="영업시간">
        <ul className="hours">
          {DAYS.filter(([k]) => k in view.business_hours).map(([key, label]) => (
            <Row key={key} mark={marks.business_hours.get(key)}>
              <span className="day">{label}</span>
              <span className="time" data-closed={view.business_hours[key]?.closed}>
                {hoursText(view.business_hours[key])}
              </span>
            </Row>
          ))}
        </ul>
      </Section>

      <Section title="쿠폰" count={view.coupon.length}>
        {view.coupon.length + removed.coupon.length === 0 ? (
          <p className="empty">아직 쿠폰이 없어요.</p>
        ) : (
          <ul className="coupons">
            {[...view.coupon, ...removed.coupon].map((c) => (
              <Row key={c.id} mark={marks.coupon.get(c.id)}>
                <div className="coupon-body">
                  <strong>{c.title}</strong>
                  <small>{periodText(c)}</small>
                </div>
                <span className="coupon-value">{discountText(c)}</span>
              </Row>
            ))}
          </ul>
        )}
      </Section>

      <Section title="오늘 한 일">
        {today.length === 0 ? (
          <p className="empty">저장한 변경이 여기에 쌓여요.</p>
        ) : (
          <ul className="today" aria-label="오늘 한 일">
            {[...today].reverse().flatMap((entry) =>
              entry.lines.map((line, i) => (
                <li key={`${entry.at}-${i}`}>
                  <span>{line}</span>
                  <time>{clockText(entry.at)}</time>
                </li>
              )),
            )}
          </ul>
        )}
      </Section>
    </div>
  );
}
