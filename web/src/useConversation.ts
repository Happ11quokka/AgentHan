// 대화 상태 한곳: 메시지·가게 화면·승인 대기·오늘 한 일·연결 상태. 사이드카와 승인 카드가 같은 상태를 본다.
import { useCallback, useEffect, useRef, useState } from "react";
import { ApiError, type AssistantApi } from "./api";
import { describeChanges } from "./labels";
import { forgetPhotoUrl, photoUrl } from "./photo";
import * as storage from "./storage";
import type { Action, LogEntry, Message, Pending, Place, Turn, View } from "./types";

/** 실패했을 때 [다시 시도]가 되풀이할 일. */
type Retryable = { kind: "action"; action: Action } | { kind: "send"; text: string; image?: File } | { kind: "open"; id: string } | { kind: "start" };

export type Conversation = {
  sessionId: string | null;
  messages: Message[];
  place: Place | null;
  pending: Pending[];
  log: LogEntry[];
  recent: storage.Recent[];
  busy: boolean;
  online: boolean;
  error: string | null;
  send: (text: string, image?: File) => Promise<void>;
  act: (action: Action) => Promise<void>;
  retry: () => void;
  newChat: () => Promise<void>;
  open: (id: string) => Promise<void>;
};

let nextId = 1;
const newId = () => nextId++;

const isOffline = (e: unknown) => e instanceof ApiError && e.status === 0;
const friendly = (e: unknown) =>
  isOffline(e) ? "서버에 연결하지 못했어요. 잠시 뒤 다시 시도해 주세요." : "비서가 답하지 못했어요. 다시 시도해 주세요.";

export function useConversation(api: AssistantApi): Conversation {
  const [sessionId, setSessionId] = useState<string | null>(null);
  const [messages, setMessages] = useState<Message[]>([]);
  const [place, setPlace] = useState<Place | null>(null);
  const [pending, setPending] = useState<Pending[]>([]);
  const [log, setLog] = useState<LogEntry[]>([]);
  const [recent, setRecent] = useState<storage.Recent[]>(storage.loadRecent);
  const [busy, setBusy] = useState(false);
  const [online, setOnline] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const toRetry = useRef<Retryable | null>(null);
  const inFlight = useRef(false); // 렌더 전에 들어온 두 번째 클릭도 막는다
  const latest = useRef({ place, pending, messages });
  latest.current = { place, pending, messages };

  useEffect(() => {
    if (sessionId) storage.saveChat(sessionId, { messages, log });
  }, [sessionId, messages, log]);

  /** 서버를 한 번 부르는 공통 틀: 바쁨·오류·연결 상태·다시 시도할 일을 맡는다. */
  const attempt = useCallback(async <T,>(job: Retryable, call: () => Promise<T>): Promise<T | undefined> => {
    if (inFlight.current) return undefined;
    inFlight.current = true;
    setBusy(true);
    setError(null);
    try {
      const result = await call();
      toRetry.current = null;
      setOnline(true);
      return result;
    } catch (e) {
      if (e instanceof ApiError && e.status === 404 && job.kind === "open") throw e;
      toRetry.current = job;
      setOnline(!isOffline(e));
      setError(friendly(e));
      return undefined;
    } finally {
      inFlight.current = false;
      setBusy(false);
    }
  }, []);

  const show = useCallback((id: string, view: View, saved: storage.SavedChat) => {
    for (const m of latest.current.messages) if (m.role === "owner") forgetPhotoUrl(m.image);
    setSessionId(id);
    setMessages(saved.messages);
    setLog(saved.log);
    setPlace(view.place);
    setPending(view.pending);
  }, []);

  const newChat = useCallback(async () => {
    const started = await attempt({ kind: "start" }, () => api.start());
    if (started) show(started.session_id, started, { messages: [], log: [] });
  }, [api, attempt, show]);

  const open = useCallback(
    async (id: string) => {
      try {
        const view = await attempt({ kind: "open", id }, () => api.view(id));
        if (view) show(id, view, storage.loadChat(id));
      } catch {
        // 서버에서 사라진 대화(404): 목록에서 빼고 새로 시작한다.
        storage.forget(id);
        setRecent(storage.loadRecent());
        await newChat();
      }
    },
    [api, attempt, newChat, show],
  );

  /** 비서 답을 받아 대화·가게 화면에 반영한다. 승인 카드가 끝났을 때만 결과와 '오늘 한 일'을 남긴다. */
  const applyTurn = useCallback((turn: Turn) => {
    const before = latest.current;
    const resolved = before.pending.length > 0 && turn.pending.length === 0;
    const lines = resolved && before.place ? describeChanges(before.place, turn.place) : [];
    const added: Message[] = [{ id: newId(), role: "agent", text: turn.reply, nextActions: turn.next_actions }];
    if (resolved && before.pending.some((p) => p.kind !== "review"))
      added.push({ id: newId(), role: "result", saved: lines.length > 0, lines });
    if (lines.length) setLog((l) => [...l, { at: Date.now(), lines }]);
    setMessages((m) => [...m, ...added]);
    setPlace(turn.place);
    setPending(turn.pending);
  }, []);

  const perform = useCallback(
    async (text: string, image?: File) => {
      if (!sessionId) return;
      const turn = await attempt({ kind: "send", text, image }, () =>
        image ? api.sendPhoto(sessionId, image, text) : api.say(sessionId, text),
      );
      if (turn) applyTurn(turn);
    },
    [api, applyTurn, attempt, sessionId],
  );

  const send = useCallback(
    async (text: string, image?: File) => {
      if (!sessionId || inFlight.current) return;
      const message: Message = { id: newId(), role: "owner", text, image: image && photoUrl(image), hadImage: Boolean(image) };
      if (!latest.current.messages.some((m) => m.role === "owner")) {
        storage.rememberRecent({ id: sessionId, title: text.trim() || "사진으로 메뉴 올리기", at: Date.now() });
        setRecent(storage.loadRecent());
      }
      setMessages((m) => [...m, message]);
      await perform(text, image);
    },
    [perform, sessionId],
  );

  const performAction = useCallback(async (action: Action) => {
    if (!sessionId) return;
    const turn = await attempt({ kind: "action", action }, () => api.act(sessionId, action));
    if (turn) applyTurn(turn);
  }, [api, applyTurn, attempt, sessionId]);

  const act = useCallback(async (action: Action) => {
    if (!sessionId || inFlight.current) return;
    if (!latest.current.messages.some((m) => m.role === "owner")) {
      storage.rememberRecent({ id: sessionId, title: action.label, at: Date.now() });
      setRecent(storage.loadRecent());
    }
    setMessages((m) => [...m, { id: newId(), role: "owner", text: action.label }]);
    await performAction(action);
  }, [performAction, sessionId]);

  const retry = useCallback(() => {
    const job = toRetry.current;
    if (!job || job.kind === "start") void newChat();
    else if (job.kind === "open") void open(job.id);
    else if (job.kind === "action") void performAction(job.action);
    else void perform(job.text, job.image);
  }, [newChat, open, perform, performAction]);

  const started = useRef(false);
  useEffect(() => {
    if (started.current) return;
    started.current = true;
    const last = storage.loadRecent()[0];
    void (last ? open(last.id) : newChat());
  }, [newChat, open]);

  return { sessionId, messages, place, pending, log, recent, busy, online, error, send, act, retry, newChat, open };
}
