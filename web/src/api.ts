import type { Action, Period, ReportView, ReviewsView, Started, Turn, View } from "./types";

/** 화면이 서버와 주고받는 유일한 길. 테스트에서는 가짜로 바꿔 끼운다. */
export interface AssistantApi {
  start(): Promise<Started>;
  act(sessionId: string, action: Action): Promise<Turn>;
  say(sessionId: string, text: string): Promise<Turn>;
  sendPhoto(sessionId: string, image: File, text: string): Promise<Turn>;
  view(sessionId: string): Promise<View>;
  reviews(sessionId: string): Promise<ReviewsView>;
  report(sessionId: string, period: Period): Promise<ReportView>;
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
  reviews: (id) => call(`${base(id)}/reviews`),
  report: (id, period) => call(`${base(id)}/report?period=${period}`),
};
