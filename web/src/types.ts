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

export type Place = {
  menu: MenuRow[];
  business_hours: Record<string, DayHours>;
  coupon: Coupon[];
};

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

export type View = { place: Place; pending: Pending[] };

export type Action = { action_id: string; label: string; args: Record<string, unknown> };

export type Turn = View & { reply: string; next_actions?: Action[] };

export type Started = View & { session_id: string };

/** 대화창에 쌓이는 것: 사장님 말, 비서 답, 승인 카드가 끝난 결과. */
export type Message =
  | { id: number; role: "owner"; text: string; image?: string; hadImage?: boolean }
  /** cardFor: 이 답이 띄운 승인 카드의 task_id. 카드가 떠 있는 동안 말풍선은 첫 줄만 보인다. */
  | { id: number; role: "agent"; text: string; nextActions?: Action[]; cardFor?: string }
  | { id: number; role: "result"; saved: boolean; lines: string[] }
  /** 화면에서만 만든 알림(서버 대화 기록에 없음). reviewId: [리뷰 탭에서 보기]가 열 카드. */
  | { id: number; role: "notice"; text: string; reviewId: string };

// ── 리뷰·손님 통계 탭 (/reviews, /report) ──
export type ReviewRow = {
  id: string;
  visit_date: string;
  rating: number;
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
export type ReplyOpName = "post" | "skip" | "redraft";
export type ReplyOp = { op: "post"; text?: string } | { op: "skip" } | { op: "redraft"; hint: RedraftHint };
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
