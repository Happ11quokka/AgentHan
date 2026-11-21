// 서버의 코드·숫자를 사장님 말로 바꾸는 유일한 곳. 화면 다른 곳은 코드나 문구 표를 직접 두지 않는다.
import type { Coupon, DayHours, DraftRow, MetricKey, Pending, Period, Place, RecordRow, RedraftHint, ReplyError, ReplyOpName, ReviewReport, ReviewRow } from "./types";

export const won = (n: number) => `${n.toLocaleString("ko-KR")}원`;
const num = (n: number) => n.toLocaleString("ko-KR");

// ── 시간 ──────────────────────────────
export const sameDay = (a: number, b: number) => new Date(a).toDateString() === new Date(b).toDateString();
export const clockText = (at: number) =>
  new Date(at).toLocaleTimeString("ko-KR", { hour: "numeric", minute: "2-digit" });
/** 오늘이면 시각, 아니면 날짜. */
export const whenText = (at: number) =>
  sameDay(at, Date.now()) ? clockText(at) : new Date(at).toLocaleDateString("ko-KR", { month: "numeric", day: "numeric" });

// ── 가게 화면 ──────────────────────────
export const DAYS: Array<[string, string]> = [
  ["mon", "월요일"],
  ["tue", "화요일"],
  ["wed", "수요일"],
  ["thu", "목요일"],
  ["fri", "금요일"],
  ["sat", "토요일"],
  ["sun", "일요일"],
];

export const hoursText = (h: DayHours | undefined) =>
  !h ? "정보 없음" : h.closed ? "쉬는 날" : `${h.open_time} ~ ${h.close_time}`;

export const discountText = (c: Pick<Coupon, "discount_type" | "discount_value">) =>
  c.discount_type === "percent" ? `${c.discount_value}% 할인` : `${won(c.discount_value)} 할인`;

const monthDay = (iso: string) => iso.slice(5).replace("-", "/");
export const periodText = (c: Pick<Coupon, "start_date" | "end_date">) => `${monthDay(c.start_date)} ~ ${monthDay(c.end_date)}`;

// ── 쿠폰 입력 블록 ─────────────────────
/** 블록 칸의 값. discount_value는 입력칸 글자 그대로 둔다(비운 칸을 0과 구별). */
export type CouponInput = {
  title: string;
  discount_type: Coupon["discount_type"];
  discount_value: string;
  start_date: string;
  end_date: string;
};

export const COUPON_FORM_TEXT = {
  heading: "쿠폰 만들기",
  title: "쿠폰 이름",
  titlePlaceholder: "예: 겨울 맞이 할인",
  type: "할인 방식",
  amount: "원",
  percent: "%",
  value: "할인 값",
  start: "시작일",
  end: "종료일",
  cancel: "취소",
  submit: "미리 보기",
} as const;

/** [미리 보기]를 막는 이유 한 줄. 막을 게 없으면 null. */
export function couponProblem(c: CouponInput): string | null {
  if (!c.title.trim()) return "쿠폰 이름을 적어 주세요.";
  const value = Number(c.discount_value);
  if (!c.discount_value.trim() || !Number.isInteger(value) || value < 1) return "할인 값을 1 이상의 숫자로 적어 주세요.";
  if (c.discount_type === "percent" && value > 100) return "% 할인은 1~100 사이로 적어 주세요.";
  if (!c.start_date || !c.end_date) return "시작일과 종료일을 골라 주세요.";
  if (c.end_date < c.start_date) return "종료일이 시작일보다 빨라요.";
  return null;
}

/** 블록 아래 미리보기 한 줄: "겨울 할인 · 10% 할인 · 11/17 ~ 12/01". */
export function couponPreview(c: CouponInput): string {
  const parts = [c.title.trim() || COUPON_FORM_TEXT.title];
  const value = Number(c.discount_value);
  if (c.discount_value.trim() && Number.isFinite(value)) parts.push(discountText({ discount_type: c.discount_type, discount_value: value }));
  if (c.start_date && c.end_date) parts.push(periodText(c));
  return parts.join(" · ");
}

export const couponActionLabel = (c: CouponInput) => `쿠폰 만들기: ${c.title.trim()}`;

export type RowMark = "added" | "updated" | "removed";
export const MARK_TEXT: Record<RowMark, string> = { added: "추가 예정", updated: "바뀔 예정", removed: "내릴 예정" };

/** 여러 변경 카드가 한 번에 저장될 때 카드 버튼 대신 보이는 안내. */
export const BUNDLED_TEXT = "위의 [모두 저장]·[모두 취소]로 함께 저장하거나 취소해요.";

export const RESULT_TEXT = { saved: "저장 완료", skipped: "저장 안 함" } as const;
export const CONNECTION_TEXT = { online: "연결됨", offline: "연결 끊김" } as const;

const FEATURE_NAMES: Record<string, string> = {
  menu: "메뉴",
  business_hours: "영업시간",
  coupon: "쿠폰",
  search_keywords: "대표키워드",
  talktalk: "톡톡 답장",
  order: "주문 상태",
  reservation: "예약 상태",
};
export const featureName = (feature: string) => FEATURE_NAMES[feature] ?? "가게 정보";

// ── 리뷰 탭 ────────────────────────────
export const ratingText = (avg: number | null) => (avg === null ? "-" : avg.toFixed(1));
export const unansweredText = (n: number) => (n ? `답글 안 단 리뷰 ${n}개` : "모든 리뷰에 답글을 달았어요");
export const REPLY_TEXT = { done: "답글 달림", todo: "답글 없음" } as const;
/** [답글 초안 받기]가 비서에게 하는 말. 비서는 날짜·별점보다 리뷰 문장으로 리뷰를 더 잘 찾는다. */
export function replyAsk(r: ReviewRow) {
  const quote = r.text.length > 30 ? `${r.text.slice(0, 30)}…` : r.text;
  return `"${quote}" 리뷰에 답글 초안 써 줘`;
}
export const reviewDate = (iso: string) => monthDay(iso);

// ── 리뷰 답글 확인 카드 ────────────────
export const REVIEW_CARD_TEXT = {
  title: "리뷰 답글 올리기",
  source: "원래 리뷰",
  reply: "올릴 답글",
  hint: "고치고 싶으면 대화로 말해 주세요.",
  approve: "답글 올리기",
  cancel: "취소",
} as const;
/** 답글을 올린 뒤 결과 칩·'오늘 한 일'에 남는 한 줄. */
export const REVIEW_POSTED_TEXT = "리뷰 답글 올림";

// ── 답글 대기함 (리뷰 탭) ──────────────
export const INBOX_TEXT = {
  filter: "리뷰 보기",
  filterWaiting: "기다림",
  filterAll: "전체",
  mustRead: "꼭 읽어 보세요",
  draft: "답글 초안",
  posted: "올린 답글",
  post: "올리기",
  skip: "건너뛰기",
  editBox: "답글 고치기",
  redraft: "다시 쓰기",
  writing: "초안 쓰는 중…",
  getDraft: "답글 초안 받기",
  empty: "답글 기다리는 리뷰가 없어요.",
  simulate: "새 리뷰 도착 (데모)",
} as const;
/** 리뷰 탭의 정기 보고 줄(#98). 켜고 끄기는 대화에서 미리보기·승인으로만 한다. */
export const REPORT_TEXT = {
  section: "리뷰 정기 보고",
  off: "정기 보고 꺼짐",
  howTo: "대화에서 \"매일 리뷰 요약해서 보내 줘\"라고 하면 켤 수 있어요.",
  runNow: "지금 받아 보기",
  running: "보고 만드는 중…",
} as const;
export const waitingText = (n: number) => `답글 기다리는 리뷰 ${n}개`;
/** 왼쪽 자주 쓰는 일 배지를 읽어 줄 말. */
export const quickBadgeText = (key: string, n: number) => (key === "reviews" ? waitingText(n) : `${n}개`);
export const preparingText = (n: number) => `초안 쓰는 중 ${n}개`;
export const issuesText = (issues: string[]) => `확인이 필요해요: ${issues.join(", ")}`;
/** 카드 머리의 상태 칩. 답글이 달린 리뷰는 상태와 상관없이 '답글 달림'. */
export function draftChip(r: ReviewRow): { text: string; tone: Tone } {
  if (r.has_owner_reply) return { text: REPLY_TEXT.done, tone: "same" };
  switch (r.draft?.status) {
    case "ready":
      return { text: "초안 준비됨", tone: "new" };
    case "needs_check":
      return { text: "확인 필요", tone: "need" };
    case "skipped":
      return { text: "건너뜀", tone: "same" };
    case "failed":
      return { text: "초안 못 씀", tone: "need" };
    default:
      return { text: REPLY_TEXT.todo, tone: "need" };
  }
}
export const REDRAFT_HINTS: Array<[RedraftHint, string]> = [
  ["shorter", "더 짧게"],
  ["more_apologetic", "더 정중하게"],
  ["warmer", "더 따뜻하게"],
];
/** 카드 버튼을 누른 뒤 그 카드에만 보이는 '하는 중'. 대화창에는 아무것도 남기지 않는다. */
export const INBOX_WORKING_TEXT: Record<ReplyOpName, string> = {
  draft: INBOX_TEXT.writing,
  post: "올리는 중…",
  skip: "건너뛰는 중…",
  redraft: "다시 쓰는 중…",
};
/** 카드 안에 보일 실패 이유. 검사에 걸렸으면 걸린 항목, 아니면 서버가 준 문구. */
export const replyErrorText = (e: ReplyError) => (e.reason === "checks" && e.issues.length ? issuesText(e.issues) : e.message);
/** 서버에 닿지 못했을 때(연결 끊김·서버 오류) 카드와 알림에 쓸 이유. */
export const REPLY_SERVER_ERROR: ReplyError = { reason: "server", message: "서버 오류로 처리하지 못했어요", issues: [] };
/** 채팅에 올리는 짧은 알림(서버 대화 기록에는 넣지 않는다). */
export function replyNoticeText(r: Pick<ReviewRow, "visit_date" | "rating">, e: ReplyError) {
  const why = e.reason === "checks" && e.issues.length ? e.issues.join(", ") : e.message;
  const which = r.rating === null ? "" : ` 별점 ${r.rating}점`;
  return `${reviewDate(r.visit_date)}${which} 리뷰 답글을 올리지 못했어요: ${why}`;
}
export const REPLY_NOTICE_VIEW = "리뷰 탭에서 보기";

// ── 손님 통계 탭 ───────────────────────
export const PERIOD_TEXT: Record<Period, string> = { week: "주간", day: "일간" };
export const METRIC_TEXT: Record<MetricKey, string> = {
  place_views: "플레이스 조회",
  booking_order_requests: "예약·주문 신청",
  smartcall_calls: "전화 문의",
};

/** 지난 기간 대비 한 줄: "지난주보다 60 늘었어요". */
export function changeText(change: number | null, period: Period) {
  if (change === null) return "비교할 기록이 없어요";
  const before = period === "week" ? "지난주보다" : "어제보다";
  if (change === 0) return `${before} 그대로예요`;
  return `${before} ${num(Math.abs(change))} ${change > 0 ? "늘었어요" : "줄었어요"}`;
}

export const hourText = (h: number) => (h === 0 ? "밤 12시" : h < 12 ? `오전 ${h}시` : h === 12 ? "낮 12시" : `오후 ${h - 12}시`);
export const peakText = (h: number | null) => (h === null ? "붐비는 시간 정보 없음" : `가장 붐비는 시간 ${hourText(h)}`);
export const quietText = (h: number | null) => (h === null ? "" : `가장 한산한 시간 ${hourText(h)}`);
export const percentText = (n: number) => `${n.toFixed(1)}%`;
export const NEW_KEYWORD_TEXT = "새로 뜸";
export const shortDay = (key: string) => (DAYS.find(([k]) => k === key)?.[1] ?? key).slice(0, 1);
export { num as countText };

// ── 사진 초안 ──────────────────────────
export type Tone = "new" | "change" | "same" | "need";

/** 초안 한 줄의 상태 문구. 이름·가격이 비면 그것부터 묻는다(사진만 붙일 때는 가격을 묻지 않는다). */
export function draftStatus(d: DraftRow): { text: string; tone: Tone } {
  if (!d.name_ko.trim() && d.change !== "photo_attach") return { text: "이름을 알려 주세요", tone: "need" };
  if (d.price === null && d.change !== "photo_attach") return { text: "가격을 알려 주세요", tone: "need" };
  switch (d.change) {
    case "price_change":
      return { text: `가격 ${num(d.previous_price ?? 0)} → ${num(d.price ?? 0)}`, tone: "change" };
    case "unchanged":
      return { text: "그대로", tone: "same" };
    case "photo_attach":
      return { text: d.menu_name ? `${d.menu_name}에 사진만 바꿈` : "사진만 바꿈", tone: "change" };
    default:
      return { text: "새 메뉴", tone: "new" };
  }
}

const NOTE_BADGES: Record<string, string> = {
  food_guess: "AI 추측",
  cutout_failed: "배경 지우기 실패",
  check_spelling: "철자 확인",
  generated: "AI가 쓴 설명",
};
export const noteBadges = (notes: string[]) =>
  notes.map((n) => NOTE_BADGES[n]).filter((b): b is string => Boolean(b));

/**
 * 승인 카드가 할 수 있는 일.
 * - blocked: 이름·가격을 아직 모르는 초안이 있거나 미리보기가 실패했다
 * - answer: [저장]이 대신 말할 문장. 기존 메뉴에 사진만 붙일 때는 "○○ 사진 바꿔"여야 저장된다
 * - need: 막혔을 때 무엇을 알려 줘야 하는지
 */
export function approval(p: Pending): { blocked: boolean; answer: string; need: string | null } {
  if (p.kind !== "photo") return { blocked: Boolean(p.error), answer: "네", need: null };
  const drafts = p.drafts ?? [];
  const missing = drafts.flatMap((d, i) => {
    const s = draftStatus(d);
    return s.tone === "need" ? [`${i + 1}번 ${s.text.startsWith("이름") ? "이름" : "가격"}`] : [];
  });
  const [only] = drafts;
  const attach = drafts.length === 1 && only.change === "photo_attach" && only.menu_name;
  return {
    blocked: missing.length > 0,
    answer: attach ? `${only.menu_name} 사진 바꿔` : "네",
    need: missing.length ? `저장하려면 대화로 알려 주세요: ${missing.join(", ")}` : null,
  };
}

// ── 바뀐 것 문장 ───────────────────────
/** 저장 전후 리뷰 정기 보고 설정(#98)이 바뀌었으면 그 한 줄. 가게 정보가 아니라 describeChanges가 못 본다(#112). */
export function describeReportChange(before: ReviewReport | null, after: ReviewReport | undefined): string[] {
  if (!before || !after) return [];
  if (before.enabled === after.enabled && before.schedule === after.schedule) return [];
  return after.enabled && after.schedule ? [`리뷰 정기 보고: ${after.schedule}`] : ["리뷰 정기 보고 끔"];
}

/** 두 가게 화면 사이에 바뀐 것을 사장님 문장으로. 승인 카드와 '오늘 한 일'이 같이 쓴다. */
export function describeChanges(before: Place, after: Place): string[] {
  const lines: string[] = [];
  const oldMenus = new Map(before.menu.map((m) => [m.id, m]));
  const newIds = new Set(after.menu.map((m) => m.id));
  for (const m of after.menu) {
    const old = oldMenus.get(m.id);
    if (!old) {
      lines.push(`새 메뉴 ${m.name}${m.price !== null ? ` ${won(m.price)}` : ""}`);
      continue;
    }
    if (old.price !== m.price && m.price !== null) lines.push(`${m.name} 가격 ${num(old.price ?? 0)} → ${num(m.price)}`);
    if (old.name !== m.name) lines.push(`메뉴 이름 ${old.name} → ${m.name}`);
    if ((old.description ?? "") !== (m.description ?? "")) lines.push(`${m.name} 설명 바꿈`);
    if (old.photo_url !== m.photo_url && m.photo_url) lines.push(`${m.name} 사진 바꿈`);
  }
  for (const m of before.menu) if (!newIds.has(m.id)) lines.push(`메뉴 내림 ${m.name}`);

  for (const [key, label] of DAYS) {
    const a = before.business_hours[key];
    const b = after.business_hours[key];
    if (JSON.stringify(a) !== JSON.stringify(b)) lines.push(`${label} ${hoursText(b)}`);
  }

  const oldCoupons = new Set(before.coupon.map((c) => c.id));
  for (const c of after.coupon) if (!oldCoupons.has(c.id)) lines.push(couponLine(c));

  const oldWords = before.keywords ?? [];
  const newWords = after.keywords ?? oldWords; // 키워드가 없는 응답은 바뀐 것이 없다.
  for (const k of newWords) if (!oldWords.includes(k)) lines.push(`대표키워드 추가 ${k}`);
  for (const k of oldWords) if (!newWords.includes(k)) lines.push(`대표키워드 뺌 ${k}`);
  lines.push(...recordChanges(before, after));
  return lines;
}

// ── 기록(톡톡·주문·예약) 바뀐 것 ─────────
/** 기록 섹션이 한 줄 머리에 붙이는 이름. 없는 섹션은 섹션 id 그대로. */
const RECORD_NAMES: Record<string, string> = { talktalk: "톡톡 문의", order: "주문", reservation: "예약" };
/** 필드 이름. 없는 필드는 필드 id 그대로. */
const FIELD_NAMES: Record<string, string> = {
  message: "문의",
  reply_text: "답장",
  status: "상태",
  items: "주문",
  type: "받는 방식",
  date: "날짜",
  time: "시간",
  people: "인원",
  reason: "사유",
};
/** 서버 코드 값(상태·방식). 없는 값은 그대로. */
const VALUE_TEXT: Record<string, string> = {
  unanswered: "답장 전",
  saved_not_sent: "답장 저장(아직 안 보냄)",
  new: "새 주문",
  accepted: "접수",
  rejected: "거절",
  ready: "준비 완료",
  completed: "전달 완료",
  requested: "예약 신청",
  confirmed: "확정",
  cancelled: "취소",
  no_show: "노쇼",
  pickup: "포장",
  delivery: "배달",
};
const KNOWN_SECTIONS = new Set(["menu", "business_hours", "coupon", "keywords"]);

const isRecordRows = (value: unknown): value is RecordRow[] =>
  Array.isArray(value) && value.every((r) => r && typeof r === "object" && "id" in r && "label" in r && "fields" in r);

function valueText(field: string, value: unknown): string {
  if (value === null || value === undefined || value === "") return "";
  if (field === "people" && typeof value === "number") return `${value}명`;
  const text = typeof value === "string" ? value : JSON.stringify(value);
  return VALUE_TEXT[text] ?? text;
}

/** 기록 한 개를 구별하는 머리줄: "톡톡 문의 · 주차 가능한가요?". 바뀌는 필드는 머리에서 뺀다. */
function recordHead(section: string, row: RecordRow, changed: Set<string>) {
  const parts = Object.entries(row.label)
    .filter(([k]) => !changed.has(k))
    .map(([k, v]) => valueText(k, v))
    .filter(Boolean);
  return [RECORD_NAMES[section] ?? section, ...parts].join(" · ");
}

/** 기록 섹션마다 before/after 값이 다른 필드를 "답장: 근처 … " / "상태: 답장 전 → 확정"으로. */
function recordChanges(before: Place, after: Place): string[] {
  const lines: string[] = [];
  const sections = after as unknown as Record<string, unknown>;
  const olds = before as unknown as Record<string, unknown>;
  for (const [section, rows] of Object.entries(sections)) {
    if (KNOWN_SECTIONS.has(section) || !isRecordRows(rows)) continue;
    const previous = isRecordRows(olds[section]) ? (olds[section] as RecordRow[]) : null;
    if (!previous) continue; // 예전 응답과 비교할 기록이 없으면 바뀐 것을 알 수 없다.
    const oldRows = new Map(previous.map((r) => [r.id, r]));
    for (const row of rows) {
      const old = oldRows.get(row.id);
      const now = { ...row.label, ...row.fields };
      const was: Record<string, unknown> = old ? { ...old.label, ...old.fields } : {};
      const changed = Object.keys(now).filter((k) => JSON.stringify(now[k]) !== JSON.stringify(was[k]));
      if (!changed.length) continue;
      lines.push(old ? recordHead(section, row, new Set(changed)) : `${recordHead(section, row, new Set())} 새로 생김`);
      if (!old) continue;
      for (const k of changed) {
        const name = FIELD_NAMES[k] ?? k;
        const a = valueText(k, was[k]);
        const b = valueText(k, now[k]);
        lines.push(a ? `${name}: ${a} → ${b || "비움"}` : `${name}: ${b}`);
      }
    }
    const newIds = new Set(rows.map((r) => r.id));
    for (const old of previous) if (!newIds.has(old.id)) lines.push(`${recordHead(section, old, new Set())} 지움`);
  }
  return lines;
}

const squash = (text: string) => text.replace(/\s+/g, "");
/** 새 쿠폰 한 줄. 쿠폰 이름에 이미 할인 문구가 들어 있으면 다시 붙이지 않는다. */
function couponLine(c: Coupon) {
  const discount = discountText(c);
  return squash(c.title).includes(squash(discount)) ? `새 쿠폰 ${c.title}` : `새 쿠폰 ${c.title} ${discount}`;
}

const hasHangul = (text: string) => /[가-힣]/.test(text);

/** 승인 카드에 보일 변경 줄. 미리보기가 실패하면 그 이유 한 줄(한국어가 아니면 쉬운 말로). */
export function pendingLines(place: Place, p: Pending): string[] {
  if (p.error) return [hasHangul(p.error) ? p.error : "이 변경은 저장할 수 없어요. 비서에게 다시 말해 주세요."];
  if (!p.after) return [];
  const lines = describeChanges(place, p.after);
  return lines.length ? lines : [`${featureName(p.feature)} 정보를 바꿔요`];
}
