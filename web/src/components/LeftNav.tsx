import { quickBadgeText, whenText } from "../labels";
import type { Recent } from "../storage";
import { Icon, type IconName } from "./Icon";
import type { Tab } from "./Sidecar";

/**
 * 자주 쓰는 일. 하나를 늘리려면 이 표에만 한 줄 더한다.
 * - ask: 비서에게 보낼 말(없으면 사진 고르기를 연다)
 * - tab: 같이 보여 줄 사이드카 탭
 * - inChat: 대화 영역에 입력 블록이 뜨는 일. 좁은 화면에서 사이드카를 펼쳐 가리지 않는다.
 */
export const QUICK: Array<{ key: string; label: string; hint: string; icon: IconName; ask: string | null; action_id?: string; tab: Tab; inChat?: boolean }> = [
  { key: "photo", label: "메뉴 사진 올리기", hint: "메뉴판·음식 사진", icon: "camera", ask: null, tab: "shop" },
  { key: "reviews", label: "요즘 리뷰", hint: "손님 반응 정리", icon: "star", ask: "요즘 리뷰 어때?", action_id: "review.summary", tab: "reviews" },
  { key: "stats", label: "이번 주 손님", hint: "방문·유입 요약", icon: "chart", ask: "이번 주 손님 얼마나 왔어?", action_id: "stats.week", tab: "stats" },
  {
    key: "coupon",
    label: "쿠폰 만들기",
    hint: "할인으로 손님 부르기",
    icon: "ticket",
    ask: null,
    action_id: "coupon.create", // 값 없이 보내면 대화 영역에 쿠폰 입력 블록이 뜬다
    tab: "shop",
    inChat: true,
  },
];

export type Quick = (typeof QUICK)[number];

/**
 * 전화 주문(Part C) 화면으로 가는 링크. 사장님 주문 카드 화면은 `?key=`가 있어야 열리므로
 * 지금 주소에 key가 있으면 그대로 넘긴다.
 */
function phoneLinks(search: string): Array<{ key: string; label: string; hint: string; icon: IconName; href: string }> {
  const ownerKey = new URLSearchParams(search).get("key");
  return [
    { key: "phone", label: "손님으로 전화 걸기", hint: "AI가 받는 전화 주문", icon: "phone", href: "/phone" },
    {
      key: "owner",
      label: "전화 주문 카드",
      hint: "들어온 주문·손님 질문",
      icon: "receipt",
      href: ownerKey ? `/owner?key=${encodeURIComponent(ownerKey)}` : "/owner",
    },
  ];
}

type Props = {
  recent: Recent[];
  current: string | null;
  busy: boolean;
  onNew: () => void;
  onOpen: (id: string) => void;
  onQuick: (q: Quick) => void;
  /** 자주 쓰는 일 옆 숫자 배지(예: reviews → 답글 기다리는 리뷰 수). 0이면 안 보인다. */
  badges?: Partial<Record<string, number>>;
};

export function LeftNav({ recent, current, busy, onNew, onOpen, onQuick, badges = {} }: Props) {
  return (
    <nav className="nav" aria-label="대화 목록">
      <div className="brand">
        <img className="brand-mark" src="/agent.png" alt="" />
        <span className="brand-name">사장님 비서</span>
      </div>
      <button type="button" className="new-chat" onClick={onNew} disabled={busy}>
        <Icon name="plus" />새 대화
      </button>

      <h2 className="nav-title">자주 쓰는 일</h2>
      <ul className="quick">
        {QUICK.map((q) => (
          <li key={q.key}>
            <button
              type="button"
              aria-label={q.label}
              aria-describedby={`quick-${q.key}`}
              onClick={() => onQuick(q)}
              disabled={busy}
            >
              <span className="quick-icon" aria-hidden>
                <Icon name={q.icon} />
              </span>
              <span className="quick-text">
                <strong>{q.label}</strong>
                <small id={`quick-${q.key}`}>{q.hint}</small>
              </span>
              {Boolean(badges[q.key]) && (
                <span className="quick-badge" aria-label={quickBadgeText(q.key, badges[q.key] ?? 0)}>
                  {badges[q.key]}
                </span>
              )}
            </button>
          </li>
        ))}
      </ul>

      <h2 className="nav-title">전화 주문</h2>
      <ul className="quick">
        {phoneLinks(window.location.search).map((l) => (
          <li key={l.key}>
            <a href={l.href} target="_blank" rel="noreferrer" aria-describedby={`phone-${l.key}`}>
              <span className="quick-icon" aria-hidden>
                <Icon name={l.icon} />
              </span>
              <span className="quick-text">
                <strong>{l.label}</strong>
                <small id={`phone-${l.key}`}>{l.hint}</small>
              </span>
            </a>
          </li>
        ))}
      </ul>

      <h2 className="nav-title">최근 대화</h2>
      {recent.length === 0 ? (
        <p className="nav-empty">아직 대화가 없어요.</p>
      ) : (
        <ul className="recent" aria-label="최근 대화">
          {recent.map((r) => (
            <li key={r.id}>
              <button
                type="button"
                aria-current={r.id === current ? "true" : undefined}
                onClick={() => onOpen(r.id)}
                disabled={busy}
              >
                <span className="recent-title">{r.title}</span>
                <span className="recent-time">{whenText(r.at)}</span>
              </button>
            </li>
          ))}
        </ul>
      )}
    </nav>
  );
}
