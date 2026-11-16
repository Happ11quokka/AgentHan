import { vi } from "vitest";
import { ApiError, type AssistantApi } from "../api";
import type { DraftRow, Pending, Place, Turn, View } from "../types";

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

export const withCake: Place = {
  ...SEED,
  menu: [
    ...SEED.menu,
    { id: "m03", name: "밀푀유 케이크", price: 8500, description: "", photo_url: "/p/m03" },
  ],
};

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
    say: vi.fn(async (_id: string, _text: string) => next()),
    sendPhoto: vi.fn(async (_id: string, _image: File, _text: string) => next()),
    view: vi.fn(async (_id: string) => {
      if (view instanceof Error) throw view;
      return view;
    }),
  } satisfies AssistantApi;
  return api;
}

export const offline = () => new ApiError(0, "서버에 연결하지 못했어요.");
export const gone = () => new ApiError(404, "없는 대화입니다.");
