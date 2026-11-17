// 대화 상태 한곳: 메시지·가게 화면·승인 대기·오늘 한 일·연결 상태. 사이드카와 승인 카드가 같은 상태를 본다.
import { useCallback, useEffect, useRef, useState } from "react";
import { ApiError, type AssistantApi } from "./api";
import { couponActionLabel, describeChanges, pendingLines, REVIEW_POSTED_TEXT, type CouponInput } from "./labels";
import { forgetPhotoUrl, photoUrl } from "./photo";
import * as storage from "./storage";
import type { Action, LogEntry, Message, Pending, Place, ReviewRow, Turn, View } from "./types";

const COUPON_ACTION = "coupon.create";

/** 오늘부터 14일 동안. 날짜는 사장님 기기의 날짜(YYYY-MM-DD). */
const isoDay = (d: Date) =>
  `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}-${String(d.getDate()).padStart(2, "0")}`;
function blankCoupon(): CouponInput {
  const today = new Date();
  const end = new Date(today);
  end.setDate(end.getDate() + 14);
  return { title: "", discount_type: "percent", discount_value: "", start_date: isoDay(today), end_date: isoDay(end) };
}

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
  /** 대화 영역에 떠 있는 쿠폰 입력 블록(한 번에 하나). 없으면 null. */
  couponForm: CouponInput | null;
  /** 리뷰 답글 카드가 가리키는 원래 리뷰. 못 찾으면 undefined. */
  reviewOf: (reviewId: string | undefined) => ReviewRow | undefined;
  openCoupon: (initial?: CouponInput) => void;
  closeCoupon: () => void;
  submitCoupon: (values: CouponInput) => Promise<void>;
  send: (text: string, image?: File) => Promise<void>;
  act: (action: Action) => Promise<void>;
  retry: () => void;
  newChat: () => Promise<void>;
  open: (id: string) => Promise<void>;
  addLog: (lines: string[]) => void;
  notify: (text: string, reviewId: string) => void;
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
  const [couponForm, setCouponForm] = useState<CouponInput | null>(null);
  const [reviewRows, setReviewRows] = useState<ReviewRow[]>([]);
  const toRetry = useRef<Retryable | null>(null);
  const sentCoupon = useRef<CouponInput | null>(null); // 거절되면 블록을 다시 열 값
  const inFlight = useRef(false); // 렌더 전에 들어온 두 번째 클릭도 막는다
  const latest = useRef({ place, pending, messages });
  latest.current = { place, pending, messages };

  useEffect(() => {
    if (sessionId) storage.saveChat(sessionId, { messages, log });
  }, [sessionId, messages, log]);

  // 리뷰 답글 카드가 뜨면 그 리뷰를 찾으려고 리뷰 목록을 한 번 읽는다. 실패하면 원래 리뷰 칸만 빠진다.
  const missingReview = pending.find((p) => p.kind === "review" && p.review_id && !reviewRows.some((r) => r.id === p.review_id))?.review_id;
  useEffect(() => {
    if (!sessionId || !missingReview) return;
    let live = true;
    api
      .reviews(sessionId)
      .then((v) => live && setReviewRows(v.reviews))
      .catch(() => undefined);
    return () => {
      live = false;
    };
  }, [api, sessionId, missingReview]);
  const reviewOf = useCallback((id: string | undefined) => (id ? reviewRows.find((r) => r.id === id) : undefined), [reviewRows]);

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
    setCouponForm(null);
    setReviewRows([]);
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

  /**
   * 비서 답을 받아 대화·가게 화면에 반영한다. 승인 카드가 끝났을 때만 결과와 '오늘 한 일'을 남긴다.
   * answered: 카드 버튼(task.approve/task.cancel)으로 끝낸 것이면 그 액션. 리뷰 답글은 가게 화면이
   * 바뀌지 않으므로 이것으로 올렸는지 안다.
   */
  const applyTurn = useCallback((turn: Turn, answered?: Action) => {
    const before = latest.current;
    const resolved = before.pending.length > 0 && turn.pending.length === 0;
    const lines = resolved && before.place ? describeChanges(before.place, turn.place) : [];
    const answeredReview =
      resolved && answered && before.pending.some((p) => p.kind === "review" && p.task_id === answered.args.task_id);
    if (answeredReview && answered.action_id === "task.approve") lines.push(REVIEW_POSTED_TEXT);
    const card = turn.pending.find((p) => p.kind === "review" && !before.pending.some((b) => b.task_id === p.task_id));
    // 이 답이 새로 띄운 변경 카드의 줄. 말풍선의 승인 질문 줄 밑에 같은 내용을 보인다.
    const previews = turn.pending
      .filter((p) => !p.kind && !before.pending.some((b) => b.task_id === p.task_id))
      .map((p) => pendingLines(turn.place, p));
    const added: Message[] = [
      {
        id: newId(),
        role: "agent",
        text: turn.reply,
        nextActions: turn.next_actions,
        ...(card && { cardFor: card.task_id }),
        ...(previews.length && { previews }),
      },
    ];
    if (resolved && (answeredReview || before.pending.some((p) => p.kind !== "review")))
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
    if (!turn) return;
    applyTurn(turn, action);
    // 쿠폰 값이 거절되면(승인 카드 없이 "값을 다시 확인해 주세요") 넣었던 값 그대로 블록을 다시 연다.
    const sent = sentCoupon.current;
    if (action.action_id === COUPON_ACTION && sent && action.label === couponActionLabel(sent) && !turn.pending.some((p) => p.feature === "coupon")) setCouponForm(sent);
  }, [api, applyTurn, attempt, sessionId]);

  /** 대화 턴 밖에서 한 일(예: 대기함 [올리기])을 '오늘 한 일'에만 남긴다. */
  const addLog = useCallback((lines: string[]) => setLog((l) => [...l, { at: Date.now(), lines }]), []);
  /** 화면에서만 만든 짧은 알림을 대화창에 올린다. 서버 대화 기록에는 넣지 않는다. */
  const notify = useCallback((text: string, reviewId: string) => {
    setMessages((m) => [...m, { id: newId(), role: "notice", text, reviewId }]);
  }, []);

  const openCoupon = useCallback((initial?: CouponInput) => setCouponForm(initial ?? blankCoupon()), []);
  const closeCoupon = useCallback(() => setCouponForm(null), []);

  const act = useCallback(async (action: Action) => {
    // 값 없이 온 쿠폰 만들기(왼쪽 버튼·다음 액션 칩)는 서버에 묻지 않고 입력 블록을 연다.
    if (action.action_id === COUPON_ACTION && Object.keys(action.args).length === 0) {
      if (!inFlight.current) setCouponForm((open) => open ?? blankCoupon());
      return;
    }
    if (!sessionId || inFlight.current) return;
    if (!latest.current.messages.some((m) => m.role === "owner")) {
      storage.rememberRecent({ id: sessionId, title: action.label, at: Date.now() });
      setRecent(storage.loadRecent());
    }
    setMessages((m) => [...m, { id: newId(), role: "owner", text: action.label }]);
    await performAction(action);
  }, [performAction, sessionId]);

  /** 블록의 [미리 보기]: 블록을 닫고 값을 coupon.create 액션으로 보낸다. 서버가 미리보기와 승인 카드를 돌려준다. */
  const submitCoupon = useCallback(async (values: CouponInput) => {
    if (!sessionId || inFlight.current) return;
    sentCoupon.current = values;
    setCouponForm(null);
    await act({
      action_id: COUPON_ACTION,
      label: couponActionLabel(values),
      args: {
        title: values.title.trim(),
        discount_type: values.discount_type,
        discount_value: Number(values.discount_value),
        start_date: values.start_date,
        end_date: values.end_date,
      },
    });
  }, [act, sessionId]);

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

  return {
    sessionId, messages, place, pending, log, recent, busy, online, error,
    couponForm, reviewOf, openCoupon, closeCoupon, submitCoupon,
    send, act, retry, newChat, open, addLog, notify,
  };
}
