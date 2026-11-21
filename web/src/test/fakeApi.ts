import { vi } from "vitest";
import { ApiError, type AssistantApi } from "../api";
import type { Action, DraftRow, OnboardingInput, Pending, Period, Place, ReplyOp, ReplyResult, ReportView, ReviewsView, Turn, View } from "../types";

export const SEED: Place = {
  menu: [
    { id: "m01", name: "메도빅", price: 7500, description: "꿀 케이크", photo_url: null },
    { id: "m02", name: "나폴레옹", price: 7000, description: "", photo_url: null },
  ],
  business_hours: {
    mon: { closed: false, open_time: "09:00", close_time: "21:00" },
    sun: { closed: true },
  },
  coupon: [],
};

export function draft(over: Partial<DraftRow> = {}): DraftRow {
  return {
    name_ko: "밀푀유 케이크",
    name_native: "",
    price: null,
    description: "",
    menu_id: null,
    change: "new",
    previous_price: null,
    menu_name: null,
    notes: ["food_guess", "price_missing"],
    photo_url: "/assistant/sessions/s1/photos/draft-0",
    ...over,
  };
}

export function photoPending(drafts: DraftRow[], after: Place | null = null): Pending {
  const ready = after !== null;
  return {
    task_id: "t1",
    feature: "menu",
    kind: "photo",
    drafts,
    after,
    changes: ready ? { menu: { added: ["m03"], updated: [], removed: [] } } : {},
  };
}

/** 버튼 경로의 리뷰 답글 초안 카드와 그 답(서버가 돌려주는 모양 그대로). */
export function reviewPending(over: Partial<Pending> = {}): Pending {
  return { task_id: "t5", version: 1, feature: "review", kind: "review", review_id: "r04", reply: "불편을 드려 죄송합니다. 간을 다시 맞추겠습니다.", ...over };
}
export const REVIEW_DRAFT_REPLY = "답글 초안이에요. 내용을 확인해 주세요.\n> 불편을 드려 죄송합니다. 간을 다시 맞추겠습니다.\n이대로 올릴까요?";
export const REVIEW_POSTED_REPLY = "답글을 저장했어요. 실제 발송은 하지 않았어요.";

export const withCake: Place = {
  ...SEED,
  menu: [
    ...SEED.menu,
    { id: "m03", name: "밀푀유 케이크", price: 8500, description: "", photo_url: "/p/m03" },
  ],
};

export const REVIEWS: ReviewsView = {
  summary: { count: 2, average_rating: 3, unanswered: 1, waiting: 0, preparing: 0 },
  reviews: [
    {
      id: "r04",
      visit_date: "2026-11-06",
      rating: 1,
      text: "빵이 짜요. (거친 표현 가림)",
      softened: true,
      original: "빵이 존나 짜요. 사장 인성 쓰레기.",
      has_owner_reply: false,
      draft: null,
    },
    {
      id: "r01",
      visit_date: "2026-11-01",
      rating: 5,
      text: "메도빅이 촉촉해요.",
      softened: false,
      original: null,
      has_owner_reply: true,
      draft: null,
    },
  ],
};

/** 답글 대기함: 초안 셋(1점 리뷰는 확인 필요, 5점 둘은 준비됨)과 답글 달린 리뷰 하나. */
export const INBOX: ReviewsView = {
  summary: { count: 4, average_rating: 3.8, unanswered: 3, waiting: 3, preparing: 0 },
  reviews: [
    {
      id: "r20",
      visit_date: "2026-11-08",
      rating: 5,
      text: "메도빅 최고예요.",
      softened: false,
      original: null,
      has_owner_reply: false,
      draft: { text: "맛있게 드셔 주셔서 감사합니다.", status: "ready", issues: [] },
    },
    {
      id: "r19",
      visit_date: "2026-11-07",
      rating: 1,
      text: "케이크가 너무 달아요.",
      softened: false,
      original: null,
      has_owner_reply: false,
      draft: { text: "연락 주시면 010-1234-5678로 안내드릴게요.", status: "needs_check", issues: ["전화번호 등 개인정보"] },
    },
    {
      id: "r18",
      visit_date: "2026-11-06",
      rating: 5,
      text: "커피가 맛있어요.",
      softened: false,
      original: null,
      has_owner_reply: false,
      draft: { text: "다음에도 찾아 주세요.", status: "ready", issues: [] },
    },
    {
      id: "r01",
      visit_date: "2026-11-01",
      rating: 4,
      text: "자리가 편해요.",
      softened: false,
      original: null,
      has_owner_reply: true,
      draft: null,
    },
  ],
};

/** INBOX에서 한 리뷰만 바꾼 화면(올림·건너뜀 뒤 다시 읽은 모양). */
export function inboxWith(id: string, over: Partial<ReviewsView["reviews"][number]>, summary: Partial<ReviewsView["summary"]> = {}): ReviewsView {
  return {
    summary: { ...INBOX.summary, ...summary },
    reviews: INBOX.reviews.map((r) => (r.id === id ? { ...r, ...over } : r)),
  };
}

export function report(period: Period = "week"): ReportView {
  const views = period === "week" ? 1260 : 180;
  return {
    period,
    end: "2026-11-08",
    cards: [
      { key: "place_views", value: views, change: 60, change_percent: 5 },
      { key: "booking_order_requests", value: 84, change: -6, change_percent: -6.7 },
      { key: "smartcall_calls", value: 35, change: null, change_percent: null },
    ],
    channels: [
      { name: "네이버 검색", views: 700, share_percent: 55.6, change: 40 },
      { name: "네이버 지도", views: 560, share_percent: 44.4, change: 20 },
    ],
    hourly: Array.from({ length: 24 }, (_, h) => (h === 14 ? 200 : 10)),
    weekday: ["mon", "tue", "wed", "thu", "fri", "sat", "sun"].map((day) => ({ day, views: 180 })),
    peak_hour: 14,
    quiet_hour: 3,
    keywords: [
      { name: "러시아 디저트", views: 120, new: true },
      { name: "메도빅", views: 90, new: false },
    ],
    conversion_percent: 6.7,
    conversion_change_pp: -0.8,
    tip: "조회는 늘었지만 예약·주문으로 이어지는 비율은 줄었어요",
  };
}

type Script = Array<Turn | Error>;

/** 순서대로 답하는 가짜 API. say·sendPhoto는 같은 대본을 공유한다. */
export function fakeApi(script: Script = [], view: View | Error = { place: SEED, pending: [] }) {
  const queue = [...script];
  let sessions = 0;
  const next = async (): Promise<Turn> => {
    const item = queue.shift();
    if (item === undefined) return { reply: "알겠어요.", place: SEED, pending: [] };
    if (item instanceof Error) throw item;
    return item;
  };
  const api = {
    start: vi.fn(async () => ({ session_id: `s${++sessions}`, place: SEED, pending: [] })),
    act: vi.fn(async (_id: string, _action: Action) => next()),
    say: vi.fn(async (_id: string, _text: string) => next()),
    sendPhoto: vi.fn(async (_id: string, _image: File, _text: string) => next()),
    view: vi.fn(async (_id: string) => {
      if (view instanceof Error) throw view;
      return view;
    }),
    onboard: vi.fn(async (_id: string, values: OnboardingInput): Promise<View> => ({ place: SEED, pending: [], shop: { name: values.name, category: values.category } })),
    reviews: vi.fn(async (_id: string) => REVIEWS),
    simulateReview: vi.fn(async (_id: string) => ({ ...REVIEWS, review_id: "sim-01" })),
    // 기본: 그 리뷰를 그대로 돌려준다(테스트마다 mockResolvedValue로 결과를 정한다).
    replyToReview: vi.fn(async (_id: string, reviewId: string, _op: ReplyOp): Promise<ReplyResult> => {
      const review = INBOX.reviews.find((r) => r.id === reviewId) ?? INBOX.reviews[0];
      return { review, summary: INBOX.summary };
    }),
    report: vi.fn(async (_id: string, period: Period) => report(period)),
    runReviewReport: vi.fn(async (_id: string): Promise<View> => ({ place: SEED, pending: [], messages: [] })),
  } satisfies AssistantApi;
  return api;
}

export const offline = () => new ApiError(0, "서버에 연결하지 못했어요.");
export const gone = () => new ApiError(404, "없는 대화입니다.");
