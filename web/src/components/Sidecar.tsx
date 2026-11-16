import { useRef } from "react";
import { CONNECTION_TEXT } from "../labels";
import type { Action, LogEntry, Pending, Period, Place, ReportView, ReviewsView } from "../types";
import type { Loaded } from "../useLoad";
import { Icon } from "./Icon";
import { ReviewsTab } from "./ReviewsTab";
import { ShopTab } from "./ShopTab";
import { StatsTab } from "./StatsTab";

export type Tab = "shop" | "reviews" | "stats";

const TABS: Array<[Tab, string]> = [
  ["shop", "내 가게"],
  ["reviews", "리뷰"],
  ["stats", "손님 통계"],
];

type Props = {
  tab: Tab;
  onTab: (tab: Tab) => void;
  place: Place | null;
  pending: Pending[];
  log: LogEntry[];
  online: boolean;
  busy: boolean;
  reviews: Loaded<ReviewsView>;
  report: Loaded<ReportView>;
  period: Period;
  onPeriod: (period: Period) => void;
  onAction: (action: Action) => void;
  onClose: () => void;
};

export function Sidecar(props: Props) {
  const { tab, onTab, place, pending, log, online, onClose } = props;
  const buttons = useRef<Array<HTMLButtonElement | null>>([]);

  const moveFocus = (step: number) => {
    const index = (TABS.findIndex(([key]) => key === tab) + step + TABS.length) % TABS.length;
    onTab(TABS[index][0]);
    buttons.current[index]?.focus();
  };

  return (
    <aside className="side" aria-label="비서와 가게">
      <button type="button" className="icon-button side-close" aria-label="내 가게 닫기" onClick={onClose}>
        <Icon name="close" />
      </button>
      <section className="profile">
        <img src="/agent.png" alt="비서 프로필" className={props.busy ? "working" : undefined} />
        <div>
          <strong>사장님 비서</strong>
          <span className="online" data-online={online}>
            <i aria-hidden />
            {online ? CONNECTION_TEXT.online : CONNECTION_TEXT.offline}
          </span>
        </div>
      </section>

      <div
        className="tabs"
        role="tablist"
        aria-label="사이드카"
        onKeyDown={(e) => {
          if (e.key === "ArrowRight") moveFocus(1);
          if (e.key === "ArrowLeft") moveFocus(-1);
        }}
      >
        {TABS.map(([key, label], i) => (
          <button
            key={key}
            ref={(el) => {
              buttons.current[i] = el;
            }}
            type="button"
            role="tab"
            id={`tab-${key}`}
            aria-selected={tab === key}
            aria-controls={tab === key ? `panel-${key}` : undefined}
            tabIndex={tab === key ? 0 : -1}
            onClick={() => onTab(key)}
          >
            {label}
          </button>
        ))}
      </div>

      <div className="tab-panel" role="tabpanel" id={`panel-${tab}`} aria-labelledby={`tab-${tab}`}>
        {tab === "shop" &&
          (place ? <ShopTab place={place} pending={pending} log={log} /> : <p className="loading">불러오는 중…</p>)}
        {tab === "reviews" && <ReviewsTab loaded={props.reviews} busy={props.busy} onAction={props.onAction} />}
        {tab === "stats" && <StatsTab loaded={props.report} period={props.period} onPeriod={props.onPeriod} />}
      </div>
    </aside>
  );
}
