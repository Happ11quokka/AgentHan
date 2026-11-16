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
  | { id: number; role: "agent"; text: string; nextActions?: Action[] }
  | { id: number; role: "result"; saved: boolean; lines: string[] };

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
};

export type ReviewsView = {
  summary: { count: number; average_rating: number | null; unanswered: number };
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
