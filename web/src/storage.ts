// 이 브라우저에만 남기는 최근 대화와 대화 기록. 저장이 막혀도 화면은 그대로 돈다.
import type { LogEntry, Message } from "./types";

export type Recent = { id: string; title: string; at: number };
export type SavedChat = { messages: Message[]; log: LogEntry[] };

const RECENT = "agenthan.recent.v1";
const CHAT = (id: string) => `agenthan.chat.v1.${id}`;
const MAX_RECENT = 12;

function read<T>(key: string, fallback: T): T {
  try {
    const raw = localStorage.getItem(key);
    return raw ? (JSON.parse(raw) as T) : fallback;
  } catch {
    return fallback;
  }
}

function write(key: string, value: unknown) {
  try {
    localStorage.setItem(key, JSON.stringify(value));
  } catch {
    // 저장소가 막힌 브라우저: 기록만 못 남긴다.
  }
}

export const loadRecent = () => read<Recent[]>(RECENT, []);

export function rememberRecent(entry: Recent) {
  const rest = loadRecent().filter((r) => r.id !== entry.id);
  write(RECENT, [entry, ...rest].slice(0, MAX_RECENT));
}

export function forget(id: string) {
  write(RECENT, loadRecent().filter((r) => r.id !== id));
  try {
    localStorage.removeItem(CHAT(id));
  } catch {
    // 무시
  }
}

export const loadChat = (id: string) => read<SavedChat>(CHAT(id), { messages: [], log: [] });

export function saveChat(id: string, saved: SavedChat) {
  // 보낸 사진 주소는 이 탭에서만 살아 있어 남기지 않는다.
  const messages = saved.messages.map((m) =>
    m.role === "owner" && m.image ? { ...m, image: undefined, hadImage: true } : m,
  );
  write(CHAT(id), { ...saved, messages });
}
