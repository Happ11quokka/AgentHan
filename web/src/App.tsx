import { useCallback, useEffect, useRef, useState } from "react";
import { httpApi, type AssistantApi } from "./api";
import { ChatPane } from "./components/ChatPane";
import type { StatsKind } from "./components/StatsTab";
import { SuggestionsPage } from "./components/SuggestionsPage";
import { LeftNav, type Quick } from "./components/LeftNav";
import { Onboarding } from "./components/Onboarding";
import { Sidecar, type Tab } from "./components/Sidecar";
import { REPLY_SERVER_ERROR, replyNoticeText, REVIEW_POSTED_TEXT } from "./labels";
import type { OnboardingInput, Period, ReplyError, ReplyOp, ReplyResult, ReviewRow, ReviewStatsPeriod, RoutinesChange, Suggestion } from "./types";
import { useConversation } from "./useConversation";
import { useLoad } from "./useLoad";

/** 접은 왼쪽 사이드바를 다음에도 기억한다(브라우저마다). */
const NAV_COLLAPSED_KEY = "agenthan.nav.collapsed";
function loadCollapsed() {
  try {
    return localStorage.getItem(NAV_COLLAPSED_KEY) === "1";
  } catch {
    return false;
  }
}

/** 답글 대기함 초안을 쓰는 동안 리뷰를 다시 읽는 간격. */
const REVIEW_POLL_MS = 2500;
/** 카드 버튼이 실패했을 때 채팅에도 알릴 까닭: 검사에 걸려 막힘, 저장·초안 쓰기 실패(서버 쪽 오류). */
const NOTICE_REASONS = new Set(["checks", "save_failed", "draft_failed"]);

/** reportPollMs: 정해진 때의 리뷰 보고를 받으려고 세션 조회를 다시 읽는 간격(0이면 안 읽음). */
type AppProps = { api?: AssistantApi; reviewPollMs?: number; reportPollMs?: number; demo?: boolean };

export default function App({ api = httpApi, reviewPollMs = REVIEW_POLL_MS, reportPollMs, demo = import.meta.env.DEV }: AppProps) {
  const chat = useConversation(api, reportPollMs);
  const [tab, setTab] = useState<Tab>("home");
  const [period, setPeriod] = useState<Period>("week");
  const [navOpen, setNavOpen] = useState(false);
  const [sideOpen, setSideOpen] = useState(false);
  const [hasInput, setHasInput] = useState(false);
  const photoInput = useRef<HTMLInputElement>(null);

  // 리뷰·통계는 그 탭이 열려 있을 때만 읽고, 대화가 바뀌거나 비서 답이 오면 다시 읽는다.
  const id = chat.sessionId;
  const turn = `${id}:${chat.messages.length}`;
  // 리뷰는 왼쪽 배지(기다리는 수) 때문에 대화마다 한 번은 읽는다. 탭이 열려 있으면 답이 올 때마다 다시 읽는다.
  const reviews = useLoad(id ? () => api.reviews(id) : null, tab === "reviews" ? `${turn}:${tab}` : `${id}`);
  const reloadReviews = reviews.reload;
  const preparing = reviews.data?.summary.preparing ?? 0;
  useEffect(() => {
    if (!preparing) return; // 초안을 다 쓰면 멈춘다
    const timer = setTimeout(reloadReviews, reviewPollMs);
    return () => clearTimeout(timer);
  }, [reviews.data, preparing, reloadReviews, reviewPollMs]);
  const simulateReview = async () => {
    if (!id) return;
    await api.simulateReview(id).catch(() => undefined);
    reloadReviews();
  };
  // 대기함 카드 버튼: 대화 턴이 아니다. 결과로 그 카드와 요약만 바꾸고 채팅 말풍선은 만들지 않는다.
  // 채팅에는 다시 봐야 할 것(검사에 걸려 막힘, 서버 오류)만 화면 알림 한 개로 올린다.
  const [focusReview, setFocusReview] = useState<{ id: string; at: number } | null>(null);
  const { update: updateReviews } = reviews;
  const { addLog, notify } = chat;
  const replyToReview = useCallback(
    async (review: ReviewRow, op: ReplyOp): Promise<ReplyError | null> => {
      if (!id) return null;
      // 초안 받기는 사이드카 안에서만: 실패해도 채팅에 알리지 않고 카드에만 보인다.
      const quiet = op.op === "draft";
      let result: ReplyResult;
      try {
        result = await api.replyToReview(id, review.id, op);
      } catch {
        if (!quiet) notify(replyNoticeText(review, REPLY_SERVER_ERROR), review.id);
        return REPLY_SERVER_ERROR;
      }
      updateReviews((v) => ({ summary: result.summary, reviews: v.reviews.map((r) => (r.id === result.review.id ? result.review : r)) }));
      const { error } = result;
      if (!error) {
        if (op.op === "post") addLog([REVIEW_POSTED_TEXT]);
        return null;
      }
      if (!quiet && NOTICE_REASONS.has(error.reason)) notify(replyNoticeText(result.review, error), review.id);
      return error;
    },
    [api, id, addLog, notify, updateReviews],
  );
  const showReview = (reviewId: string) => {
    setTab("reviews");
    setSideOpen(true);
    setFocusReview({ id: reviewId, at: Date.now() });
  };
  // 루틴 설정(#33): 초안 미리 쓰기를 켜면 서버가 바로 쓰기 시작하니 리뷰(쓰는 중 수)를 다시 읽는다.
  const { changeRoutines: saveRoutines } = chat;
  const changeRoutines = useCallback(
    async (change: RoutinesChange) => {
      const error = await saveRoutines(change);
      if (!error && change.drafts) reloadReviews();
      return error;
    },
    [saveRoutines, reloadReviews],
  );
  // 메뉴판 사진을 올려 메뉴가 채워지기 시작하면 메뉴 탭을 열어 채워지는 모습을 보인다.
  // (이전 렌더의 값과 비교해 렌더 중에 바꾼다: 효과 안 setState를 피한다)
  const filling = Boolean(chat.place?.menu.some((m) => m.filling?.length)) || chat.readingSince !== null;
  const [wasFilling, setWasFilling] = useState(false);
  if (filling !== wasFilling) {
    setWasFilling(filling);
    if (filling) {
      setTab("menu");
      setSideOpen(true);
    }
  }
  const report = useLoad(tab === "stats" && id ? () => api.report(id, period) : null, `${turn}:${tab}:${period}`);
  // 통계 탭 › AI 리뷰 통계: 그 화면을 볼 때만 읽는다.
  const [statsKind, setStatsKind] = useState<StatsKind>("visits");
  const [reviewPeriod, setReviewPeriod] = useState<ReviewStatsPeriod>("week");
  const reviewStats = useLoad(
    tab === "stats" && statsKind === "reviews" && id ? () => api.reviewStats(id, reviewPeriod) : null,
    `${turn}:${tab}:${statsKind}:${reviewPeriod}`,
  );

  // 왼쪽 사이드바 접기: 버튼과 ⌘B(Ctrl+B). 넓은 화면에서만 아이콘 줄이 된다.
  const [navCollapsed, setNavCollapsed] = useState(loadCollapsed);
  const toggleNav = useCallback(() => {
    setNavCollapsed((c) => {
      try {
        localStorage.setItem(NAV_COLLAPSED_KEY, c ? "0" : "1");
      } catch {
        // 저장 못 해도 이번 화면에서는 접힌다
      }
      return !c;
    });
  }, []);
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if ((e.metaKey || e.ctrlKey) && !e.altKey && !e.shiftKey && e.key.toLowerCase() === "b") {
        e.preventDefault();
        toggleNav();
      }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [toggleNav]);

  // 왼쪽 [비서 제안]: 대화마다 한 번 읽고, [나중에]로 치운 카드는 그 대화 동안 숨긴다.
  const [page, setPage] = useState<"chat" | "suggest">("chat");
  const suggestions = useLoad(id ? () => api.suggestions(id) : null, `${id}`);
  const [dismissed, setDismissed] = useState<{ id: string | null; kinds: string[] }>({ id: null, kinds: [] });
  const hidden = dismissed.id === id ? dismissed.kinds : [];
  const ideas = (suggestions.data?.items ?? []).filter((i) => !hidden.includes(i.kind));
  const actOnIdea = (item: Suggestion) => {
    if (item.tab) {
      setTab(item.tab);
      setSideOpen(true);
      return;
    }
    if (item.ask) {
      setPage("chat");
      void chat.send(item.ask);
    }
  };
  const dismissIdea = (item: Suggestion) => setDismissed({ id, kinds: [...hidden, item.kind] });

  // 처음 쓰는 사장님(#94): 가게 이름이나 업종이 빈 대화는 온보딩부터. 데모는 ?onboarding=1로 늘 띄운다.
  const [forceOnboarding, setForceOnboarding] = useState(() => new URLSearchParams(window.location.search).get("onboarding") === "1");
  const { shop, onboard } = chat;
  const finishOnboarding = useCallback(
    async (values: OnboardingInput) => {
      const error = await onboard(values);
      if (!error) setForceOnboarding(false);
      return error;
    },
    [onboard],
  );
  if (shop && (forceOnboarding || !shop.name.trim() || !shop.category.trim()))
    return <Onboarding key={chat.sessionId} initial={shop} onSubmit={finishOnboarding} />;

  const quick = (q: Quick) => {
    setNavOpen(false);
    setPage("chat");
    setTab(q.tab);
    if (!q.inChat) setSideOpen(true); // 좁은 화면에서는 접힌 사이드카를 펼쳐 보여 준다
    if (q.action_id) void chat.act({ action_id: q.action_id, args: {}, label: q.label });
    else if (q.ask) void chat.send(q.ask);
    else photoInput.current?.click();
  };

  return (
    <div className="app" data-nav={navOpen ? "open" : "closed"} data-side={sideOpen ? "open" : "closed"} data-nav-collapsed={navCollapsed}>
      <LeftNav
        recent={chat.recent}
        current={chat.sessionId}
        busy={chat.busy}
        onNew={() => {
          setNavOpen(false);
          setPage("chat");
          void chat.newChat();
        }}
        onOpen={(target) => {
          setNavOpen(false);
          setPage("chat");
          void chat.open(target);
        }}
        onQuick={quick}
        badges={{ reviews: reviews.data?.summary.waiting ?? 0 }}
        // 좁은 화면에서 목록을 열면 늘 펼쳐 보인다(접기는 넓은 화면의 아이콘 줄).
        collapsed={navCollapsed && !navOpen}
        onToggle={toggleNav}
        suggestCount={ideas.length}
        suggestCurrent={page === "suggest"}
        onSuggest={() => {
          setNavOpen(false);
          setPage("suggest");
        }}
      />
      <ChatPane
        chat={chat}
        photoInput={photoInput}
        sideOpen={sideOpen}
        onToggleNav={() => setNavOpen((o) => !o)}
        onToggleSide={() => setSideOpen((o) => !o)}
        onShowReview={showReview}
        onInputChange={setHasInput}
        hidden={page === "suggest"}
      />
      {page === "suggest" && <SuggestionsPage loaded={suggestions} items={ideas} busy={chat.busy} onAct={actOnIdea} onDismiss={dismissIdea} />}
      <Sidecar
        tab={tab}
        onTab={setTab}
        place={chat.place}
        pending={chat.pending}
        online={chat.online}
        busy={chat.busy}
        reviews={reviews}
        hasInput={hasInput}
        report={report}
        period={period}
        onPeriod={setPeriod}
        onAction={(action) => void chat.act(action)}
        onSimulateReview={demo ? () => void simulateReview() : undefined}
        onReply={replyToReview}
        focusReview={focusReview}
        reviewReport={chat.reviewReport}
        onRunReport={chat.runReport}
        routines={chat.routines}
        onRoutines={changeRoutines}
        statsKind={statsKind}
        onStatsKind={setStatsKind}
        reviewStats={reviewStats}
        reviewPeriod={reviewPeriod}
        onReviewPeriod={setReviewPeriod}
        onAsk={(text) => {
          setPage("chat");
          void chat.send(text);
        }}
        onEditMenu={chat.editMenu}
        onDeleteMenu={chat.deleteMenu}
        onPolishMenu={chat.polishMenu}
        readingSince={chat.readingSince}
        onClose={() => setSideOpen(false)}
      />
      <button
        type="button"
        className="scrim"
        aria-label="닫기"
        tabIndex={-1}
        onClick={() => {
          setNavOpen(false);
          setSideOpen(false);
        }}
      />
    </div>
  );
}
