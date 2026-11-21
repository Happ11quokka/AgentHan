// 대화 상태 한곳: 메시지·가게 화면·승인 대기·오늘 한 일·연결 상태. 사이드카와 승인 카드가 같은 상태를 본다.
import { useCallback, useEffect, useRef, useState } from "react";
import { ApiError, type AssistantApi } from "./api";
import { businessActionLabel, businessInitial, couponActionLabel, describeChanges, describeReportChange, REVIEW_POSTED_TEXT, type BusinessInput, type CouponInput } from "./labels";
import { forgetPhotoUrl, photoUrl } from "./photo";
import * as storage from "./storage";
import type { Action, LogEntry, MenuEdit, Message, OnboardingInput, Pending, Place, ReviewReport, ReviewRow, Routines, RoutinesChange, ServerMessage, ShopInfo, Turn, View } from "./types";

const COUPON_ACTION = "coupon.create";
const BUSINESS_ACTION = "business_info.edit";
const SAY_ACTION = "chat.say";
/** 정해진 때의 리뷰 보고(#98)를 받으려고 세션 조회(/place)를 다시 읽는 간격. */
export const REPORT_POLL_MS = 60_000;
/** 메뉴판 사진으로 올린 메뉴의 설명·사진을 채우는 동안 메뉴 탭을 다시 읽는 간격. */
export const FILL_POLL_MS = 1000;

/** 주소에 ?menu=empty가 있으면 메뉴를 비운 가게로 새 대화를 연다(시연: 메뉴판 사진으로 채우기). */
const emptyMenuDemo = () => new URLSearchParams(window.location.search).get("menu") === "empty";

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
  /** 가게 이름·업종. 예전 서버라 모르면 null. */
  shop: ShopInfo | null;
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
  /** 대화 영역에 떠 있는 가게 기본 정보 입력 블록의 처음 값(지금 가게 값). 없으면 null. */
  businessForm: BusinessInput | null;
  closeBusiness: () => void;
  /** 블록의 [미리 보기]: 고친 칸만 business_info.edit 액션으로 보낸다. */
  submitBusiness: (changes: Partial<BusinessInput>) => Promise<void>;
  /** 블록의 [추천 글 받기]: 소개글 초안. 실패하면 던진다. */
  draftIntroduction: () => Promise<string>;
  send: (text: string, image?: File) => Promise<void>;
  act: (action: Action) => Promise<void>;
  retry: () => void;
  newChat: () => Promise<void>;
  open: (id: string) => Promise<void>;
  addLog: (lines: string[]) => void;
  notify: (text: string, reviewId: string) => void;
  /** 온보딩 값을 저장한다. 성공하면 null, 실패하면 사장님께 보일 문장. */
  onboard: (values: OnboardingInput) => Promise<string | null>;
  /** 리뷰 정기 보고 설정 한 줄(#98). 예전 서버라 모르면 null. */
  reviewReport: ReviewReport | null;
  /** 리뷰 탭 [지금 받아 보기]: 보고 한 회분을 대화에 넣는다. 성공하면 null, 실패하면 사장님께 보일 문장. */
  runReport: () => Promise<string | null>;
  /** 루틴 설정(#33): 정기 보고 일정과 답글 초안 미리 쓰기. 예전 서버라 모르면 null. */
  routines: Routines | null;
  /** 루틴 설정 스위치·일정을 바꾼다. 대화 턴이 아니다. 성공하면 null, 실패하면 사장님께 보일 문장. */
  changeRoutines: (change: RoutinesChange) => Promise<string | null>;
  /** 사진을 읽는 중이면 보낸 시각(ms), 아니면 null. 메뉴 탭의 '메뉴판 읽는 중' 진행 막대. */
  readingSince: number | null;
  /** 메뉴 탭에서 직접 고친 값을 저장한다(승인 카드 없음). 성공하면 null, 실패하면 사장님께 보일 문장. */
  editMenu: (menuId: string, edit: MenuEdit) => Promise<string | null>;
  /** [사진 다듬기]: 사진 다듬기를 부탁한다. 사장님께 보일 한 줄(시작함·준비 중·실패)을 돌려준다. */
  polishMenu: (menuId: string) => Promise<string>;
  /** 메뉴 탭에서 메뉴 한 줄을 지운다. 성공하면 null, 실패하면 사장님께 보일 문장. */
  deleteMenu: (menuId: string) => Promise<string | null>;
  /** 세션 조회를 바로 다시 읽는다(리뷰 도착 직후 비서 알림 받기). 실패는 조용히 넘긴다. */
  refresh: () => Promise<void>;
};

let nextId = 1;
const newId = () => nextId++;

const ONBOARD_FAILED = "저장하지 못했어요. 다시 시도해 주세요.";
const REPORT_FAILED = "리뷰 보고를 받지 못했어요. 다시 시도해 주세요.";
const ROUTINES_FAILED = "설정을 바꾸지 못했어요. 다시 시도해 주세요.";
const MENU_FAILED = "메뉴를 저장하지 못했어요. 다시 시도해 주세요.";

/** 서버가 먼저 보낸 메시지 → 대화창의 비서 말풍선(칩은 그대로 누를 수 있다). */
const fromServer = (m: ServerMessage): Message => ({ id: newId(), role: "agent", text: m.text, nextActions: m.next_actions });

const isOffline = (e: unknown) => e instanceof ApiError && e.status === 0;
const friendly = (e: unknown) =>
  isOffline(e) ? "서버에 연결하지 못했어요. 잠시 뒤 다시 시도해 주세요." : "비서가 답하지 못했어요. 다시 시도해 주세요.";

export function useConversation(api: AssistantApi, reportPollMs: number = REPORT_POLL_MS): Conversation {
  const [sessionId, setSessionId] = useState<string | null>(null);
  const [messages, setMessages] = useState<Message[]>([]);
  const [place, setPlace] = useState<Place | null>(null);
  const [shop, setShop] = useState<ShopInfo | null>(null);
  const [pending, setPending] = useState<Pending[]>([]);
  const [log, setLog] = useState<LogEntry[]>([]);
  const [recent, setRecent] = useState<storage.Recent[]>(storage.loadRecent);
  const [busy, setBusy] = useState(false);
  const [online, setOnline] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [couponForm, setCouponForm] = useState<CouponInput | null>(null);
  const [businessForm, setBusinessForm] = useState<BusinessInput | null>(null);
  const [reviewRows, setReviewRows] = useState<ReviewRow[]>([]);
  const [reviewReport, setReviewReport] = useState<ReviewReport | null>(null);
  const [routines, setRoutines] = useState<Routines | null>(null);
  // 사진을 보내고 비서 답을 기다리는 동안(메뉴판 읽는 중) 시작 시각. 메뉴 탭이 진행 막대를 그린다.
  const [readingSince, setReadingSince] = useState<number | null>(null);
  const toRetry = useRef<Retryable | null>(null);
  const sentCoupon = useRef<CouponInput | null>(null); // 거절되면 블록을 다시 열 값
  const sentBusiness = useRef<{ label: string; initial: BusinessInput; changes: Partial<BusinessInput> } | null>(null);
  const inFlight = useRef(false); // 렌더 전에 들어온 두 번째 클릭도 막는다
  const latest = useRef({ place, pending, messages, reviewReport });
  latest.current = { place, pending, messages, reviewReport };

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

  /** 대화 턴 밖에서 받은 조회 결과: 서버가 먼저 보낸 메시지와 리뷰 보고 설정만 반영한다. */
  const absorb = useCallback((view: View) => {
    const added = (view.messages ?? []).map(fromServer);
    if (added.length) setMessages((m) => [...m, ...added]);
    if (view.review_report) setReviewReport(view.review_report);
    if (view.routines) setRoutines(view.routines);
  }, []);

  const show = useCallback((id: string, view: View, saved: storage.SavedChat) => {
    for (const m of latest.current.messages) if (m.role === "owner") forgetPhotoUrl(m.image);
    setSessionId(id);
    setMessages([...saved.messages, ...(view.messages ?? []).map(fromServer)]);
    setReviewReport(view.review_report ?? null);
    setRoutines(view.routines ?? null);
    setLog(saved.log);
    setPlace(view.place);
    setShop(view.shop ?? null);
    setPending(view.pending);
    setCouponForm(null);
    setReviewRows([]);
  }, []);

  const newChat = useCallback(async () => {
    const started = await attempt({ kind: "start" }, () => api.start(emptyMenuDemo() ? { emptyMenu: true } : undefined));
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
    // 보고 설정(#98)은 가게 정보가 아니라 따로 본다(#112).
    if (resolved) lines.push(...describeReportChange(before.reviewReport, turn.review_report));
    const answeredReview =
      resolved && answered && before.pending.some((p) => p.kind === "review" && p.task_id === answered.args.task_id);
    if (answeredReview && answered.action_id === "task.approve") lines.push(REVIEW_POSTED_TEXT);
    const card = turn.pending.find((p) => p.kind === "review" && !before.pending.some((b) => b.task_id === p.task_id));
    // 변경 줄은 승인 카드에만 보인다. 말풍선은 "…아래처럼 바꿀까요?" 질문만(중복 없음).
    const added: Message[] = [
      {
        id: newId(),
        role: "agent",
        text: turn.reply,
        nextActions: turn.next_actions,
        ...(card && { cardFor: card.task_id }),
      },
    ];
    if (resolved && (answeredReview || before.pending.some((p) => p.kind !== "review")))
      added.push({ id: newId(), role: "result", saved: lines.length > 0, lines });
    if (lines.length) setLog((l) => [...l, { at: Date.now(), lines }]);
    added.push(...(turn.messages ?? []).map(fromServer));
    setMessages((m) => [...m, ...added]);
    if (turn.review_report) setReviewReport(turn.review_report);
    if (turn.routines) setRoutines(turn.routines);
    setPlace(turn.place);
    if (turn.shop) setShop(turn.shop);
    setPending(turn.pending);
  }, []);

  const perform = useCallback(
    async (text: string, image?: File) => {
      if (!sessionId) return;
      if (image) setReadingSince(Date.now());
      const turn = await attempt({ kind: "send", text, image }, () =>
        image ? api.sendPhoto(sessionId, image, text) : api.say(sessionId, text),
      );
      if (image) setReadingSince(null);
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
    // 가게 정보도 승인 카드 없이 돌아오면 고친 값 그대로 블록을 다시 연다.
    const biz = sentBusiness.current;
    if (action.action_id === BUSINESS_ACTION && biz && action.label === biz.label && !turn.pending.some((p) => p.feature === "business_info"))
      setBusinessForm({ ...biz.initial, ...biz.changes });
  }, [api, applyTurn, attempt, sessionId]);

  /** 대화 턴 밖에서 한 일(예: 대기함 [올리기])을 '오늘 한 일'에만 남긴다. */
  const addLog = useCallback((lines: string[]) => setLog((l) => [...l, { at: Date.now(), lines }]), []);
  /** 화면에서만 만든 짧은 알림을 대화창에 올린다. 서버 대화 기록에는 넣지 않는다. */
  const notify = useCallback((text: string, reviewId: string) => {
    setMessages((m) => [...m, { id: newId(), role: "notice", text, reviewId }]);
  }, []);

  /** 온보딩 [시작하기]: 대화 턴이 아니라 가게 저장소에 바로 저장한다(사장님이 넣은 값만). */
  const onboard = useCallback(
    async (values: OnboardingInput) => {
      if (!sessionId) return ONBOARD_FAILED;
      try {
        const view = await api.onboard(sessionId, values);
        setPlace(view.place);
        setPending(view.pending);
        setShop(view.shop ?? { name: values.name, category: values.category });
        absorb(view);
        setOnline(true);
        return null;
      } catch (e) {
        if (isOffline(e)) setOnline(false);
        return isOffline(e) ? friendly(e) : ONBOARD_FAILED;
      }
    },
    [api, sessionId, absorb],
  );

  /** [지금 받아 보기]: 대화 턴이 아니다. 비서가 먼저 보낸 것처럼 보고 말풍선 하나만 더한다. */
  const runReport = useCallback(async () => {
    if (!sessionId) return REPORT_FAILED;
    try {
      absorb(await api.runReviewReport(sessionId));
      setOnline(true);
      return null;
    } catch (e) {
      if (isOffline(e)) setOnline(false);
      return isOffline(e) ? friendly(e) : REPORT_FAILED;
    }
  }, [api, sessionId, absorb]);

  /** 루틴 설정 스위치·일정: 대화 턴이 아니다. 바뀐 설정과 보고 한 줄만 반영한다. */
  const changeRoutines = useCallback(
    async (change: RoutinesChange) => {
      if (!sessionId) return ROUTINES_FAILED;
      try {
        absorb(await api.setRoutines(sessionId, change));
        setOnline(true);
        return null;
      } catch (e) {
        if (isOffline(e)) setOnline(false);
        return isOffline(e) ? friendly(e) : ROUTINES_FAILED;
      }
    },
    [api, sessionId, absorb],
  );

  // 정해진 때의 리뷰 보고: 서버는 세션을 조회할 때 기한 지난 보고를 메시지로 쌓아 한 번만 건넨다.
  // 대화가 열려 있는 동안 조회(/place)를 가끔 다시 읽어 그 메시지를 받는다. 실패는 조용히 넘긴다.
  useEffect(() => {
    if (!sessionId || reportPollMs <= 0) return;
    const timer = setInterval(() => {
      if (inFlight.current) return; // 비서 답을 기다리는 중이면 그 답이 함께 가져온다
      api
        .view(sessionId)
        .then(absorb)
        .catch(() => undefined);
    }, reportPollMs);
    return () => clearInterval(timer);
  }, [api, sessionId, reportPollMs, absorb]);

  // 메뉴판 사진으로 올린 메뉴는 설명·흰 배경 사진을 뒤에서 채운다. 채우는 칸이 남아 있는 동안만
  // 메뉴 탭을 자주 다시 읽어 채워지는 모습을 보인다. 실패는 조용히 넘긴다.
  const filling = Boolean(place?.menu.some((m) => m.filling?.length));
  useEffect(() => {
    if (!sessionId || !filling) return;
    const timer = setInterval(() => {
      if (inFlight.current) return; // 비서 답이 오면 그 답이 가게 화면을 함께 가져온다
      api
        .view(sessionId)
        .then((view) => {
          if (inFlight.current) return;
          setPlace(view.place);
          setPending(view.pending);
        })
        .catch(() => undefined);
    }, FILL_POLL_MS);
    return () => clearInterval(timer);
  }, [api, sessionId, filling]);

  /** 메뉴 탭 직접 수정·삭제: 대화 턴이 아니다. 가게 화면만 바꾼다. */
  const changeMenu = useCallback(
    async (call: (id: string) => Promise<View>) => {
      if (!sessionId) return MENU_FAILED;
      try {
        const view = await call(sessionId);
        setPlace(view.place);
        setPending(view.pending);
        setOnline(true);
        return null;
      } catch (e) {
        if (isOffline(e)) setOnline(false);
        if (e instanceof ApiError && e.status === 422) return "값을 확인해 주세요. 이름은 비울 수 없고, 같은 이름이 있으면 안 되며, 가격은 0보다 커야 해요.";
        return isOffline(e) ? friendly(e) : MENU_FAILED;
      }
    },
    [sessionId],
  );
  const editMenu = useCallback((menuId: string, edit: MenuEdit) => changeMenu((id) => api.editMenu(id, menuId, edit)), [api, changeMenu]);
  const polishMenu = useCallback(
    async (menuId: string) => {
      if (!sessionId) return MENU_FAILED;
      try {
        const view = await api.polishMenu(sessionId, menuId);
        setPlace(view.place);
        setPending(view.pending);
        return view.message;
      } catch (e) {
        return isOffline(e) ? friendly(e) : "사진 다듬기를 시작하지 못했어요. 다시 시도해 주세요.";
      }
    },
    [api, sessionId],
  );
  const deleteMenu = useCallback((menuId: string) => changeMenu((id) => api.deleteMenu(id, menuId)), [api, changeMenu]);
  const refresh = useCallback(async () => {
    if (!sessionId) return;
    await api
      .view(sessionId)
      .then(absorb)
      .catch(() => undefined);
  }, [api, sessionId, absorb]);

  const openCoupon = useCallback((initial?: CouponInput) => setCouponForm(initial ?? blankCoupon()), []);
  const closeCoupon = useCallback(() => setCouponForm(null), []);

  const act = useCallback(async (action: Action) => {
    // 말로 보내는 칩(chat.say, 버튼이 없는 기능의 맥락 칩): 그 말을 사장님 말 한 턴으로 보낸다.
    // 쓰기는 이 말로 시작되는 미리보기·승인을 그대로 거친다.
    if (action.action_id === SAY_ACTION) {
      const text = typeof action.args.text === "string" ? action.args.text.trim() : "";
      if (text) await send(text);
      return;
    }
    // 값 없이 온 쿠폰 만들기(왼쪽 버튼·다음 액션 칩)는 서버에 묻지 않고 입력 블록을 연다.
    if (action.action_id === COUPON_ACTION && Object.keys(action.args).length === 0) {
      if (!inFlight.current) setCouponForm((open) => open ?? blankCoupon());
      return;
    }
    // 값 없이 온 가게 정보 수정(도구함)은 지금 가게 값으로 채운 입력 블록을 연다.
    if (action.action_id === BUSINESS_ACTION && Object.keys(action.args).length === 0) {
      if (!inFlight.current) setBusinessForm((open) => open ?? businessInitial(latest.current.place?.business_info));
      return;
    }
    if (!sessionId || inFlight.current) return;
    if (!latest.current.messages.some((m) => m.role === "owner")) {
      storage.rememberRecent({ id: sessionId, title: action.label, at: Date.now() });
      setRecent(storage.loadRecent());
    }
    setMessages((m) => [...m, { id: newId(), role: "owner", text: action.label }]);
    await performAction(action);
  }, [performAction, send, sessionId]);

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

  const closeBusiness = useCallback(() => setBusinessForm(null), []);
  const submitBusiness = useCallback(async (changes: Partial<BusinessInput>) => {
    if (!sessionId || inFlight.current || !businessForm) return;
    const label = businessActionLabel(changes);
    sentBusiness.current = { label, initial: businessForm, changes };
    setBusinessForm(null);
    await act({ action_id: BUSINESS_ACTION, label, args: changes });
  }, [act, businessForm, sessionId]);
  const draftIntroduction = useCallback(async () => {
    if (!sessionId) throw new Error("no session");
    return (await api.draftIntroduction(sessionId)).text;
  }, [api, sessionId]);

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
    sessionId, messages, place, shop, pending, log, recent, busy, online, error,
    couponForm, reviewOf, openCoupon, closeCoupon, submitCoupon,
    businessForm, closeBusiness, submitBusiness, draftIntroduction,
    send, act, retry, newChat, open, addLog, notify, onboard, reviewReport, runReport,
    routines, changeRoutines, editMenu, deleteMenu, polishMenu, readingSince, refresh,
  };
}
