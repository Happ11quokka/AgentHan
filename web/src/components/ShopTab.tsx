import type { ReactNode } from "react";
import { approval, DAYS, discountText, hoursText, MARK_TEXT, periodText, won, type RowMark } from "../labels";
import type { Pending, Place, ReviewReport, Routines, RoutinesChange, SectionChanges } from "../types";
import { RoutineSettings } from "./RoutineSettings";

type Mark = RowMark | undefined;

/** 승인 대기 변경을 덮어 그린 화면과, 줄마다 표시할 강조. */
function overlay(place: Place, pending: Pending[]) {
  let view = place;
  const marks: Record<"menu" | "business_hours" | "coupon" | "keywords", Map<string, Mark>> = {
    menu: new Map(),
    business_hours: new Map(),
    coupon: new Map(),
    keywords: new Map(),
  };
  const removed: Place = { menu: [], business_hours: {}, coupon: [], keywords: [] };
  for (const p of pending) {
    if (!p.after) continue;
    for (const [section, diff] of Object.entries(p.changes ?? {}) as Array<[keyof typeof marks, SectionChanges]>) {
      if (!(section in marks)) continue; // 톡톡·주문·예약 같은 기록은 이 탭에 그리지 않는다(승인 카드가 보여 준다).
      view ={ ...view, [section]: p.after[section] };
      for (const k of diff.added) marks[section].set(k, "added");
      for (const k of diff.updated) marks[section].set(k, "updated");
      for (const k of diff.removed) marks[section].set(k, "removed");
    }
  }
  for (const m of place.menu) if (marks.menu.get(m.id) === "removed") removed.menu.push(m);
  for (const c of place.coupon) if (marks.coupon.get(c.id) === "removed") removed.coupon.push(c);
  for (const k of place.keywords ?? []) if (marks.keywords.get(k) === "removed") removed.keywords?.push(k);
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

/** 승인 전 변경이 있으면 위에 한 줄: 저장해야 반영된다. */
function PendingBanner({ pending }: { pending: Pending[] }) {
  if (pending.length === 0) return null;
  const waitingPhoto = pending.some((p) => p.kind === "photo" && approval(p).blocked);
  return (
    <p className="shop-banner">
      {waitingPhoto ? "사진에서 읽은 메뉴를 확인하고 있어요." : "저장 전 변경을 미리 보여 드려요."} 대화에서 저장을 눌러야 반영돼요.
    </p>
  );
}

/** 메뉴 탭: 네이버 플레이스처럼 테두리 없는 줄(이름·설명·가격, 오른쪽 사진). 승인 전 변경은 알약 배지. */
export function MenuTab({ place, pending }: { place: Place; pending: Pending[] }) {
  const { view, marks, removed } = overlay(place, pending);
  const before = new Map(place.menu.map((m) => [m.id, m]));
  return (
    <div className="shop">
      <PendingBanner pending={pending} />
      <Section title="메뉴" count={view.menu.length}>
        {view.menu.length + removed.menu.length === 0 ? (
          <p className="empty">아직 메뉴가 없어요.</p>
        ) : (
          <ul className="menu-list">
            {[...view.menu, ...removed.menu].map((m) => {
              const mark = marks.menu.get(m.id);
              const old = mark === "updated" ? before.get(m.id) : undefined;
              const oldPrice = old && old.price !== m.price && old.price !== null ? won(old.price) : null;
              return (
                <li key={m.id} className="menu-item" data-mark={mark}>
                  <div className="menu-body">
                    {mark && <span className="mark">{MARK_TEXT[mark]}</span>}
                    <strong>{m.name}</strong>
                    {m.description && <p className="menu-desc">{m.description}</p>}
                    <p className="menu-price">
                      {oldPrice && <s>{oldPrice}</s>}
                      <span>{m.price !== null ? won(m.price) : "가격 없음"}</span>
                    </p>
                  </div>
                  {m.photo_url ? (
                    <img className="menu-photo" src={m.photo_url} alt="" />
                  ) : (
                    <span className="menu-photo empty" aria-hidden>
                      {m.name.slice(0, 1)}
                    </span>
                  )}
                </li>
              );
            })}
          </ul>
        )}
      </Section>
    </div>
  );
}

type HomeProps = {
  place: Place;
  pending: Pending[];
  report: ReviewReport | null;
  routines: Routines | null;
  onRoutines?: (change: RoutinesChange) => Promise<string | null>;
  onRunReport?: () => Promise<string | null>;
};

/** 홈 탭: 영업시간 · 쿠폰 · 대표키워드 · 루틴 설정. */
export function HomeTab({ place, pending, report, routines, onRoutines, onRunReport }: HomeProps) {
  const { view, marks, removed } = overlay(place, pending);
  return (
    <div className="shop home">
      <PendingBanner pending={pending} />

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

      {view.keywords !== undefined && (
        <Section title="대표키워드" count={view.keywords.length}>
          {view.keywords.length + (removed.keywords?.length ?? 0) === 0 ? (
            <p className="empty">아직 대표키워드가 없어요.</p>
          ) : (
            <ul className="keywords" aria-label="대표키워드">
              {[...view.keywords, ...(removed.keywords ?? [])].map((k) => (
                <Row key={k} mark={marks.keywords.get(k)}>
                  <span className="keyword">{k}</span>
                </Row>
              ))}
            </ul>
          )}
        </Section>
      )}

      {onRoutines && onRunReport && <RoutineSettings report={report} routines={routines} onChange={onRoutines} onRunReport={onRunReport} />}
    </div>
  );
}
