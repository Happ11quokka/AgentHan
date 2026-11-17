import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, test } from "vitest";
import App from "./App";
import type { ReviewsView } from "./types";
import {
  INBOX,
  inboxWith,
  REVIEWS,
  REVIEW_DRAFT_REPLY,
  REVIEW_POSTED_REPLY,
  SEED,
  draft,
  fakeApi,
  gone,
  offline,
  photoPending,
  reviewPending,
  withCake,
} from "./test/fakeApi";

const chatArea = () => within(screen.getByRole("main", { name: "비서와 대화" }));

const CAKE = new File(["jpeg"], "cake.jpg", { type: "image/jpeg" });

async function open(api = fakeApi()) {
  const user = userEvent.setup();
  render(<App api={api} />);
  await screen.findByText(/무엇을 도와드릴까요/);
  return { user, api };
}

async function say(user: ReturnType<typeof userEvent.setup>, text: string) {
  await user.type(screen.getByRole("textbox", { name: "비서에게 말하기" }), text);
  await user.click(screen.getByRole("button", { name: "보내기" }));
}

describe("대화", () => {
  test("첫 화면에 비서 프로필과 인사가 보이고 새 대화가 시작된다", async () => {
    const { api } = await open();
    expect(api.start).toHaveBeenCalledTimes(1);
    const side = screen.getByRole("complementary", { name: "비서와 가게" });
    expect(within(side).getByRole("img", { name: "비서 프로필" })).toBeInTheDocument();
    expect(within(side).getByText("연결됨")).toBeInTheDocument();
  });

  test("말을 보내면 내 말풍선, 기다림 표시, 비서 답이 차례로 보인다", async () => {
    const api = fakeApi([{ reply: "영업시간은 9시부터예요.", place: SEED, pending: [] }]);
    const { user } = await open(api);
    await say(user, "영업시간 알려 줘");
    expect(api.say).toHaveBeenCalledWith("s1", "영업시간 알려 줘");
    expect(chatArea().getByText("영업시간 알려 줘")).toBeInTheDocument();
    expect(await screen.findByText("영업시간은 9시부터예요.")).toBeInTheDocument();
    expect(screen.queryByRole("status", { name: "비서가 답을 쓰는 중" })).not.toBeInTheDocument();
  });

  test("비서 답의 시스템 줄(JSON·더미 안내)은 숨기고 굵게·목록·인용은 모양으로 보인다", async () => {
    const saved =
      '저장했어요.\n더미 저장 확인 (실제 발송 없음): [{"ok": true, "action": "updated", "record": {"id": "m01"}}]';
    const asked =
      '메뉴 등록·수정: 아래처럼 바꿀까요? (더미 저장소, 실제 네이버 반영 없음)\n{"id": "m01", "price": 9000}\n네 / 취소 / 고칠 내용 말하기';
    const draftReply =
      '초안을 만들었어요.\n**답글 초안** · 리뷰: "웨이팅이 길었어요."\n> 기다리게 해서 죄송해요.\n- 첫째 줄\n1. 둘째 줄';
    const api = fakeApi([
      { reply: saved, place: SEED, pending: [] },
      { reply: asked, place: SEED, pending: [] },
      { reply: draftReply, place: SEED, pending: [] },
    ]);
    const { user } = await open(api);
    await say(user, "메도빅 9000원");
    expect(await chatArea().findByText("저장했어요.")).toBeInTheDocument();
    await say(user, "메도빅 9000원으로");
    expect(await chatArea().findByText("메뉴 등록·수정: 아래처럼 바꿀까요?")).toBeInTheDocument();
    await say(user, "웨이팅 리뷰 답글");
    const bold = await chatArea().findByText("답글 초안");
    expect(bold.tagName).toBe("STRONG");
    expect(chatArea().getByText("기다리게 해서 죄송해요.").closest("blockquote")).not.toBeNull();
    expect(chatArea().getByText("첫째 줄").tagName).toBe("LI");
    expect(chatArea().getByText("둘째 줄").tagName).toBe("LI");
    const all = screen.getByRole("main", { name: "비서와 대화" }).textContent ?? "";
    for (const hidden of ["더미", "{", "**", "네 / 취소"]) expect(all).not.toContain(hidden);
  });

  test("서버가 실패하면 쉬운 말로 알리고 다시 시도하면 같은 말을 보낸다", async () => {
    const api = fakeApi([offline(), { reply: "다시 왔어요.", place: SEED, pending: [] }]);
    const { user } = await open(api);
    await say(user, "안녕");
    expect(await screen.findByText(/서버에 연결하지 못했어요/)).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "다시 시도" }));
    expect(await screen.findByText("다시 왔어요.")).toBeInTheDocument();
    expect(api.say).toHaveBeenNthCalledWith(2, "s1", "안녕");
  });
});

describe("액션", () => {

  test("시작 화면의 리뷰 버튼은 액션을 보낸다", async () => {
    const { user, api } = await open();
    await user.click(screen.getByRole("button", { name: "요즘 리뷰 어때?" }));
    expect(api.act).toHaveBeenCalledWith("s1", { action_id: "review.summary", args: {}, label: "요즘 리뷰 어때?" });
    expect(api.say).not.toHaveBeenCalled();
  });
  test("응답의 칩과 승인 카드는 인자와 버전을 그대로 보낸다", async () => {
    const pending = { task_id: "t2", version: 3, feature: "coupon", after: SEED, changes: {} };
    const api = fakeApi([
      { reply: "고르세요", place: SEED, pending: [], next_actions: [{ action_id: "stats.day", label: "오늘 통계", args: { period: "day" } }] },
      { reply: "저장할까요?", place: SEED, pending: [pending] },
      { reply: "저장했어요", place: SEED, pending: [] },
    ]);
    const { user } = await open(api);
    await user.click(screen.getByRole("button", { name: "요즘 리뷰" }));
    await user.click(await screen.findByRole("button", { name: "오늘 통계" }));
    expect(api.act).toHaveBeenLastCalledWith("s1", { action_id: "stats.day", label: "오늘 통계", args: { period: "day" } });
    const card = await screen.findByRole("region", { name: "저장 전 확인" });
    await user.click(within(card).getByRole("button", { name: "저장" }));
    expect(api.act).toHaveBeenLastCalledWith("s1", { action_id: "task.approve", label: "저장", args: { task_id: "t2", version: 3 } });
    expect(api.say).not.toHaveBeenCalled();
    expect(chatArea().getByText("오늘 통계", { selector: "p" })).toBeInTheDocument();
  });

  test("승인 카드 취소는 해당 작업을 지정한다", async () => {
    const pending = { task_id: "t3", version: 2, feature: "review", kind: "review" as const, reply: "감사합니다" };
    const api = fakeApi([{ reply: "확인해 주세요", place: SEED, pending: [pending] }]);
    const { user } = await open(api);
    await user.click(screen.getByRole("button", { name: "요즘 리뷰" }));
    const card = await screen.findByRole("region", { name: "저장 전 확인" });
    expect(within(card).getByText("감사합니다")).toBeInTheDocument();
    await user.click(within(card).getByRole("button", { name: "취소" }));
    expect(api.act).toHaveBeenLastCalledWith("s1", { action_id: "task.cancel", label: "취소", args: { task_id: "t3" } });
    expect(api.say).not.toHaveBeenCalled();
  });
});

describe("사진과 승인 카드", () => {
  test("사진을 올리면 글과 함께 보내고 내 말풍선에 사진이 보인다", async () => {
    const api = fakeApi([
      { reply: "1. 밀푀유 케이크\n가격은 얼마로 올릴까요?", place: SEED, pending: [photoPending([draft()])] },
    ]);
    const { user } = await open(api);
    await user.type(screen.getByRole("textbox", { name: "비서에게 말하기" }), "이거 올려 줘");
    await user.upload(screen.getByLabelText("사진 올리기"), CAKE);
    await user.click(screen.getByRole("button", { name: "보내기" }));
    expect(api.sendPhoto).toHaveBeenCalledWith("s1", CAKE, "이거 올려 줘");
    expect(screen.getByRole("img", { name: "보낸 사진" })).toBeInTheDocument();
  });

  test("가격이 없는 초안은 저장을 막고 비서 질문을 보여 준다", async () => {
    const api = fakeApi([
      {
        reply: "1. 밀푀유 케이크\n이름이 '밀푀유 케이크' 맞나요? 가격은 얼마로 올릴까요?",
        place: SEED,
        pending: [photoPending([draft()])],
      },
    ]);
    const { user } = await open(api);
    await user.upload(screen.getByLabelText("사진 올리기"), CAKE);
    await user.click(screen.getByRole("button", { name: "보내기" }));
    const card = await screen.findByRole("region", { name: "저장 전 확인" });
    expect(within(card).getByText("밀푀유 케이크")).toBeInTheDocument();
    expect(within(card).getByText("가격을 알려 주세요")).toBeInTheDocument();
    expect(within(card).getByText("AI 추측")).toBeInTheDocument();
    expect(within(card).getByRole("button", { name: "저장" })).toBeDisabled();
    expect(within(card).getByText("저장하려면 대화로 알려 주세요: 1번 가격")).toBeInTheDocument();
  });

  test("기존 메뉴에 사진만 붙일 때 저장은 '○○ 사진 바꿔'를 보낸다", async () => {
    const attach = draft({
      name_ko: "초코 케이크",
      price: null,
      change: "photo_attach",
      previous_price: 7500,
      menu_name: "메도빅",
      notes: ["food_guess"],
    });
    const api = fakeApi([{ reply: "기존 메뉴 메도빅에 이 사진을 붙일까요?", place: SEED, pending: [photoPending([attach])] }]);
    const { user } = await open(api);
    await user.upload(screen.getByLabelText("사진 올리기"), CAKE);
    await user.click(screen.getByRole("button", { name: "보내기" }));
    const card = await screen.findByRole("region", { name: "저장 전 확인" });
    expect(within(card).getByText("메도빅에 사진만 바꿈")).toBeInTheDocument();
    await user.click(within(card).getByRole("button", { name: "저장" }));
    expect(api.sendPhoto).toHaveBeenCalledTimes(1);
    expect(api.say).toHaveBeenLastCalledWith("s1", "메도빅 사진 바꿔");
  });

  test("메뉴판 줄이 모두 그대로여도 저장할 수 있다", async () => {
    const same = [draft({ name_ko: "메도빅", price: 7500, change: "unchanged", previous_price: 7500, notes: [] })];
    const api = fakeApi([{ reply: "읽었어요.", place: SEED, pending: [photoPending(same)] }]);
    const { user } = await open(api);
    await say(user, "메뉴판 올렸어");
    const card = await screen.findByRole("region", { name: "저장 전 확인" });
    await user.click(within(card).getByRole("button", { name: "저장" }));
    expect(api.say).toHaveBeenLastCalledWith("s1", "네");
  });

  test("첨부한 사진은 파일 이름 대신 '사진 1장'으로 보인다", async () => {
    const { user } = await open();
    await user.upload(screen.getByLabelText("사진 올리기"), CAKE);
    expect(screen.getByText("사진 1장")).toBeInTheDocument();
    expect(screen.queryByText("cake.jpg")).not.toBeInTheDocument();
  });

  test("저장을 누르면 '네'를 보내고 끝난 카드와 오늘 한 일이 남는다", async () => {
    const ready = photoPending([draft({ price: 8500, notes: [] })], withCake);
    const api = fakeApi([
      { reply: "이대로 저장할까요?", place: SEED, pending: [ready] },
      { reply: "저장했어요.", place: withCake, pending: [] },
    ]);
    const { user } = await open(api);
    await say(user, "가격 8500원");
    const card = await screen.findByRole("region", { name: "저장 전 확인" });
    expect(within(card).getByText("새 메뉴")).toBeInTheDocument();
    expect(within(card).getByText("8,500원")).toBeInTheDocument();
    await user.click(within(card).getByRole("button", { name: "저장" }));
    expect(api.say).toHaveBeenLastCalledWith("s1", "네");
    expect(await screen.findByText("저장했어요.")).toBeInTheDocument();
    expect(screen.queryByRole("region", { name: "저장 전 확인" })).not.toBeInTheDocument();
    expect(screen.getByText("저장 완료")).toBeInTheDocument();
    const today = screen.getByRole("list", { name: "오늘 한 일" });
    expect(within(today).getByText("새 메뉴 밀푀유 케이크 8,500원")).toBeInTheDocument();
  });

  test("취소를 누르면 '취소'를 보낸다", async () => {
    const api = fakeApi([
      { reply: "이대로 저장할까요?", place: SEED, pending: [photoPending([draft({ price: 8500 })], withCake)] },
      { reply: "취소했어요.", place: SEED, pending: [] },
    ]);
    const { user } = await open(api);
    await say(user, "가격 8500원");
    const card = await screen.findByRole("region", { name: "저장 전 확인" });
    await user.click(within(card).getByRole("button", { name: "취소" }));
    expect(api.say).toHaveBeenLastCalledWith("s1", "취소");
    expect(await screen.findByText("저장 안 함")).toBeInTheDocument();
  });

  test("메뉴판 초안의 상태는 사장님 말로만 보이고 내부 코드는 안 보인다", async () => {
    const drafts = [
      draft({ name_ko: "메도빅", price: 8000, change: "price_change", previous_price: 7500, notes: [] }),
      draft({ name_ko: "나폴레옹", price: 7000, change: "unchanged", previous_price: 7000, notes: [] }),
      draft({ name_ko: "블린", price: 9000, change: "photo_attach", previous_price: 9000, notes: [] }),
      draft({ name_ko: "펠메니", price: null, change: null, notes: ["price_missing"] }),
    ];
    const api = fakeApi([{ reply: "읽었어요.", place: SEED, pending: [photoPending(drafts)] }]);
    const { user } = await open(api);
    await user.upload(screen.getByLabelText("사진 올리기"), CAKE);
    await user.click(screen.getByRole("button", { name: "보내기" }));
    const card = await screen.findByRole("region", { name: "저장 전 확인" });
    expect(within(card).getByText("가격 7,500 → 8,000")).toBeInTheDocument();
    expect(within(card).getByText("그대로")).toBeInTheDocument();
    expect(within(card).getByText("사진만 바꿈")).toBeInTheDocument();
    expect(within(card).getByText("가격을 알려 주세요")).toBeInTheDocument();
    const text = document.body.textContent ?? "";
    for (const code of ["price_change", "photo_attach", "unchanged", "price_missing", "upsert_menu", "s1", "task_id"]) {
      expect(text).not.toContain(code);
    }
  });
});

describe("사이드카 [내 가게]", () => {
  test("메뉴·영업시간·쿠폰이 보이고 저장 전 변경은 강조된다", async () => {
    const coupon = {
      id: "coupon-1",
      title: "오픈 기념",
      discount_type: "percent" as const,
      discount_value: 10,
      start_date: "2026-11-20",
      end_date: "2026-11-30",
    };
    const after = { ...SEED, coupon: [coupon] };
    const pending = {
      task_id: "t2",
      feature: "coupon",
      after,
      changes: { coupon: { added: ["coupon-1"], updated: [], removed: [] } },
    };
    const api = fakeApi([{ reply: "이 쿠폰을 만들까요?", place: SEED, pending: [pending] }]);
    const { user } = await open(api);
    const shop = screen.getByRole("tabpanel", { name: "내 가게" });
    expect(within(shop).getByText("메도빅")).toBeInTheDocument();
    expect(within(shop).getByText("7,500원")).toBeInTheDocument();
    expect(within(shop).getByText("일요일")).toBeInTheDocument();
    expect(within(shop).getByText("쉬는 날")).toBeInTheDocument();
    await say(user, "쿠폰 만들어 줘");
    const added = await within(shop).findByText("오픈 기념");
    expect(added.closest("[data-mark]")).toHaveAttribute("data-mark", "added");
    const card = screen.getByRole("region", { name: "저장 전 확인" });
    expect(within(card).getByText("새 쿠폰 오픈 기념 10% 할인")).toBeInTheDocument();
  });
});

describe("왼쪽 자주 쓰는 일", () => {
  test("요즘 리뷰·이번 주 손님은 액션을 보내고, 쿠폰 만들기는 입력 블록을 연다", async () => {
    const { user, api } = await open();
    await user.click(screen.getByRole("button", { name: "쿠폰 만들기" }));
    expect(chatArea().getByRole("form", { name: "쿠폰 만들기" })).toBeInTheDocument();
    expect(api.say).not.toHaveBeenCalled();
    expect(api.act).not.toHaveBeenCalled();
    await user.click(screen.getByRole("button", { name: "요즘 리뷰" }));
    await waitFor(() => expect(api.act).toHaveBeenLastCalledWith("s1", { action_id: "review.summary", args: {}, label: "요즘 리뷰" }));
    expect(screen.getByRole("tab", { name: "리뷰" })).toHaveAttribute("aria-selected", "true");
    await user.click(screen.getByRole("button", { name: "이번 주 손님" }));
    await waitFor(() => expect(api.act).toHaveBeenLastCalledWith("s1", { action_id: "stats.week", args: {}, label: "이번 주 손님" }));
    expect(screen.getByRole("tab", { name: "손님 통계" })).toHaveAttribute("aria-selected", "true");
  });

  test("메뉴 사진 올리기는 사진 고르기를 열고 [내 가게]를 보여 준다", async () => {
    const { user } = await open();
    await user.click(screen.getByRole("tab", { name: "리뷰" }));
    const input = screen.getByLabelText<HTMLInputElement>("사진 올리기");
    let opened = false;
    input.addEventListener("click", () => (opened = true));
    await user.click(screen.getByRole("button", { name: "메뉴 사진 올리기" }));
    expect(opened).toBe(true);
    expect(screen.getByRole("tab", { name: "내 가게" })).toHaveAttribute("aria-selected", "true");
  });
});

describe("새 대화와 최근 대화", () => {
  test("새 대화를 열고, 최근 대화를 누르면 그 대화로 돌아간다", async () => {
    const { user, api } = await open();
    await say(user, "첫 대화 질문");
    await screen.findByText("알겠어요.");
    await user.click(screen.getByRole("button", { name: "새 대화" }));
    await waitFor(() => expect(api.start).toHaveBeenCalledTimes(2));
    expect(chatArea().queryByText("첫 대화 질문")).not.toBeInTheDocument();
    const recent = screen.getByRole("list", { name: "최근 대화" });
    await user.click(within(recent).getByRole("button", { name: /첫 대화 질문/ }));
    expect(api.view).toHaveBeenCalledWith("s1");
    expect(await chatArea().findByText("첫 대화 질문")).toBeInTheDocument();
  });

  test("서버에서 사라진 대화는 목록에서 빼고 새 대화를 연다", async () => {
    const api = fakeApi([], gone());
    const { user } = await open(api);
    await say(user, "사라질 대화");
    await screen.findByText("알겠어요.");
    await user.click(screen.getByRole("button", { name: "새 대화" }));
    await waitFor(() => expect(api.start).toHaveBeenCalledTimes(2));
    const recent = screen.getByRole("list", { name: "최근 대화" });
    await user.click(within(recent).getByRole("button", { name: /사라질 대화/ }));
    await waitFor(() => expect(api.start).toHaveBeenCalledTimes(3));
    expect(screen.queryByRole("button", { name: /사라질 대화/ })).not.toBeInTheDocument();
  });

  test("새로고침하면 마지막 대화를 이어서 연다", async () => {
    const api = fakeApi();
    const first = await open(api);
    await say(first.user, "남아 있을 말");
    await screen.findByText("알겠어요.");
    cleanup(); // 탭을 닫고 다시 연 것과 같다. 브라우저 저장소는 남아 있다.
    render(<App api={api} />);
    await waitFor(() => expect(api.view).toHaveBeenCalledWith("s1"));
    expect(await chatArea().findByText("남아 있을 말")).toBeInTheDocument();
    expect(api.start).toHaveBeenCalledTimes(1);
  });
});

describe("고친 흐름", () => {
  test("탭을 누르면 그 탭이 열리고 화살표 키로 옮겨 간다", async () => {
    const { user } = await open();
    await user.click(screen.getByRole("tab", { name: "손님 통계" }));
    expect(await screen.findByRole("tabpanel", { name: "손님 통계" })).toBeInTheDocument();
    await user.keyboard("{ArrowLeft}");
    expect(screen.getByRole("tab", { name: "리뷰" })).toHaveAttribute("aria-selected", "true");
  });

  test("메뉴 사진 올리기는 접힌 사이드카도 펼친다", async () => {
    const { user } = await open();
    const toggle = screen.getByRole("button", { name: "내 가게 보기" });
    expect(toggle).toHaveAttribute("aria-expanded", "false");
    await user.click(screen.getByRole("button", { name: "메뉴 사진 올리기" }));
    expect(toggle).toHaveAttribute("aria-expanded", "true");
  });

  test("오늘 한 일에는 오늘 승인한 것만 보인다", async () => {
    const yesterday = Date.now() - 24 * 60 * 60 * 1000;
    localStorage.setItem("agenthan.recent.v1", JSON.stringify([{ id: "old", title: "어제 대화", at: yesterday }]));
    localStorage.setItem(
      "agenthan.chat.v1.old",
      JSON.stringify({ messages: [], log: [{ at: yesterday, lines: ["새 메뉴 어제 메뉴 1,000원"] }] }),
    );
    await open();
    expect(screen.queryByText("새 메뉴 어제 메뉴 1,000원")).not.toBeInTheDocument();
  });

  test("승인 카드 없이 가게가 바뀌어도 오늘 한 일에 쌓지 않는다", async () => {
    const api = fakeApi([{ reply: "바꿨어요.", place: withCake, pending: [] }]);
    const { user } = await open(api);
    await say(user, "그냥 바꿔");
    await screen.findByText("바꿨어요.");
    expect(screen.queryByRole("list", { name: "오늘 한 일" })).not.toBeInTheDocument();
  });

  test("서버가 꺼지면 연결 끊김이 보이고, 대화 열기 실패 뒤 다시 시도는 그 대화를 다시 연다", async () => {
    localStorage.setItem("agenthan.recent.v1", JSON.stringify([{ id: "s9", title: "열 대화", at: Date.now() }]));
    const api = fakeApi();
    api.view.mockRejectedValueOnce(offline());
    const user = userEvent.setup();
    render(<App api={api} />);
    expect(await screen.findByText(/서버에 연결하지 못했어요/)).toBeInTheDocument();
    expect(screen.getByText("연결 끊김")).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "다시 시도" }));
    await waitFor(() => expect(api.view).toHaveBeenCalledTimes(2));
    expect(api.view).toHaveBeenLastCalledWith("s9");
    expect(api.start).not.toHaveBeenCalled();
    expect(await screen.findByText("연결됨")).toBeInTheDocument();
  });

  test("보내기를 빠르게 두 번 눌러도 한 번만 보낸다", async () => {
    const { user, api } = await open();
    await user.type(screen.getByRole("textbox", { name: "비서에게 말하기" }), "한 번만");
    await user.dblClick(screen.getByRole("button", { name: "보내기" }));
    await screen.findByText("알겠어요.");
    expect(api.say).toHaveBeenCalledTimes(1);
  });
});

describe("사이드카 [리뷰]", () => {
  test("평균 별점·답글 안 단 수와 거른 리뷰를 보여 주고 원문은 눌러야 보인다", async () => {
    const { user } = await open();
    await user.click(screen.getByRole("tab", { name: "리뷰" }));
    const panel = await screen.findByRole("tabpanel", { name: "리뷰" });
    expect(await within(panel).findByText("3.0")).toBeInTheDocument();
    expect(within(panel).getByText("답글 안 단 리뷰 1개")).toBeInTheDocument();
    expect(within(panel).getByText(REVIEWS.reviews[0].text)).toBeInTheDocument();
    expect(within(panel).queryByText(REVIEWS.reviews[0].original!)).not.toBeInTheDocument();
    await user.click(within(panel).getByRole("button", { name: "원문 보기" }));
    expect(within(panel).getByText(REVIEWS.reviews[0].original!)).toBeInTheDocument();
    expect(within(panel).getByText("답글 달림")).toBeInTheDocument();
  });

  test("답글 초안 받기는 그 리뷰의 답글을 비서에게 부탁한다", async () => {
    const { user, api } = await open();
    await user.click(screen.getByRole("tab", { name: "리뷰" }));
    const panel = await screen.findByRole("tabpanel", { name: "리뷰" });
    const [first] = await within(panel).findAllByRole("button", { name: "답글 초안 받기" });
    await user.click(first);
    expect(api.act).toHaveBeenLastCalledWith("s1", { action_id: "review.draft_reply", args: { review_id: "r04" }, label: "답글 초안 받기" });
    expect(api.say).not.toHaveBeenCalled();
  });
});

describe("쿠폰 입력 블록", () => {
  async function openCoupon(api = fakeApi()) {
    const opened = await open(api);
    await opened.user.click(screen.getByRole("button", { name: "쿠폰 만들기" }));
    const form = chatArea().getByRole("form", { name: "쿠폰 만들기" });
    return { ...opened, form: within(form) };
  }
  const setDate = (input: HTMLElement, value: string) => fireEvent.change(input, { target: { value } });

  test("값을 넣고 [미리 보기]를 누르면 coupon.create 액션에 그 값을 그대로 보내고 블록이 닫힌다", async () => {
    const pending = { task_id: "t7", version: 1, feature: "coupon", after: SEED, changes: {} };
    const { user, api, form } = await openCoupon(fakeApi([{ reply: "이 쿠폰을 만들까요?", place: SEED, pending: [pending] }]));
    await user.type(form.getByLabelText("쿠폰 이름"), "겨울 할인");
    await user.type(form.getByLabelText("할인 값"), "10");
    setDate(form.getByLabelText("시작일"), "2026-12-01");
    setDate(form.getByLabelText("종료일"), "2026-12-15");
    expect(form.getByText("겨울 할인 · 10% 할인 · 12/01 ~ 12/15")).toBeInTheDocument();
    await user.click(form.getByRole("radio", { name: "원" }));
    await user.clear(form.getByLabelText("할인 값"));
    await user.type(form.getByLabelText("할인 값"), "2000");
    expect(form.getByText("겨울 할인 · 2,000원 할인 · 12/01 ~ 12/15")).toBeInTheDocument();
    await user.click(form.getByRole("button", { name: "미리 보기" }));
    expect(api.act).toHaveBeenCalledWith("s1", {
      action_id: "coupon.create",
      label: "쿠폰 만들기: 겨울 할인",
      args: { title: "겨울 할인", discount_type: "amount", discount_value: 2000, start_date: "2026-12-01", end_date: "2026-12-15" },
    });
    expect(api.say).not.toHaveBeenCalled();
    expect(chatArea().queryByRole("form", { name: "쿠폰 만들기" })).not.toBeInTheDocument();
    expect(chatArea().getByText("쿠폰 만들기: 겨울 할인")).toBeInTheDocument();
  });

  test("기본 기간은 오늘부터 14일이고, 빈 칸·범위 밖 값·거꾸로 된 기간이면 [미리 보기]가 막히고 이유가 보인다", async () => {
    const { user, form } = await openCoupon();
    const start = form.getByLabelText<HTMLInputElement>("시작일").value;
    const end = form.getByLabelText<HTMLInputElement>("종료일").value;
    expect((Date.parse(end) - Date.parse(start)) / 86_400_000).toBe(14);
    const submit = form.getByRole("button", { name: "미리 보기" });
    expect(submit).toBeDisabled();
    expect(form.getByText("쿠폰 이름을 적어 주세요.")).toBeInTheDocument();
    await user.type(form.getByLabelText("쿠폰 이름"), "오픈 기념");
    expect(submit).toBeDisabled();
    expect(form.getByText("할인 값을 1 이상의 숫자로 적어 주세요.")).toBeInTheDocument();
    await user.type(form.getByLabelText("할인 값"), "150");
    expect(submit).toBeDisabled();
    expect(form.getByText("% 할인은 1~100 사이로 적어 주세요.")).toBeInTheDocument();
    await user.clear(form.getByLabelText("할인 값"));
    await user.type(form.getByLabelText("할인 값"), "15");
    expect(submit).toBeEnabled();
    setDate(form.getByLabelText("종료일"), "2020-01-01");
    expect(submit).toBeDisabled();
    expect(form.getByText("종료일이 시작일보다 빨라요.")).toBeInTheDocument();
    await user.click(form.getByRole("button", { name: "취소" }));
    expect(chatArea().queryByRole("form", { name: "쿠폰 만들기" })).not.toBeInTheDocument();
  });

  test("비서 답의 값 없는 쿠폰 만들기 칩은 블록을 열고, 서버가 값을 거절하면 넣은 값 그대로 다시 연다", async () => {
    const api = fakeApi([
      { reply: "쿠폰을 만들어 볼까요?", place: SEED, pending: [], next_actions: [{ action_id: "coupon.create", label: "쿠폰 만들기", args: {} }] },
      { reply: "값을 다시 확인해 주세요.", place: SEED, pending: [] },
    ]);
    const { user } = await open(api);
    await say(user, "손님이 줄었어");
    await user.click(await chatArea().findByRole("button", { name: "쿠폰 만들기" }));
    expect(api.act).not.toHaveBeenCalled();
    const form = within(chatArea().getByRole("form", { name: "쿠폰 만들기" }));
    await user.type(form.getByLabelText("쿠폰 이름"), "단골 할인");
    await user.type(form.getByLabelText("할인 값"), "5");
    await user.click(form.getByRole("button", { name: "미리 보기" }));
    expect(await chatArea().findByText("값을 다시 확인해 주세요.")).toBeInTheDocument();
    const again = within(chatArea().getByRole("form", { name: "쿠폰 만들기" }));
    expect(again.getByLabelText("쿠폰 이름")).toHaveValue("단골 할인");
    expect(again.getByLabelText("할인 값")).toHaveValue(5);
  });
});

describe("리뷰 답글 확인 카드", () => {
  test("원래 리뷰와 올릴 답글을 보여 주고, 말풍선에서는 카드와 겹치는 인용·질문을 숨긴다", async () => {
    const api = fakeApi([{ reply: REVIEW_DRAFT_REPLY, place: SEED, pending: [reviewPending()] }]);
    const { user } = await open(api);
    await say(user, "짜다는 리뷰에 답글 써 줘");
    const card = within(await screen.findByRole("region", { name: "저장 전 확인" }));
    expect(await card.findByText(REVIEWS.reviews[0].text)).toBeInTheDocument();
    expect(card.getByLabelText("별점 1점")).toBeInTheDocument();
    expect(card.getByText("11/06")).toBeInTheDocument();
    expect(card.getByText("불편을 드려 죄송합니다. 간을 다시 맞추겠습니다.")).toBeInTheDocument();
    expect(card.getByText("고치고 싶으면 대화로 말해 주세요.")).toBeInTheDocument();
    expect(card.getByRole("button", { name: "답글 올리기" })).toBeEnabled();
    expect(card.getByRole("button", { name: "취소" })).toBeInTheDocument();
    expect(chatArea().getByText("답글 초안이에요. 내용을 확인해 주세요.")).toBeInTheDocument();
    expect(chatArea().getAllByText("불편을 드려 죄송합니다. 간을 다시 맞추겠습니다.")).toHaveLength(1);
    expect(chatArea().queryByText("이대로 올릴까요?")).not.toBeInTheDocument();
    expect(screen.getByRole("main", { name: "비서와 대화" }).querySelector(".bubble blockquote")).toBeNull();
  });

  test("[답글 올리기]는 task.approve를 보내고, 더미 안내 없이 '리뷰 답글 올림'을 결과와 오늘 한 일에 남긴다", async () => {
    const api = fakeApi([
      { reply: REVIEW_DRAFT_REPLY, place: SEED, pending: [reviewPending()] },
      { reply: REVIEW_POSTED_REPLY, place: SEED, pending: [] },
    ]);
    const { user } = await open(api);
    await say(user, "짜다는 리뷰에 답글 써 줘");
    const card = await screen.findByRole("region", { name: "저장 전 확인" });
    await user.click(within(card).getByRole("button", { name: "답글 올리기" }));
    expect(api.act).toHaveBeenLastCalledWith("s1", { action_id: "task.approve", label: "답글 올리기", args: { task_id: "t5", version: 1 } });
    expect(await chatArea().findByText("답글을 저장했어요.")).toBeInTheDocument();
    expect(screen.getByRole("main", { name: "비서와 대화" }).textContent).not.toContain("실제 발송");
    const result = chatArea().getByText("저장 완료").closest(".result") as HTMLElement;
    expect(within(result).getByText("리뷰 답글 올림")).toBeInTheDocument();
    const today = screen.getByRole("list", { name: "오늘 한 일" });
    expect(within(today).getByText("리뷰 답글 올림")).toBeInTheDocument();
    // 카드가 사라지면 처음 말풍선은 다시 초안 인용까지 보인다.
    expect(chatArea().getByText("불편을 드려 죄송합니다. 간을 다시 맞추겠습니다.").closest("blockquote")).not.toBeNull();
  });
});

describe("사이드카 [손님 통계]", () => {
  test("요약 카드·유입 채널·키워드·해 볼 일을 보여 주고 일간으로 바꿀 수 있다", async () => {
    const { user, api } = await open();
    await user.click(screen.getByRole("tab", { name: "손님 통계" }));
    const panel = await screen.findByRole("tabpanel", { name: "손님 통계" });
    expect(await within(panel).findByText("플레이스 조회")).toBeInTheDocument();
    expect(within(panel).getByText("1,260")).toBeInTheDocument();
    expect(within(panel).getByText("지난주보다 60 늘었어요")).toBeInTheDocument();
    expect(within(panel).getByText("지난주보다 6 줄었어요")).toBeInTheDocument();
    expect(within(panel).getByText("비교할 기록이 없어요")).toBeInTheDocument();
    expect(within(panel).getByText("네이버 검색")).toBeInTheDocument();
    expect(within(panel).getByText("가장 붐비는 시간 오후 2시")).toBeInTheDocument();
    expect(within(panel).getByText("러시아 디저트").closest("li")).toHaveTextContent("새로 뜸");
    expect(within(panel).getByText("조회는 늘었지만 예약·주문으로 이어지는 비율은 줄었어요")).toBeInTheDocument();
    await user.click(within(panel).getByRole("radio", { name: "일간" }));
    expect(api.report).toHaveBeenLastCalledWith("s1", "day");
    expect(await within(panel).findByText("180")).toBeInTheDocument();
    expect(within(panel).getByText("어제보다 60 늘었어요")).toBeInTheDocument();
  });

  test("통계를 못 불러오면 쉬운 말로 알려 주고 다시 불러올 수 있다", async () => {
    const api = fakeApi();
    api.report.mockRejectedValueOnce(offline());
    const { user } = await open(api);
    await user.click(screen.getByRole("tab", { name: "손님 통계" }));
    const panel = await screen.findByRole("tabpanel", { name: "손님 통계" });
    expect(await within(panel).findByText("통계를 불러오지 못했어요.")).toBeInTheDocument();
    await user.click(within(panel).getByRole("button", { name: "다시 불러오기" }));
    expect(await within(panel).findByText("플레이스 조회")).toBeInTheDocument();
  });
});

describe("답글 대기함", () => {
  async function openInbox(api = fakeApi(), props: { reviewPollMs?: number; demo?: boolean } = {}) {
    api.reviews.mockResolvedValue(INBOX);
    const user = userEvent.setup();
    render(<App api={api} {...props} />);
    await screen.findByText(/무엇을 도와드릴까요/);
    await user.click(screen.getByRole("tab", { name: "리뷰" }));
    const panel = within(await screen.findByRole("tabpanel", { name: "리뷰" }));
    await panel.findByRole("heading", { name: "답글 기다리는 리뷰 3개" });
    return { user, api, panel };
  }
  const card = (panel: ReturnType<typeof within>, text: string) => within(panel.getByText(text).closest("li") as HTMLElement);
  /** 대화창에 쌓인 말풍선·결과·알림 수. */
  const chatItems = () => document.querySelectorAll(".messages-inner > .row, .messages-inner > .result, .messages-inner > .notice").length;

  test("기다리는 리뷰만 초안과 함께 보이고, 1~2점은 맨 위에 '꼭 읽어 보세요', 확인 필요는 이유 한 줄", async () => {
    const { user, panel } = await openInbox();
    expect(panel.getByRole("radio", { name: "기다림" })).toBeChecked();
    const items = panel.getAllByRole("listitem");
    expect(items).toHaveLength(3);
    expect(within(items[0]).getByText("케이크가 너무 달아요.")).toBeInTheDocument();
    expect(within(items[0]).getByText("꼭 읽어 보세요")).toBeInTheDocument();
    expect(within(items[0]).getByText("확인이 필요해요: 전화번호 등 개인정보")).toBeInTheDocument();
    expect(within(items[0]).getByRole("button", { name: "올리기" })).toBeDisabled();
    expect(within(items[1]).getByText("맛있게 드셔 주셔서 감사합니다.")).toBeInTheDocument();
    expect(within(items[1]).queryByText("꼭 읽어 보세요")).not.toBeInTheDocument();
    expect(within(items[1]).getByRole("button", { name: "올리기" })).toBeEnabled();
    // 초안이 있는 리뷰에는 [답글 초안 받기]가 없다.
    expect(panel.queryByRole("button", { name: "답글 초안 받기" })).not.toBeInTheDocument();
    expect(panel.queryByText("자리가 편해요.")).not.toBeInTheDocument();
    await user.click(panel.getByRole("radio", { name: "전체" }));
    expect(panel.getByText("자리가 편해요.")).toBeInTheDocument();
    // 왼쪽 '요즘 리뷰'에 기다리는 수 배지
    expect(screen.getByLabelText("답글 기다리는 리뷰 3개", { selector: ".quick-badge" })).toHaveTextContent("3");
  });

  test("[올리기]는 대화 턴 없이 그 카드만 바꾼다: 채팅 말풍선 수 그대로, 카드는 '답글 달림', 배지·오늘 한 일 갱신", async () => {
    const { user, api, panel } = await openInbox();
    const before = chatItems();
    const posted = inboxWith(
      "r20",
      { has_owner_reply: true, draft: { text: "맛있게 드셔 주셔서 감사합니다.", status: "posted", issues: [] } },
      { waiting: 2, unanswered: 2 },
    );
    api.replyToReview.mockResolvedValueOnce({ review: posted.reviews[0], summary: posted.summary });
    const reads = api.reviews.mock.calls.length;
    await user.click(card(panel, "메도빅 최고예요.").getByRole("button", { name: "올리기" }));
    expect(api.replyToReview).toHaveBeenLastCalledWith("s1", "r20", { op: "post" });
    expect(await panel.findByRole("heading", { name: "답글 기다리는 리뷰 2개" })).toBeInTheDocument();
    const done = card(panel, "메도빅 최고예요.");
    expect(done.getByText("답글 달림")).toBeInTheDocument();
    expect(done.getByRole("region", { name: "올린 답글" })).toHaveTextContent("맛있게 드셔 주셔서 감사합니다.");
    expect(done.queryByRole("button", { name: "올리기" })).not.toBeInTheDocument();
    // 대화는 그대로: 말풍선 없음, /actions 없음, 입력창 잠그지 않음, 리뷰 전체를 다시 읽지 않음
    expect(chatItems()).toBe(before);
    expect(api.act).not.toHaveBeenCalled();
    expect(api.reviews.mock.calls.length).toBe(reads);
    expect(screen.getByLabelText("답글 기다리는 리뷰 2개", { selector: ".quick-badge" })).toHaveTextContent("2");
    await user.click(screen.getByRole("tab", { name: "내 가게" }));
    const today = await screen.findByRole("list", { name: "오늘 한 일" });
    expect(await within(today).findByText("리뷰 답글 올림")).toBeInTheDocument();
  });

  test("카드 버튼을 누르는 동안 그 카드만 '하는 중'이고 대화 입력은 잠기지 않는다", async () => {
    const { user, api, panel } = await openInbox();
    let finish!: (r: Awaited<ReturnType<typeof api.replyToReview>>) => void;
    api.replyToReview.mockImplementationOnce(() => new Promise((resolve) => (finish = resolve)));
    await user.click(card(panel, "커피가 맛있어요.").getByRole("button", { name: "더 짧게" }));
    const c = card(panel, "커피가 맛있어요.");
    expect(c.getByRole("status")).toHaveTextContent("다시 쓰는 중…");
    expect(c.getByRole("button", { name: "올리기" })).toBeDisabled();
    expect(card(panel, "메도빅 최고예요.").getByRole("button", { name: "올리기" })).toBeEnabled();
    expect(chatArea().getByRole("textbox")).toBeEnabled();
    const redrafted = { ...INBOX.reviews[2], draft: { text: "또 오세요.", status: "ready" as const, issues: [] } };
    finish({ review: redrafted, summary: INBOX.summary });
    expect(await panel.findByText("또 오세요.")).toBeInTheDocument();
    expect(card(panel, "또 오세요.").queryByRole("status")).not.toBeInTheDocument();
  });

  test("고친 문장이 검사에 걸리면 카드 안에 이유, 채팅에는 화면 알림 하나와 [리뷰 탭에서 보기]", async () => {
    const { user, api, panel } = await openInbox();
    const before = chatItems();
    api.replyToReview.mockResolvedValueOnce({
      review: INBOX.reviews[2],
      summary: INBOX.summary,
      error: { reason: "checks", message: "검사에 걸렸어요(전화번호 등 개인정보)", issues: ["전화번호 등 개인정보"] },
    });
    const c = card(panel, "커피가 맛있어요.");
    await user.click(c.getByRole("button", { name: "고치기" }));
    const edit = c.getByRole("textbox", { name: "답글 고치기" });
    await user.clear(edit);
    await user.type(edit, "010-1234-5678로 연락 주세요");
    await user.click(c.getByRole("button", { name: "이대로 올리기" }));
    expect(api.replyToReview).toHaveBeenLastCalledWith("s1", "r18", { op: "post", text: "010-1234-5678로 연락 주세요" });
    expect(await c.findByRole("alert")).toHaveTextContent("확인이 필요해요: 전화번호 등 개인정보");
    expect(c.getByRole("textbox", { name: "답글 고치기" })).toHaveValue("010-1234-5678로 연락 주세요"); // 고친 문장은 그대로
    const notices = chatArea().getAllByText(/리뷰 답글을 올리지 못했어요/);
    expect(notices).toHaveLength(1);
    expect(notices[0]).toHaveTextContent("11/06 별점 5점 리뷰 답글을 올리지 못했어요: 전화번호 등 개인정보");
    expect(chatItems()).toBe(before + 1);
    expect(api.act).not.toHaveBeenCalled();
    expect(api.say).not.toHaveBeenCalled();
    // [리뷰 탭에서 보기]: 다른 탭에 있다가도 리뷰 탭을 열고 그 카드를 가리킨다
    await user.click(screen.getByRole("tab", { name: "내 가게" }));
    await user.click(chatArea().getByRole("button", { name: "리뷰 탭에서 보기" }));
    const reopened = within(await screen.findByRole("tabpanel", { name: "리뷰" }));
    await waitFor(() => expect(reopened.getByText("커피가 맛있어요.").closest("li")).toHaveAttribute("data-focused", "true"));
  });

  test("서버 오류면 카드에 이유, 채팅에 알림 하나. 이미 답글 달림 같은 것은 카드에만", async () => {
    const { user, api, panel } = await openInbox();
    const before = chatItems();
    api.replyToReview.mockRejectedValueOnce(new Error("500"));
    await user.click(card(panel, "메도빅 최고예요.").getByRole("button", { name: "건너뛰기" }));
    expect(await card(panel, "메도빅 최고예요.").findByRole("alert")).toHaveTextContent("서버 오류로 처리하지 못했어요");
    expect(chatArea().getAllByText(/리뷰 답글을 올리지 못했어요: 서버 오류로 처리하지 못했어요/)).toHaveLength(1);
    expect(chatItems()).toBe(before + 1);

    api.replyToReview.mockResolvedValueOnce({
      review: INBOX.reviews[2],
      summary: INBOX.summary,
      error: { reason: "busy", message: "아직 초안을 쓰는 중이에요.", issues: [] },
    });
    await user.click(card(panel, "커피가 맛있어요.").getByRole("button", { name: "올리기" }));
    expect(await card(panel, "커피가 맛있어요.").findByRole("alert")).toHaveTextContent("아직 초안을 쓰는 중이에요.");
    expect(chatItems()).toBe(before + 1);
  });

  test("[고치기]는 카드 안에서 고쳐 [이대로 올리기]로 고친 문장을 보내고, [취소]는 초안으로 돌아간다", async () => {
    const { user, api, panel } = await openInbox();
    const c = card(panel, "커피가 맛있어요.");
    await user.click(c.getByRole("button", { name: "고치기" }));
    expect(c.getByRole("textbox", { name: "답글 고치기" })).toHaveValue("다음에도 찾아 주세요.");
    await user.click(c.getByRole("button", { name: "취소" }));
    expect(c.queryByRole("textbox", { name: "답글 고치기" })).not.toBeInTheDocument();
    expect(c.getByText("다음에도 찾아 주세요.")).toBeInTheDocument();

    await user.click(c.getByRole("button", { name: "고치기" }));
    const edit = c.getByRole("textbox", { name: "답글 고치기" });
    await user.clear(edit);
    await user.type(edit, "  커피 칭찬 고맙습니다!  ");
    const before = chatItems();
    await user.click(c.getByRole("button", { name: "이대로 올리기" }));
    expect(api.replyToReview).toHaveBeenLastCalledWith("s1", "r18", { op: "post", text: "커피 칭찬 고맙습니다!" });
    await waitFor(() => expect(c.queryByRole("textbox", { name: "답글 고치기" })).not.toBeInTheDocument());
    expect(chatItems()).toBe(before);
    expect(api.act).not.toHaveBeenCalled();
  });

  test("[건너뛰기]와 다시 쓰기 칩은 그 리뷰와 고정된 hint만 보내고 채팅에는 아무것도 남기지 않는다", async () => {
    const { user, api, panel } = await openInbox();
    const before = chatItems();
    const c = () => card(panel, "커피가 맛있어요.");
    const sent: Array<[string, string]> = [
      ["더 짧게", "shorter"],
      ["더 정중하게", "more_apologetic"],
      ["더 따뜻하게", "warmer"],
    ];
    for (const [label, hint] of sent) {
      const chip = within(c().getByRole("group", { name: "다시 쓰기" })).getByRole("button", { name: label });
      await waitFor(() => expect(chip).toBeEnabled());
      await user.click(chip);
      await waitFor(() => expect(api.replyToReview).toHaveBeenLastCalledWith("s1", "r18", { op: "redraft", hint }));
    }
    const skipped = { ...INBOX.reviews[2], draft: { ...INBOX.reviews[2].draft!, status: "skipped" as const } };
    api.replyToReview.mockResolvedValueOnce({ review: skipped, summary: { ...INBOX.summary, waiting: 2 } });
    await waitFor(() => expect(c().getByRole("button", { name: "건너뛰기" })).toBeEnabled());
    await user.click(c().getByRole("button", { name: "건너뛰기" }));
    expect(api.replyToReview).toHaveBeenLastCalledWith("s1", "r18", { op: "skip" });
    await waitFor(() => expect(panel.queryByText("커피가 맛있어요.")).not.toBeInTheDocument()); // [기다림]에서 빠진다
    expect(chatItems()).toBe(before);
    expect(api.act).not.toHaveBeenCalled();
  });

  test("초안을 쓰는 중이면 'M개 쓰는 중'을 보이고 몇 초마다 다시 읽다가, 다 쓰면 멈춘다", async () => {
    const api = fakeApi();
    const writing: ReviewsView = {
      summary: { ...INBOX.summary, waiting: 0, preparing: 2 },
      reviews: INBOX.reviews.map((r) => (r.has_owner_reply ? r : { ...r, draft: null })),
    };
    api.reviews.mockResolvedValueOnce(writing).mockResolvedValueOnce(writing).mockResolvedValue(INBOX);
    const user = userEvent.setup();
    render(<App api={api} reviewPollMs={30} />);
    await user.click(await screen.findByRole("tab", { name: "리뷰" }));
    const panel = within(await screen.findByRole("tabpanel", { name: "리뷰" }));
    expect(await panel.findByText("초안 쓰는 중 2개")).toBeInTheDocument();
    expect(panel.getAllByText("초안 쓰는 중…").length).toBeGreaterThan(0);
    expect(panel.queryByRole("button", { name: "답글 초안 받기" })).not.toBeInTheDocument();
    expect(await panel.findByRole("heading", { name: "답글 기다리는 리뷰 3개" }, { timeout: 2000 })).toBeInTheDocument();
    expect(panel.queryByText("초안 쓰는 중 2개")).not.toBeInTheDocument();
    const calls = api.reviews.mock.calls.length;
    await new Promise((r) => setTimeout(r, 150));
    expect(api.reviews.mock.calls.length).toBe(calls);
  });

  test("[새 리뷰 도착 (데모)]는 개발 모드에서만 보이고 누르면 데모 리뷰를 더한 뒤 다시 읽는다", async () => {
    const { user, api, panel } = await openInbox(fakeApi(), { demo: true });
    const before = api.reviews.mock.calls.length;
    await user.click(panel.getByRole("button", { name: "새 리뷰 도착 (데모)" }));
    expect(api.simulateReview).toHaveBeenCalledWith("s1");
    await waitFor(() => expect(api.reviews.mock.calls.length).toBeGreaterThan(before));
    cleanup();
    localStorage.clear();
    const hidden = await openInbox(fakeApi(), { demo: false });
    expect(hidden.panel.queryByRole("button", { name: "새 리뷰 도착 (데모)" })).not.toBeInTheDocument();
  });
});
