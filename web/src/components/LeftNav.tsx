import { useEffect, useState } from "react";
import { quickBadgeText, whenText } from "../labels";
import type { Recent } from "../storage";
import { BrandLogo } from "./BrandMark";
import { Icon, type IconName } from "./Icon";
import { NaverImportButton } from "./NaverImport";
import type { Tab } from "./Sidecar";

/**
 * 자주 쓰는 일. 하나를 늘리려면 이 표에만 한 줄 더한다.
 * - ask: 비서에게 보낼 말(없으면 사진 고르기를 연다)
 * - tab: 같이 보여 줄 사이드카 탭
 * - inChat: 대화 영역에 입력 블록이 뜨는 일. 좁은 화면에서 사이드카를 펼쳐 가리지 않는다.
 */
export type Quick = { key: string; label: string; hint: string; icon: IconName; ask: string | null; action_id?: string; tab: Tab; inChat?: boolean };

export const QUICK: Quick[] = [
  { key: "photo", label: "메뉴 사진 올리기", hint: "메뉴판·음식 사진", icon: "camera", ask: null, tab: "menu" },
  { key: "reviews", label: "요즘 리뷰", hint: "손님 반응 정리", icon: "star", ask: "요즘 리뷰 어때?", action_id: "review.summary", tab: "reviews" },
  { key: "stats", label: "이번 주 손님", hint: "방문·유입 요약", icon: "chart", ask: "이번 주 손님 얼마나 왔어?", action_id: "stats.week", tab: "stats" },
  {
    key: "coupon",
    label: "쿠폰 만들기",
    hint: "할인으로 손님 부르기",
    icon: "ticket",
    ask: null,
    action_id: "coupon.create", // 값 없이 보내면 대화 영역에 쿠폰 입력 블록이 뜬다
    tab: "home",
    inChat: true,
  },
];

/**
 * AgentHan 도구함: 비서가 할 수 있는 모든 기능(`src/agenthan/agent/place_features.json`의 18개)을 단계별로 둔다.
 * key·label·stage는 카탈로그의 id·name_ko·stage와 같다(App.test.tsx가 맞춰 본다).
 * 화면 버튼이 있는 기능은 그 action_id를, 없는 기능은 예시 문장(ask)을 대화로 보낸다.
 */
export const TOOLBOX: Array<Quick & { stage: string }> = [
  { stage: "시작하기", key: "business_info", label: "가게 기본 정보 수정", hint: "상호·주소·전화번호·소개글", icon: "store", ask: "가게 기본 정보(이름·주소·전화번호·소개글)를 바꾸고 싶어", action_id: "business_info.edit", tab: "home", inChat: true },
  { stage: "매일 운영", key: "menu", label: "메뉴 등록·수정", hint: "이름·가격·설명, 품절·삭제", icon: "utensils", ask: "메뉴를 등록하거나 고치고 싶어", tab: "menu" },
  { stage: "매일 운영", key: "business_hours", label: "영업시간·휴무 변경", hint: "여는 시간·브레이크·임시 휴무", icon: "clock", ask: "영업시간이나 휴무를 바꾸고 싶어", action_id: "hours.edit", tab: "home", inChat: true },
  { stage: "매일 운영", key: "talktalk", label: "톡톡 문의 답장", hint: "손님 채팅 문의에 답하기", icon: "chat", ask: "톡톡 문의 온 거 보여 줘", tab: "home" },
  { stage: "매일 운영", key: "order", label: "네이버 주문", hint: "접수·거절·준비 완료", icon: "bag", ask: "들어온 네이버 주문 보여 줘", tab: "home" },
  { stage: "매일 운영", key: "reservation", label: "네이버 예약", hint: "확정·취소·노쇼", icon: "calendar", ask: "들어온 네이버 예약 보여 줘", tab: "home" },
  { stage: "매일 운영", key: "smartcall", label: "전화·부재중 기록 조회", hint: "못 받은 전화 확인", icon: "phone", ask: "부재중 전화 기록 보여 줘", tab: "home" },
  { stage: "매일 운영", key: "place_plus", label: "POS 연동 확인", hint: "포스기가 플레이스와 연결되는지", icon: "plug", ask: "우리 가게 POS가 네이버 플레이스랑 연동되는지 확인하고 싶어", tab: "home" },
  { stage: "손님 끌기", key: "search_keywords", label: "대표키워드 설정", hint: "검색에 잘 나오게 하는 단어", icon: "tag", ask: "대표키워드를 바꾸고 싶어", tab: "home" },
  { stage: "손님 끌기", key: "search_diagnosis", label: "검색 노출 진단", hint: "검색하면 몇 위에 나오는지", icon: "search", ask: "손님이 검색하면 우리 가게가 몇 위로 나와?", tab: "home" },
  { stage: "손님 끌기", key: "coupon", label: "플레이스 쿠폰", hint: "할인·혜택 쿠폰 만들기", icon: "ticket", ask: null, action_id: "coupon.create", tab: "home", inChat: true },
  { stage: "손님 끌기", key: "new_opening", label: "새로 오픈 소식 등록", hint: "오픈 날짜 알리기", icon: "flag", ask: "새로 오픈 소식을 등록하고 싶어", tab: "home" },
  { stage: "평판 관리", key: "review", label: "리뷰 확인·분석·답글", hint: "손님 반응 요약과 답글 초안", icon: "star", ask: "요즘 리뷰 어때?", action_id: "review.summary", tab: "reviews" },
  { stage: "돈 관리", key: "naverpay_signup", label: "네이버페이 가입", hint: "가입 방법과 준비할 것", icon: "card", ask: "네이버페이 가입하려면 어떻게 해?", tab: "home" },
  { stage: "돈 관리", key: "onsite_payment", label: "매장 QR결제 설정", hint: "가게에서 QR로 결제받기", icon: "qr", ask: "매장 QR결제를 설정하고 싶어", tab: "home" },
  { stage: "돈 관리", key: "settlement", label: "정산·입금 내역 조회", hint: "언제 얼마 들어오는지", icon: "coin", ask: "이번 달 정산·입금 내역 보여 줘", tab: "home" },
  { stage: "성과 확인", key: "statistics", label: "방문·유입 통계 조회", hint: "방문자·검색 유입·요일별 관심", icon: "chart", ask: "이번 주 손님 얼마나 왔어?", action_id: "stats.week", tab: "stats" },
  { stage: "학습·지원", key: "learning_support", label: "기능 사용법·교육 안내", hint: "교육·고객센터 도움말 찾기", icon: "book", ask: "스마트플레이스 쓰는 법을 배울 수 있는 교육 찾아 줘", tab: "home" },
];

/** 도구함 단계: 표에 처음 나온 순서. */
const STAGES = [...new Set(TOOLBOX.map((t) => t.stage))];

type Props = {
  recent: Recent[];
  current: string | null;
  busy: boolean;
  onNew: () => void;
  onOpen: (id: string) => void;
  onQuick: (q: Quick) => void;
  /** 자주 쓰는 일 옆 숫자 배지(예: reviews → 답글 기다리는 리뷰 수). 0이면 안 보인다. */
  badges?: Partial<Record<string, number>>;
  /** 접힌 사이드바: 아이콘만 보인다(넓은 화면). */
  collapsed?: boolean;
  onToggle?: () => void;
  /** [비서 제안]: 아직 치우지 않은 제안 수와 지금 그 페이지인지. */
  suggestCount?: number;
  suggestCurrent?: boolean;
  onSuggest?: () => void;
  /**
   * 자주 쓰는 일 맨 위 [전화 주문]: 누르면 사이드바 옆에 전화 주문 서랍이 열린다(App이 그린다).
   * orderCount: 확인 안 한 주문 수(빨간 배지, 0이면 숨김). ordersOpen: 서랍이 열려 있다.
   */
  onOrders?: () => void;
  orderCount?: number;
  ordersOpen?: boolean;
  /** 있으면 [새 대화] 자리에 [네이버 플레이스 불러오기](시연 컨셉)가 먼저 온다. 불러오면 [새 대화]로 돌아간다. */
  onNaverImported?: () => void;
};

export function LeftNav({ recent, current, busy, onNew, onOpen, onQuick, badges = {}, collapsed = false, onToggle, suggestCount = 0, suggestCurrent = false, onSuggest, onOrders, orderCount = 0, ordersOpen = false, onNaverImported }: Props) {
  const toggleLabel = collapsed ? "사이드바 펼치기" : "사이드바 접기";
  // 도구함: 맨 아래 버튼을 누르면 사이드바 옆에 전체 기능 패널이 열린다. Esc·바깥·도구 고르기로 닫는다.
  const [boxOpen, setBoxOpen] = useState(false);
  useEffect(() => {
    if (!boxOpen) return;
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") setBoxOpen(false);
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [boxOpen]);
  const pick = (t: Quick) => {
    setBoxOpen(false);
    onQuick(t);
  };

  return (
    <>
      <nav className="nav" aria-label="대화 목록" data-collapsed={collapsed}>
        {/* 위에는 로고만: 펼치면 AgentHan, 접으면 H/N 마크 */}
        <div className="brand">
          <BrandLogo collapsed={collapsed} />
        </div>
        {onNaverImported ? (
          <NaverImportButton onImported={onNaverImported} className="new-chat" compact={collapsed} />
        ) : (
        <button type="button" className="new-chat" onClick={onNew} disabled={busy} aria-label={collapsed ? "새 대화" : undefined} title={collapsed ? "새 대화" : undefined}>
          <Icon name="plus" />
          {!collapsed && "새 대화"}
        </button>
        )}
        {onSuggest && (
          <button
            type="button"
            className="nav-suggest"
            aria-label={suggestCount ? `비서 제안 (새 제안 ${suggestCount}개)` : "비서 제안"}
            aria-current={suggestCurrent ? "page" : undefined}
            title={collapsed ? "비서 제안" : undefined}
            onClick={onSuggest}
          >
            <Icon name="bulb" />
            {!collapsed && <span>비서 제안</span>}
            {suggestCount > 0 && (
              <span className="nav-suggest-badge" aria-hidden>
                {suggestCount}
              </span>
            )}
          </button>
        )}

        {!collapsed && <h2 className="nav-title">자주 쓰는 일</h2>}
        <ul className="quick">
          {onOrders && (
            <li>
              <button
                type="button"
                className="quick-orders"
                aria-label={orderCount ? `전화 주문 (확인 안 한 주문 ${orderCount}건)` : "전화 주문"}
                aria-describedby={collapsed ? undefined : "quick-orders"}
                aria-expanded={ordersOpen}
                aria-controls="orders-panel"
                title={collapsed ? "전화 주문" : undefined}
                onClick={() => {
                  setBoxOpen(false);
                  onOrders();
                }}
              >
                <span className="quick-icon" aria-hidden>
                  <Icon name="receipt" />
                </span>
                {!collapsed && (
                  <span className="quick-text">
                    <strong>전화 주문</strong>
                    <small id="quick-orders">들어온 주문 확인·거절</small>
                  </span>
                )}
                {orderCount > 0 && (
                  <span className="quick-badge" data-tone="urgent" aria-hidden>
                    {orderCount}
                  </span>
                )}
              </button>
            </li>
          )}
          {QUICK.map((q) => (
            <li key={q.key}>
              <button
                type="button"
                aria-label={q.label}
                aria-describedby={collapsed ? undefined : `quick-${q.key}`}
                title={collapsed ? q.label : undefined}
                onClick={() => onQuick(q)}
                disabled={busy}
              >
                <span className="quick-icon" aria-hidden>
                  <Icon name={q.icon} />
                </span>
                {!collapsed && (
                  <span className="quick-text">
                    <strong>{q.label}</strong>
                    <small id={`quick-${q.key}`}>{q.hint}</small>
                  </span>
                )}
                {Boolean(badges[q.key]) && (
                  <span className="quick-badge" aria-label={quickBadgeText(q.key, badges[q.key] ?? 0)}>
                    {badges[q.key]}
                  </span>
                )}
              </button>
            </li>
          ))}
        </ul>

        {!collapsed && <h2 className="nav-title">최근 대화</h2>}
        {collapsed ? null : recent.length === 0 ? (
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

        {/* 맨 아래 도구함 버튼 */}
        <div className="nav-foot">
          <button
            type="button"
            className="nav-toolbox"
            aria-label="AgentHan 도구함"
            aria-expanded={boxOpen}
            aria-controls="toolbox-panel"
            title={collapsed ? "AgentHan 도구함" : undefined}
            onClick={() => setBoxOpen((o) => !o)}
          >
            <span className="quick-icon" aria-hidden>
              <Icon name="box" />
            </span>
            {!collapsed && (
              <span className="quick-text">
                <strong>도구함</strong>
                <small>AgentHan이 할 수 있는 일 {TOOLBOX.length}가지</small>
              </span>
            )}
          </button>
        </div>
      </nav>

      {boxOpen && (
        <>
          <button type="button" className="toolbox-scrim" aria-label="도구함 닫기" tabIndex={-1} onClick={() => setBoxOpen(false)} />
          <div id="toolbox-panel" className="toolbox-panel" role="dialog" aria-label="AgentHan 도구함">
            <header className="toolbox-head">
              <div>
                <h2>도구함</h2>
                <p>누르면 비서가 바로 도와 드려요.</p>
              </div>
              <button type="button" className="toolbox-close" aria-label="도구함 닫기" onClick={() => setBoxOpen(false)}>
                <Icon name="close" />
              </button>
            </header>
            <div className="toolbox-body">
              {STAGES.map((stage) => (
                <section key={stage} className="toolbox-stage" aria-label={stage}>
                  <h3>{stage}</h3>
                  <ul>
                    {TOOLBOX.filter((t) => t.stage === stage).map((t) => (
                      <li key={t.key}>
                        <button type="button" aria-label={t.label} aria-describedby={`tool-${t.key}`} onClick={() => pick(t)} disabled={busy}>
                          <span className="quick-icon" aria-hidden>
                            <Icon name={t.icon} />
                          </span>
                          <strong>{t.label}</strong>
                          <small id={`tool-${t.key}`}>{t.hint}</small>
                        </button>
                      </li>
                    ))}
                  </ul>
                </section>
              ))}
            </div>
          </div>
        </>
      )}

      {/* 인덱스 손잡이: 사이드바 오른쪽 가장자리에 붙은 책갈피 탭으로 접고 편다(⌘B도 같다). */}
      {onToggle && (
        <button type="button" className="nav-handle" aria-label={toggleLabel} aria-expanded={!collapsed} title={`${toggleLabel} (⌘B)`} onClick={onToggle}>
          <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.4" strokeLinecap="round" strokeLinejoin="round" aria-hidden>
            <path d={collapsed ? "M9 6l6 6-6 6" : "M15 6l-6 6 6 6"} />
          </svg>
        </button>
      )}
    </>
  );
}
