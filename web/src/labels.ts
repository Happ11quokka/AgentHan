// 서버의 코드·숫자를 사장님 말로 바꾸는 유일한 곳. 화면 다른 곳은 코드나 문구 표를 직접 두지 않는다.
import type { Coupon, DayHours, DraftRow, MetricKey, Pending, Period, Place, ReviewRow } from "./types";

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

export const RESULT_TEXT = { saved: "저장 완료", skipped: "저장 안 함" } as const;
export const CONNECTION_TEXT = { online: "연결됨", offline: "연결 끊김" } as const;

const FEATURE_NAMES: Record<string, string> = { menu: "메뉴", business_hours: "영업시간", coupon: "쿠폰" };
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
  for (const c of after.coupon) if (!oldCoupons.has(c.id)) lines.push(`새 쿠폰 ${c.title} ${discountText(c)}`);
  return lines;
}

const hasHangul = (text: string) => /[가-힣]/.test(text);

/** 승인 카드에 보일 변경 줄. 미리보기가 실패하면 그 이유 한 줄(한국어가 아니면 쉬운 말로). */
export function pendingLines(place: Place, p: Pending): string[] {
  if (p.error) return [hasHangul(p.error) ? p.error : "이 변경은 저장할 수 없어요. 비서에게 다시 말해 주세요."];
  if (!p.after) return [];
  const lines = describeChanges(place, p.after);
  return lines.length ? lines : [`${featureName(p.feature)} 정보를 바꿔요`];
}
