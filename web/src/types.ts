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
  feature: string;
  after: Place | null;
  changes: Partial<Record<keyof Place, SectionChanges>>;
  error?: string;
  kind?: "photo";
  drafts?: DraftRow[];
};

export type View = { place: Place; pending: Pending[] };

export type Turn = View & { reply: string };

export type Started = View & { session_id: string };

/** 대화창에 쌓이는 것: 사장님 말, 비서 답, 승인 카드가 끝난 결과. */
export type Message =
  | { id: number; role: "owner"; text: string; image?: string; hadImage?: boolean }
  | { id: number; role: "agent"; text: string }
  | { id: number; role: "result"; saved: boolean; lines: string[] };

/** '오늘 한 일' 한 칸: 승인으로 저장된 변경 문장들. */
export type LogEntry = { at: number; lines: string[] };
