import type { Action, OnboardingInput, Period, ReplyOp, ReplyResult, ReportView, ReviewsView, Started, Turn, View } from "./types";

/** 화면이 서버와 주고받는 유일한 길. 테스트에서는 가짜로 바꿔 끼운다. */
export interface AssistantApi {
  start(): Promise<Started>;
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
  start: () => call("/assistant/sessions", { method: "POST" }),
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
};
