import { useRef } from "react";
import { avatarState } from "../avatarState";
import { CONNECTION_TEXT } from "../labels";
import type { Action, Pending, Period, Place, ReportView, ReviewReport, ReviewStats, ReviewStatsPeriod, ReviewsView, Routines, RoutinesChange } from "../types";
import type { Loaded } from "../useLoad";
import { AgentAvatar } from "./AgentAvatar";
import { Icon } from "./Icon";
import { ReviewsTab, type OnReply } from "./ReviewsTab";
import { HomeTab, MenuTab, type OnMenuDelete, type OnMenuEdit, type OnMenuPolish } from "./ShopTab";
import { type StatsKind, StatsTab } from "./StatsTab";

export type Tab = "home" | "menu" | "reviews" | "stats";

const TABS: Array<[Tab, string]> = [
  ["home", "홈"],
  ["menu", "메뉴"],
  ["reviews", "리뷰"],
  ["stats", "통계"],
];

type Props = {
  tab: Tab;
  onTab: (tab: Tab) => void;
  place: Place | null;
  pending: Pending[];
  online: boolean;
  busy: boolean;
  hasInput: boolean;
  reviews: Loaded<ReviewsView>;
  report: Loaded<ReportView>;
  period: Period;
  onPeriod: (period: Period) => void;
  onAction: (action: Action) => void;
  /** 리뷰 탭의 [새 리뷰 도착 (데모)]. 없으면 버튼도 없다. */
  onSimulateReview?: () => void;
  /** 대기함 카드 버튼. 실패하면 카드에 보일 이유를 돌려준다. */
  onReply: OnReply;
  /** [리뷰 탭에서 보기]가 가리키는 카드(at: 누른 때, 같은 카드를 다시 눌러도 다시 스크롤). */
  focusReview: { id: string; at: number } | null;
  /** 홈 탭 루틴 설정(#33): 리뷰 정기 보고(#98)와 답글 초안 미리 쓰기. */
  reviewReport?: ReviewReport | null;
  onRunReport?: () => Promise<string | null>;
  routines?: Routines | null;
  onRoutines?: (change: RoutinesChange) => Promise<string | null>;
  /** 통계 탭 › AI 리뷰 통계. */
  statsKind?: StatsKind;
  onStatsKind?: (k: StatsKind) => void;
  reviewStats?: Loaded<ReviewStats>;
  reviewPeriod?: ReviewStatsPeriod;
  onReviewPeriod?: (p: ReviewStatsPeriod) => void;
  onAsk?: (text: string) => void;
  /** 메뉴 탭 직접 수정·삭제(승인 카드 없음). 없으면 메뉴 탭은 읽기만. */
  onEditMenu?: OnMenuEdit;
  onDeleteMenu?: OnMenuDelete;
  onPolishMenu?: OnMenuPolish;
  /** 메뉴판 사진을 읽는 중이면 보낸 시각(ms). 메뉴 탭이 진행 막대를 그린다. */
  readingSince?: number | null;
  onClose: () => void;
};

export function Sidecar(props: Props) {
  const { tab, onTab, place, pending, online, onClose } = props;
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
        <AgentAvatar state={avatarState({
          busy: props.busy,
          hasInput: props.hasInput,
          hasPendingApproval: Boolean(place) && pending.length > 0,
        })} />
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
        {(tab === "home" || tab === "menu") && !place && <p className="loading">불러오는 중…</p>}
        {tab === "home" && place && (
          <HomeTab
            place={place}
            pending={pending}
            report={props.reviewReport ?? null}
            routines={props.routines ?? null}
            onRoutines={props.onRoutines}
            onRunReport={props.onRunReport}
          />
        )}
        {tab === "menu" && place && <MenuTab place={place} pending={pending} onEdit={props.onEditMenu} onDelete={props.onDeleteMenu} onPolish={props.onPolishMenu} readingSince={props.readingSince ?? null} />}
        {tab === "reviews" && (
          <ReviewsTab
            loaded={props.reviews}
            busy={props.busy}
            onReply={props.onReply}
            focus={props.focusReview}
            onSimulate={props.onSimulateReview}
          />
        )}
        {tab === "stats" && (
          <StatsTab
            loaded={props.report}
            period={props.period}
            onPeriod={props.onPeriod}
            kind={props.statsKind}
            onKind={props.onStatsKind}
            reviewStats={props.reviewStats}
            reviewPeriod={props.reviewPeriod}
            onReviewPeriod={props.onReviewPeriod}
            onAsk={props.onAsk}
          />
        )}
      </div>
    </aside>
  );
}
