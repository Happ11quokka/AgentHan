// /assistant API 응답 모양 (src/agenthan/app/server.py, place.py). 화면은 이 타입만 본다.

export type MenuRow = {
  id: string;
  name: string;
  price: number | null;
  description: string | null;
  photo_url: string | null;
};

export type DayHours = { closed: boolean; open_time?: string; close_time?: string };

export type Coupon = {
  id: string;
  title: string;
  discount_type: "percent" | "amount";
  discount_value: number;
  start_date: string;
  end_date: string;
  status?: string;
};

/**
 * 하나씩 골라 바꾸는 기록(톡톡 문의·주문·예약, place.py _record_rows).
 * label: 기록을 서로 구별하는 필드, fields: 나머지 필드. 화면은 필드 이름을 몰라도
 * before/after 값이 다른 필드만 골라 보여 준다.
 */
export type RecordRow = { id: string; label: Record<string, unknown>; fields: Record<string, unknown> };

/** 기록 섹션 이름(기능 id). 예전 서버 응답에는 없을 수 있다. */
export type RecordSection = "talktalk" | "order" | "reservation";

export type Place = {
  menu: MenuRow[];
  business_hours: Record<string, DayHours>;
  coupon: Coupon[];
  /** 대표키워드(검색어). 예전 서버 응답에는 없을 수 있다. */
  keywords?: string[];
} & Partial<Record<RecordSection, RecordRow[]>>;

export type SectionChanges = { added: string[]; updated: string[]; removed: string[] };

export type DraftChange = "new" | "price_change" | "unchanged" | "photo_attach";

export type DraftRow = {
  name_ko: string;
  name_native: string;
  price: number | null;
  description: string;
  menu_id: string | null;
  change: DraftChange | null;
  previous_price: number | null;
  /** 이 초안이 맞는 저장된 메뉴 이름(없으면 null). 사진만 붙일 때 답할 이름이다. */
  menu_name: string | null;
  notes: string[];
  photo_url: string | null;
  /** 음식 사진 초안. 이름은 사장님이 카드에 입력한다(#93). 예전 서버 응답에는 없을 수 있다. */
  is_food?: boolean;
  /** 음식 사진에서 AI가 추측한 이름. 확정값이 아니라 입력칸 자리표시자로만 쓴다. */
  name_guess?: string | null;
};

export type Pending = {
  task_id: string;
  version?: number;
  reply?: string;
  review_id?: string;
  feature: string;
  after?: Place | null;
  changes?: Partial<Record<keyof Place, SectionChanges>>;
  error?: string;
  kind?: "photo" | "review";
  drafts?: DraftRow[];
};

/** 가게 이름·업종. 둘 중 하나라도 비면 처음 쓰는 가게라 온보딩을 띄운다(#94). 예전 서버 응답에는 없다. */
export type ShopInfo = { name: string; category: string };

/** 리뷰 정기 보고 설정 한 줄(#98). text: "매일 09:00 리뷰 요약 받는 중" / "정기 보고 꺼짐". */
export type ReviewReport = { enabled: boolean; schedule: string | null; next_run: string | null; text: string };

/** 사장님 말 없이 비서가 먼저 보낸 메시지(정해진 때의 리뷰 보고 등). 서버는 한 번만 건넨다. */
export type ServerMessage = { id: string; role: "agent"; text: string; next_actions?: Action[] };

/** review_report·messages: 예전 서버 응답에는 없다. */
export type View = { place: Place; pending: Pending[]; shop?: ShopInfo; review_report?: ReviewReport; messages?: ServerMessage[] };

/** 온보딩에서 사장님이 직접 넣은 값만 (POST /onboarding). 메뉴는 0~3개. */
export type OnboardingInput = ShopInfo & { menus: Array<{ name: string; price: number }> };

export type Action = { action_id: string; label: string; args: Record<string, unknown> };

export type Turn = View & { reply: string; next_actions?: Action[] };

export type Started = View & { session_id: string };

/** 대화창에 쌓이는 것: 사장님 말, 비서 답, 승인 카드가 끝난 결과. */
export type Message =
  | { id: number; role: "owner"; text: string; image?: string; hadImage?: boolean }
  /** cardFor: 이 답이 띄운 승인 카드의 task_id. 카드가 떠 있는 동안 말풍선은 첫 줄만 보인다. */
  /** previews: 이 답이 띄운 승인 카드마다 바뀌는 줄(카드 순서). 말풍선의 "…아래처럼 바꿀까요?" 밑에 보인다. */
  | { id: number; role: "agent"; text: string; nextActions?: Action[]; cardFor?: string; previews?: string[][] }
  | { id: number; role: "result"; saved: boolean; lines: string[] }
  /** 화면에서만 만든 알림(서버 대화 기록에 없음). reviewId: [리뷰 탭에서 보기]가 열 카드. */
  | { id: number; role: "notice"; text: string; reviewId: string };

// ── 리뷰·손님 통계 탭 (/reviews, /report) ──
export type ReviewRow = {
  id: string;
  visit_date: string;
  /** 네이버 리뷰는 별점이 없는 경우가 많다(null). */
  rating: number | null;
  /** 거친 표현을 거른 문장. 거를 게 없으면 원문 그대로. */
  text: string;
  softened: boolean;
  /** 걸렀을 때만 원문. */
  original: string | null;
  has_owner_reply: boolean;
  /** 답글 대기함이 미리 써 둔 초안(review/inbox.py). 아직 없으면 null. */
  draft: ReplyDraft | null;
};

export type DraftStatus = "ready" | "needs_check" | "posted" | "skipped" | "failed";
/** issues: needs_check일 때 걸린 검사 항목. */
export type ReplyDraft = { text: string; status: DraftStatus; issues: string[] };
/** [다시 쓰기] 칩이 보낼 수 있는 값(서버 inbox.HINTS와 같은 것만). */
export type RedraftHint = "shorter" | "more_apologetic" | "warmer";

/** 답글 대기함 카드 버튼 (POST /reviews/{id}/reply). 대화 턴이 아니다. */
export type ReplyOpName = "draft" | "post" | "skip" | "redraft";
export type ReplyOp = { op: "draft" } | { op: "post"; text?: string } | { op: "skip" } | { op: "redraft"; hint: RedraftHint };
/** reason: checks(검사 거절) | replied | busy | empty | save_failed | draft_failed | server(화면: 서버에 닿지 못함). */
export type ReplyError = { reason: string; message: string; issues: string[] };
export type ReplyResult = { review: ReviewRow; summary: ReviewsView["summary"]; error?: ReplyError };

export type ReviewsView = {
  /** waiting: 올리기만 기다리는 초안 수(ready+needs_check), preparing: 지금 쓰는 중인 초안 수. */
  summary: { count: number; average_rating: number | null; unanswered: number; waiting: number; preparing: number };
  reviews: ReviewRow[];
};

export type Period = "week" | "day";
export type MetricKey = "place_views" | "booking_order_requests" | "smartcall_calls";

export type ReportView = {
  period: Period;
  end: string;
  cards: Array<{ key: MetricKey; value: number; change: number | null; change_percent: number | null }>;
  channels: Array<{ name: string; views: number; share_percent: number; change: number | null }>;
  hourly: number[];
  weekday: Array<{ day: string; views: number }>;
  peak_hour: number | null;
  quiet_hour: number | null;
  keywords: Array<{ name: string; views: number; new: boolean }>;
  conversion_percent: number | null;
  conversion_change_pp: number | null;
  tip: string | null;
};

/** '오늘 한 일' 한 칸: 승인으로 저장된 변경 문장들. */
export type LogEntry = { at: number; lines: string[] };
