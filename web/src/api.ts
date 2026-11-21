import type { Action, MenuEdit, OnboardingInput, Period, ReplyOp, ReplyResult, ReportView, ReviewStats, ReviewStatsPeriod, ReviewsView, RoutinesChange, Started, Suggestions, Turn, View } from "./types";

/** 화면이 서버와 주고받는 유일한 길. 테스트에서는 가짜로 바꿔 끼운다. */
export interface AssistantApi {
  /** emptyMenu: 시연용, 메뉴만 비운 가게로 시작한다(메뉴판 사진으로 채우는 장면). */
  start(options?: { emptyMenu?: boolean }): Promise<Started>;
  act(sessionId: string, action: Action): Promise<Turn>;
  say(sessionId: string, text: string): Promise<Turn>;
  sendPhoto(sessionId: string, image: File, text: string): Promise<Turn>;
  view(sessionId: string): Promise<View>;
  /** 처음 쓰는 사장님: 가게 이름·업종·대표 메뉴를 그 대화의 가게 저장소에 저장한다. 대화 턴이 아니다. */
  onboard(sessionId: string, values: OnboardingInput): Promise<View>;
  reviews(sessionId: string): Promise<ReviewsView>;
  /** 데모: 더미 리뷰 한 건을 더하고 그 리뷰의 초안을 쓰기 시작한다. */
  simulateReview(sessionId: string): Promise<ReviewsView & { review_id: string }>;
  /** 답글 대기함 카드 버튼: 대화 턴이 아니라 그 리뷰만 바꾼다(대화 기록에 남지 않음). */
  replyToReview(sessionId: string, reviewId: string, op: ReplyOp): Promise<ReplyResult>;
  report(sessionId: string, period: Period): Promise<ReportView>;
  /** 리뷰 탭 [지금 받아 보기]: 리뷰 보고 한 회분을 대화 메시지(View.messages)로 받는다. 설정은 그대로. */
  runReviewReport(sessionId: string): Promise<View>;
  /** 루틴 설정 스위치·일정(#33): 사장님이 직접 바꾼 값이라 승인 카드 없이 저장한다. 대화 턴이 아니다. */
  setRoutines(sessionId: string, change: RoutinesChange): Promise<View>;
  /** 통계 탭 › AI 리뷰 통계. */
  reviewStats(sessionId: string, period: ReviewStatsPeriod): Promise<ReviewStats>;
  /** 왼쪽 [비서 제안]: 지난주 통계와 리뷰로 고른 해 볼 일. */
  suggestions(sessionId: string): Promise<Suggestions>;
  /** 메뉴 탭에서 사장님이 직접 고친 값: 승인 카드 없이 바로 저장한다. 대화 턴이 아니다. */
  editMenu(sessionId: string, menuId: string, edit: MenuEdit): Promise<View>;
  /** [사진 다듬기] 2차: CPU 스튜디오 보정(몇 초), GPU 서버가 있으면 SEED Omni(1~2분). message: 사장님께 보일 한 줄. */
  polishMenu(sessionId: string, menuId: string): Promise<View & { message: string; started: boolean }>;
  /** 가게 정보 입력 블록 [추천 글 받기]: 가게 자료로 쓴 소개글 초안. 대화 턴이 아니고 저장하지 않는다. */
  draftIntroduction(sessionId: string): Promise<{ text: string }>;
  /** 메뉴판에서 잘못 읽은 줄을 지운다. 대화 턴이 아니다. */
  deleteMenu(sessionId: string, menuId: string): Promise<View>;
}

export class ApiError extends Error {
  constructor(
    readonly status: number,
    message: string,
  ) {
    super(message);
  }
}

async function call<T>(path: string, init?: RequestInit): Promise<T> {
  let response: Response;
  try {
    response = await fetch(path, init);
  } catch {
    throw new ApiError(0, "서버에 연결하지 못했어요.");
  }
  if (!response.ok) throw new ApiError(response.status, `요청이 실패했어요 (${response.status}).`);
  return (await response.json()) as T;
}

const base = (id: string) => `/assistant/sessions/${encodeURIComponent(id)}`;

export const httpApi: AssistantApi = {
  start: (options) => call(`/assistant/sessions${options?.emptyMenu ? "?menu=empty" : ""}`, { method: "POST" }),
  act: (id, action) =>
    call(`${base(id)}/actions`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(action),
    }),
  say: (id, text) =>
    call(`${base(id)}/messages`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ text }),
    }),
  sendPhoto: (id, image, text) => {
    const form = new FormData();
    form.append("image", image);
    if (text) form.append("text", text);
    return call(`${base(id)}/photos`, { method: "POST", body: form });
  },
  view: (id) => call(`${base(id)}/place`),
  onboard: (id, values) =>
    call(`${base(id)}/onboarding`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(values),
    }),
  reviews: (id) => call(`${base(id)}/reviews`),
  simulateReview: (id) => call(`${base(id)}/reviews/simulate`, { method: "POST" }),
  replyToReview: (id, reviewId, op) =>
    call(`${base(id)}/reviews/${encodeURIComponent(reviewId)}/reply`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(op),
    }),
  report: (id, period) => call(`${base(id)}/report?period=${period}`),
  runReviewReport: (id) => call(`${base(id)}/reviews/report`, { method: "POST" }),
  setRoutines: (id, change) =>
    call(`${base(id)}/routines`, {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(change),
    }),
  reviewStats: (id, period) => call(`${base(id)}/reviews/stats?period=${period}`),
  suggestions: (id) => call(`${base(id)}/suggestions`),
  editMenu: (id, menuId, edit) =>
    call(`${base(id)}/menus/${encodeURIComponent(menuId)}`, {
      method: "PATCH",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(edit),
    }),
  polishMenu: (id, menuId) => call(`${base(id)}/menus/${encodeURIComponent(menuId)}/polish`, { method: "POST" }),
  draftIntroduction: (id) => call(`${base(id)}/introduction/draft`, { method: "POST" }),
  deleteMenu: (id, menuId) => call(`${base(id)}/menus/${encodeURIComponent(menuId)}`, { method: "DELETE" }),
};
