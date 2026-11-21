import { WELCOME_MESSAGES } from "./welcomeMessages";
import { act, cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, test } from "vitest";
import { readFileSync } from "node:fs";
import App from "./App";
import { TOOLBOX } from "./components/LeftNav";
import type { Pending, Place, ReviewsView, Turn } from "./types";
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
  await screen.findByRole("heading", { level: 1 });
  return { user, api };
}

async function say(user: ReturnType<typeof userEvent.setup>, text: string) {
  await user.type(screen.getByRole("textbox", { name: "비서에게 말하기" }), text);
  await user.click(screen.getByRole("button", { name: "보내기" }));
}

describe("대화", () => {
  test("프로필은 포커스·빈 입력·공백에는 idle, 실제 입력에는 listening이며 지우면 돌아온다", async () => {
    const { user } = await open();
    const profile = screen.getByRole("img", { name: "비서 프로필" });
    const input = screen.getByRole("textbox", { name: "비서에게 말하기" });
    await user.click(input);
    expect(profile).toHaveAttribute("data-state", "idle");
    await user.type(input, "   ");
    expect(profile).toHaveAttribute("data-state", "idle");
    await user.type(input, "메뉴 알려 줘");
    expect(profile).toHaveAttribute("data-state", "listening");
    await user.clear(input);
    expect(profile).toHaveAttribute("data-state", "idle");
  });

  test("프로필은 busy에서 speaking, 보이는 승인 카드에서 attention이며 입력보다 승인을 우선한다", async () => {
    const api = fakeApi();
    let finish!: (turn: Turn) => void;
    api.say.mockImplementation(() => new Promise<Turn>((resolve) => { finish = resolve; }));
    const { user } = await open(api);
    const profile = screen.getByRole("img", { name: "비서 프로필" });
    await say(user, "메뉴 바꿔 줘");
    expect(profile).toHaveAttribute("data-state", "speaking");
    expect(screen.getByRole("textbox", { name: "비서에게 말하기" })).toHaveValue("");
    await act(async () => finish({
      reply: "확인해 주세요", place: SEED,
      pending: [photoPending([draft({ price: 8500 })], withCake)],
    }));
    expect(screen.getByRole("region", { name: "저장 전 확인" })).toBeInTheDocument();
    expect(profile).toHaveAttribute("data-state", "attention");
    await user.type(screen.getByRole("textbox", { name: "비서에게 말하기" }), "고칠게요");
    expect(profile).toHaveAttribute("data-state", "attention");
    await user.click(screen.getByRole("button", { name: "보내기" }));
    expect(screen.queryByRole("region", { name: "저장 전 확인" })).not.toBeInTheDocument();
    expect(profile).toHaveAttribute("data-state", "speaking");
    await act(async () => finish({ reply: "취소했어요", place: SEED, pending: [] }));
    expect(profile).toHaveAttribute("data-state", "idle");
    for (const avatar of screen.getByRole("main").querySelectorAll(".avatar")) {
      expect(avatar).toHaveAttribute("src", "/agent.png");
    }
  });

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

  test("말로 보내는 맥락 칩은 그 말을 대화 한 턴으로 보낸다", async () => {
    const api = fakeApi([
      {
        reply: "리뷰 3건을 살펴봤어요.",
        place: SEED,
        pending: [],
        next_actions: [
          { action_id: "chat.say", label: "매일 리뷰 요약 받기", args: { text: "매일 리뷰 요약해서 보내 줘" } },
          { action_id: "stats.week", label: "이번 주 통계 보기", args: {} },
        ],
      },
      { reply: "리뷰 보고를 매일 받을까요?", place: SEED, pending: [] },
    ]);
    const { user } = await open(api);
    await user.click(screen.getByRole("button", { name: "요즘 리뷰 어때?" }));
    await user.click(await screen.findByRole("button", { name: "매일 리뷰 요약 받기" }));
    expect(api.say).toHaveBeenCalledWith("s1", "매일 리뷰 요약해서 보내 줘");
    expect(api.act).toHaveBeenCalledTimes(1);
    expect(await chatArea().findByText("매일 리뷰 요약해서 보내 줘", { selector: "p" })).toBeInTheDocument();
    expect(await chatArea().findByText("리뷰 보고를 매일 받을까요?")).toBeInTheDocument();
  });

  test("기능 버튼이 있는 맥락 칩은 그 액션을 보낸다", async () => {
    const api = fakeApi([
      { reply: "조회 1,260회예요.", place: SEED, pending: [], next_actions: [{ action_id: "review.summary", label: "리뷰 요약 보기", args: {} }] },
      { reply: "리뷰 3건을 살펴봤어요.", place: SEED, pending: [] },
    ]);
    const { user } = await open(api);
    await user.click(screen.getByRole("button", { name: "이번 주 손님" }));
    await user.click(await screen.findByRole("button", { name: "리뷰 요약 보기" }));
    expect(api.act).toHaveBeenLastCalledWith("s1", { action_id: "review.summary", label: "리뷰 요약 보기", args: {} });
    expect(api.say).not.toHaveBeenCalled();
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
  test("메뉴판을 올리면 사진 없는 메뉴에 새로고침 없이 대표 그림이 뜨고, 이름이 채워지면 그림도 바뀐다", async () => {
    const row = { id: "m09", name: "Блюдо дня", price: 6000, description: null, photo_url: null, ai: [], filling: ["name" as const, "description" as const] };
    const reading: Place = { ...SEED, menu: [{ ...row, illustration_url: "/illustrations/default_other.svg" }] };
    const filled: Place = {
      ...SEED,
      menu: [{ ...row, name: "오늘의 수프", description: "날마다 바뀌는 수프.", illustration_url: "/illustrations/default_soup.svg", ai: ["name", "description"], filling: [] }],
    };
    const api = fakeApi([{ reply: "메뉴 1개를 메뉴 탭에 올렸어요.", place: reading, pending: [] }], { place: filled, pending: [] });
    const { user } = await open(api);
    await user.upload(screen.getByLabelText("사진 올리기"), CAKE);
    await user.click(screen.getByRole("button", { name: "보내기" }));
    const menu = within(await screen.findByRole("tabpanel", { name: "메뉴" }));
    expect(await menu.findByRole("img", { name: "대표 그림" })).toHaveAttribute("src", "/illustrations/default_other.svg");
    expect(api.view).not.toHaveBeenCalled(); // 올린 답이 가져온 가게 화면 그대로
    await waitFor(() => expect(menu.getByRole("img", { name: "대표 그림" })).toHaveAttribute("src", "/illustrations/default_soup.svg"), { timeout: 3000 });
    expect(menu.getByText("오늘의 수프")).toBeInTheDocument();
  });

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
    expect(within(card).getByText("이름 확인 필요")).toBeInTheDocument();
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

  test("저장을 누르면 '네'를 보내고 끝난 카드와 저장 완료 줄이 남는다", async () => {
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
    expect(chatArea().getByText("새 메뉴 밀푀유 케이크 8,500원")).toBeInTheDocument();
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

describe("새 대화 첫 화면", () => {
  test("응원 문구 한 줄만 보이고 비서 이미지는 없다", async () => {
    await open();
    const heading = screen.getByRole("heading", { level: 1 });
    expect(WELCOME_MESSAGES).toContain(heading.textContent);
    expect(screen.getByRole("textbox", { name: "비서에게 말하기" })).toHaveAttribute("placeholder", expect.stringContaining("말로 하면 제가 대신 해 드려요"));
    expect(screen.getByRole("button", { name: "요즘 리뷰 어때?" })).toBeInTheDocument();
    expect(document.querySelector(".welcome-face")).toBeNull();
  });
});

describe("네이버 플레이스 불러오기 (시연 컨셉)", () => {
  test("불러오기 전에는 홈이 비어 있고, 승인하면 홈이 보이며 메뉴는 그대로다", async () => {
    localStorage.removeItem("agenthan.naverPlace.v1");
    const { user } = await open(fakeApi([]));
    const home = screen.getByRole("tabpanel", { name: "홈" });
    expect(within(home).getByText("아직 불러온 가게 정보가 없어요")).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: /네이버 플레이스 불러오기/ }));
    await user.click(await screen.findByRole("button", { name: "승인" }, { timeout: 4000 }));
    expect(within(screen.getByRole("tabpanel", { name: "홈" })).queryByText("아직 불러온 가게 정보가 없어요")).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /네이버 플레이스 불러오기/ })).not.toBeInTheDocument();
    expect(localStorage.getItem("agenthan.naverPlace.v1")).toBe("true");
  }, 8000);
});

describe("사이드카 [홈]·[메뉴]", () => {
  test("홈 구역의 [수정]은 대화에서 그 일을 시작한다(쿠폰은 입력 블록, 나머지는 말)", async () => {
    const seeded = { ...SEED, keywords: ["이태원 케이크"] };
    const api = fakeApi([{ reply: "언제로 바꿀까요?", place: seeded, pending: [] }], { place: seeded, pending: [] });
    const { user } = await open(api);
    const home = screen.getByRole("tabpanel", { name: "홈" });
    await user.click(within(home).getByRole("button", { name: "쿠폰 수정" }));
    expect(chatArea().getByRole("form", { name: "쿠폰 만들기" })).toBeInTheDocument();
    expect(api.act).not.toHaveBeenCalled();
    await user.click(within(home).getByRole("button", { name: "영업시간 수정" }));
    expect(api.say).toHaveBeenCalledWith("s1", "영업시간이나 휴무를 바꾸고 싶어");
    expect(await screen.findByText("언제로 바꿀까요?")).toBeInTheDocument();
    await user.click(within(home).getByRole("button", { name: "대표키워드 수정" }));
    expect(api.say).toHaveBeenLastCalledWith("s1", "대표키워드를 바꾸고 싶어");
    await user.click(within(home).getByRole("button", { name: "루틴 설정 수정" }));
    expect(api.say).toHaveBeenLastCalledWith("s1", "루틴 설정을 바꾸고 싶어");
  });

  test("홈에 영업시간·쿠폰, 메뉴 탭에 메뉴가 보이고 저장 전 변경은 강조된다", async () => {
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
    const home = screen.getByRole("tabpanel", { name: "홈" });
    expect(within(home).getByText("일요일")).toBeInTheDocument();
    expect(within(home).getByText("쉬는 날")).toBeInTheDocument();
    await say(user, "쿠폰 만들어 줘");
    const added = await within(home).findByText("오픈 기념");
    expect(added.closest("[data-mark]")).toHaveAttribute("data-mark", "added");
    const card = screen.getByRole("region", { name: "저장 전 확인" });
    expect(within(card).getByText("새 쿠폰: 오픈 기념")).toBeInTheDocument();
    expect(within(card).getByText("할인: 10%")).toBeInTheDocument();
    expect(within(card).getByText("기간: 11/20 ~ 11/30")).toBeInTheDocument();
    await user.click(screen.getByRole("tab", { name: "메뉴" }));
    const menu = screen.getByRole("tabpanel", { name: "메뉴" });
    expect(within(menu).getByText("메도빅")).toBeInTheDocument();
    expect(within(menu).getByText("7,500원")).toBeInTheDocument();
  });

  test("메뉴 가격이 바뀔 예정이면 배지와 함께 바뀌기 전 가격을 지워 보인다", async () => {
    const after = { ...SEED, menu: SEED.menu.map((m) => (m.name === "메도빅" ? { ...m, price: 8000 } : m)) };
    const id = SEED.menu.find((m) => m.name === "메도빅")!.id;
    const pending = { task_id: "t3", feature: "menu", after, changes: { menu: { added: [], updated: [id], removed: [] } } };
    const api = fakeApi([{ reply: "가격을 바꿀까요?", place: SEED, pending: [pending] }]);
    const { user } = await open(api);
    await say(user, "메도빅 8000원으로");
    await user.click(screen.getByRole("tab", { name: "메뉴" }));
    const row = within(screen.getByRole("tabpanel", { name: "메뉴" })).getByText("메도빅").closest("[data-mark]") as HTMLElement;
    expect(row).toHaveAttribute("data-mark", "updated");
    expect(within(row).getByText("바뀔 예정")).toBeInTheDocument();
    expect(within(row).getByText("7,500원").tagName).toBe("S");
    expect(within(row).getByText("8,000원")).toBeInTheDocument();
  });

  test("메뉴 탭 [순서 바꾸기]는 대화 턴 없이 그 순서를 바로 저장하고 새 순서로 그린다", async () => {
    const { user, api } = await open();
    await user.click(screen.getByRole("tab", { name: "메뉴" }));
    const menu = within(screen.getByRole("tabpanel", { name: "메뉴" }));
    await user.click(menu.getByRole("button", { name: "순서 바꾸기" }));
    await user.click(menu.getByRole("button", { name: "나폴레옹 위로" }));
    await user.click(menu.getByRole("button", { name: "순서 저장" }));
    expect(api.reorderMenus).toHaveBeenCalledWith(expect.any(String), ["m02", "m01"]);
    expect(api.say).not.toHaveBeenCalled();
    await waitFor(() => expect(menu.getAllByRole("listitem")[0]).toHaveTextContent("나폴레옹"));
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
    expect(screen.getByRole("tab", { name: "통계" })).toHaveAttribute("aria-selected", "true");
  });

  test("메뉴 사진 올리기는 사진 고르기를 열고 [메뉴]를 보여 준다", async () => {
    const { user } = await open();
    await user.click(screen.getByRole("tab", { name: "리뷰" }));
    const input = screen.getByLabelText<HTMLInputElement>("사진 올리기");
    let opened = false;
    input.addEventListener("click", () => (opened = true));
    await user.click(screen.getByRole("button", { name: "메뉴 사진 올리기" }));
    expect(opened).toBe(true);
    expect(screen.getByRole("tab", { name: "메뉴" })).toHaveAttribute("aria-selected", "true");
  });
});

describe("왼쪽 AgentHan 도구함", () => {
  test("비서 기능 카탈로그의 모든 기능이 같은 이름·단계로 들어 있다", () => {
    const url = `${process.cwd()}/../src/agenthan/agent/place_features.json`; // web/에서 돈다
    const catalog: Array<{ id: string; name_ko: string; stage: string }> = JSON.parse(readFileSync(url, "utf-8"));
    const pick = (rows: Array<{ id: string; name: string; stage: string }>) => rows.map((r) => JSON.stringify(r)).sort();
    expect(pick(TOOLBOX.map((t) => ({ id: t.key, name: t.label, stage: t.stage })))).toEqual(
      pick(catalog.map((f) => ({ id: f.id, name: f.name_ko, stage: f.stage }))),
    );
  });

  test("맨 아래 도구함을 열어 누르면 버튼 없는 기능은 예시 문장을, 버튼 있는 기능은 그 액션을 보내고 패널을 닫는다", async () => {
    const { user, api } = await open();
    const toggle = screen.getByRole("button", { name: "AgentHan 도구함" });
    await user.click(toggle);
    expect(toggle).toHaveAttribute("aria-expanded", "true");
    const panel = screen.getByRole("dialog", { name: "AgentHan 도구함" });
    await user.click(within(panel).getByRole("button", { name: "톡톡 문의 답장" }));
    await waitFor(() => expect(api.say).toHaveBeenLastCalledWith("s1", "톡톡 문의 온 거 보여 줘"));
    expect(screen.queryByRole("dialog", { name: "AgentHan 도구함" })).not.toBeInTheDocument();
    await screen.findByText("알겠어요.");
    await user.click(toggle);
    await user.click(within(screen.getByRole("dialog")).getByRole("button", { name: "방문·유입 통계 조회" }));
    await waitFor(() => expect(api.act).toHaveBeenLastCalledWith("s1", { action_id: "stats.week", args: {}, label: "방문·유입 통계 조회" }));
    expect(screen.getByRole("tab", { name: "통계" })).toHaveAttribute("aria-selected", "true");
  });

  test("도구함 패널은 Esc로 닫힌다", async () => {
    const { user } = await open();
    await user.click(screen.getByRole("button", { name: "AgentHan 도구함" }));
    expect(screen.getByRole("dialog", { name: "AgentHan 도구함" })).toBeInTheDocument();
    await user.keyboard("{Escape}");
    expect(screen.queryByRole("dialog", { name: "AgentHan 도구함" })).not.toBeInTheDocument();
  });

  test("전화 주문은 자주 쓰는 일 맨 위에 있고(서랍은 orders/OrderDrawer.test), 손님으로 전화 걸기는 없다", async () => {
    await open();
    const first = screen.getAllByRole("list")[0].querySelector("li");
    expect(first).toHaveTextContent("전화 주문");
    expect(screen.queryByText("손님으로 전화 걸기")).not.toBeInTheDocument();
    expect(screen.queryByRole("heading", { name: "전화 주문" })).not.toBeInTheDocument();
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
    await user.click(screen.getByRole("tab", { name: "통계" }));
    expect(await screen.findByRole("tabpanel", { name: "통계" })).toBeInTheDocument();
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

  test("답글 초안 받기는 대화 턴 없이 그 카드에 초안을 띄운다", async () => {
    const { user, api } = await open();
    await user.click(screen.getByRole("tab", { name: "리뷰" }));
    const panel = await screen.findByRole("tabpanel", { name: "리뷰" });
    const log = screen.getByRole("main", { name: "비서와 대화" });
    const before = log.innerHTML;
    const drafted = { ...REVIEWS.reviews[0], draft: { text: "불편 드려 죄송합니다.", status: "ready" as const, issues: [] } };
    api.replyToReview.mockResolvedValueOnce({ review: drafted, summary: { ...REVIEWS.summary, waiting: 1 } });
    const [first] = await within(panel).findAllByRole("button", { name: "답글 초안 받기" });
    await user.click(first);
    expect(api.replyToReview).toHaveBeenLastCalledWith("s1", "r04", { op: "draft" });
    expect(await within(panel).findByRole("textbox", { name: "답글 고치기" })).toHaveValue("불편 드려 죄송합니다.");
    expect(api.act).not.toHaveBeenCalled();
    expect(api.say).not.toHaveBeenCalled();
    expect(log.innerHTML).toBe(before); // 대화 화면에는 아무것도 더하지 않는다
  });

  test("답글 초안을 못 받아도 채팅에는 알리지 않고 카드에만 보인다", async () => {
    const { user, api } = await open();
    await user.click(screen.getByRole("tab", { name: "리뷰" }));
    const panel = await screen.findByRole("tabpanel", { name: "리뷰" });
    const log = screen.getByRole("main", { name: "비서와 대화" });
    const before = log.innerHTML;
    api.replyToReview.mockRejectedValueOnce(new Error("500"));
    const [first] = await within(panel).findAllByRole("button", { name: "답글 초안 받기" });
    await user.click(first);
    expect(await within(panel).findByRole("alert")).toHaveTextContent("서버 오류로 처리하지 못했어요");
    expect(log.innerHTML).toBe(before);
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

  test("최소 주문 금액은 선택이고, 넣으면 미리보기 줄에 조건이 보이고 coupon.create에 같이 간다", async () => {
    const pending = { task_id: "t9", version: 1, feature: "coupon", after: SEED, changes: {} };
    const { user, api, form } = await openCoupon(fakeApi([{ reply: "이 쿠폰을 만들까요?", place: SEED, pending: [pending] }]));
    await user.type(form.getByLabelText("쿠폰 이름"), "전화 주문 할인");
    await user.click(form.getByRole("radio", { name: "원" }));
    await user.type(form.getByLabelText("할인 값"), "1000");
    setDate(form.getByLabelText("시작일"), "2026-12-01");
    setDate(form.getByLabelText("종료일"), "2026-12-15");
    expect(form.getByLabelText("최소 주문 금액")).toHaveValue(null);
    expect(form.getByText("전화 주문 할인 · 1,000원 할인 · 12/01 ~ 12/15")).toBeInTheDocument();
    await user.type(form.getByLabelText("최소 주문 금액"), "10000");
    expect(form.getByText("전화 주문 할인 · 1,000원 할인 · 12/01 ~ 12/15 · 10,000원 이상 주문 시")).toBeInTheDocument();
    await user.click(form.getByRole("button", { name: "미리 보기" }));
    expect(api.act).toHaveBeenCalledWith("s1", {
      action_id: "coupon.create",
      label: "쿠폰 만들기: 전화 주문 할인",
      args: { title: "전화 주문 할인", discount_type: "amount", discount_value: 1000, start_date: "2026-12-01", end_date: "2026-12-15", min_order: 10000 },
    });
  });

  test("최소 주문 금액이 음수나 정수가 아니면 [미리 보기]가 막히고, 0이면 조건 없는 쿠폰이다", async () => {
    const { user, form } = await openCoupon();
    await user.type(form.getByLabelText("쿠폰 이름"), "오픈 기념");
    await user.type(form.getByLabelText("할인 값"), "10");
    const submit = form.getByRole("button", { name: "미리 보기" });
    expect(submit).toBeEnabled();
    for (const bad of ["-1000", "1000.5"]) {
      await user.clear(form.getByLabelText("최소 주문 금액"));
      await user.type(form.getByLabelText("최소 주문 금액"), bad);
      expect(submit).toBeDisabled();
      expect(form.getByText("최소 주문 금액은 0 이상의 정수(원)로 적거나 비워 주세요.")).toBeInTheDocument();
    }
    await user.clear(form.getByLabelText("최소 주문 금액"));
    await user.type(form.getByLabelText("최소 주문 금액"), "0");
    expect(submit).toBeEnabled();
    expect(form.queryByText(/이상 주문 시/)).not.toBeInTheDocument();
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

  test("[답글 올리기]는 task.approve를 보내고, 더미 안내 없이 '리뷰 답글 올림'을 결과에 남긴다", async () => {
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
    // 카드가 사라지면 처음 말풍선은 다시 초안 인용까지 보인다.
    expect(chatArea().getByText("불편을 드려 죄송합니다. 간을 다시 맞추겠습니다.").closest("blockquote")).not.toBeNull();
  });
});

describe("사이드카 [통계]", () => {
  test("요약 카드·유입 채널·키워드·해 볼 일을 보여 주고 일간으로 바꿀 수 있다", async () => {
    const { user, api } = await open();
    await user.click(screen.getByRole("tab", { name: "통계" }));
    const panel = await screen.findByRole("tabpanel", { name: "통계" });
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
    await user.click(screen.getByRole("tab", { name: "통계" }));
    const panel = await screen.findByRole("tabpanel", { name: "통계" });
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
    await screen.findByRole("heading", { level: 1 });
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

  test("[올리기]는 대화 턴 없이 그 카드만 바꾼다: 채팅 말풍선 수 그대로, 카드는 '답글 달림', 배지 갱신", async () => {
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
    const edit = c.getByRole("textbox", { name: "답글 고치기" });
    await user.clear(edit);
    await user.type(edit, "010-1234-5678로 연락 주세요");
    await user.click(c.getByRole("button", { name: "올리기" }));
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
    await user.click(screen.getByRole("tab", { name: "홈" }));
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

  test("초안은 카드 입력칸에서 바로 고치고 [올리기]로 고친 문장을 보낸다", async () => {
    const { user, api, panel } = await openInbox();
    const c = card(panel, "커피가 맛있어요.");
    const edit = c.getByRole("textbox", { name: "답글 고치기" });
    expect(edit).toHaveValue("다음에도 찾아 주세요.");
    await user.clear(edit);
    await user.type(edit, "  커피 칭찬 고맙습니다!  ");
    const before = chatItems();
    const posted = inboxWith("r18", { has_owner_reply: true, draft: { text: "커피 칭찬 고맙습니다!", status: "posted", issues: [] } }, { waiting: 2, unanswered: 2 });
    api.replyToReview.mockResolvedValueOnce({ review: posted.reviews.find((r) => r.id === "r18")!, summary: posted.summary });
    await user.click(c.getByRole("button", { name: "올리기" }));
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
    localStorage.setItem("agenthan.naverPlace.v1", "true");
    const hidden = await openInbox(fakeApi(), { demo: false });
    expect(hidden.panel.queryByRole("button", { name: "새 리뷰 도착 (데모)" })).not.toBeInTheDocument();
  });

  test("새 리뷰가 안 좋으면 비서 알림이 바로 대화에 온다 (나쁜 리뷰 바로 알림)", async () => {
    const { user, api, panel } = await openInbox(fakeApi(), { demo: true });
    const ALERT = {
      id: "alert-1",
      role: "agent" as const,
      text: "안 좋은 리뷰가 방금 들어왔어요 (★2)\n“포장이 찌그러져서 왔어요”\n답글로 바로 마음을 전해 보세요.",
      next_actions: [{ action_id: "review.draft_reply", label: "답글 초안 받기", args: { review_id: "sim-01" } }],
    };
    api.view.mockResolvedValueOnce({ place: SEED, pending: [], messages: [ALERT] });
    await user.click(panel.getByRole("button", { name: "새 리뷰 도착 (데모)" }));
    expect(await screen.findByText(/안 좋은 리뷰가 방금 들어왔어요/)).toBeInTheDocument();
  });
});

describe("홈 루틴 설정 (#33)", () => {
  const ROUTINES = { drafts: false, report: { enabled: false, frequency: null, weekday: "mon", time: "09:00" } };
  const DAILY = { enabled: true, schedule: "매일 09:00", next_run: "2026-11-22T09:00", text: "매일 09:00 리뷰 요약 받는 중" };

  test("스위치는 대화 턴 없이 서버에 저장하고, 돌아온 설정을 그대로 보인다", async () => {
    const api = fakeApi();
    api.start.mockResolvedValueOnce({ session_id: "s1", place: SEED, pending: [], routines: ROUTINES } as never);
    api.setRoutines.mockResolvedValueOnce({
      place: SEED,
      pending: [],
      review_report: DAILY,
      routines: { ...ROUTINES, report: { ...ROUTINES.report, enabled: true, frequency: "daily" } },
    });
    const { user } = await open(api);
    const region = within(screen.getByRole("region", { name: "루틴 설정" }));
    await user.click(region.getByRole("switch", { name: "리뷰 정기 보고" }));
    expect(api.setRoutines).toHaveBeenCalledWith("s1", { report: { enabled: true } });
    expect(await region.findByText("매일 09:00에 리뷰 요약을 보내요")).toBeInTheDocument();
    expect(region.getByRole("radio", { name: "매일" })).toHaveAttribute("aria-checked", "true");
    expect(api.act).not.toHaveBeenCalled();
    expect(api.say).not.toHaveBeenCalled();
  });

  test("초안 미리 쓰기를 켜면 리뷰를 다시 읽어 쓰는 중을 보인다", async () => {
    const api = fakeApi();
    api.start.mockResolvedValueOnce({ session_id: "s1", place: SEED, pending: [], routines: ROUTINES } as never);
    api.setRoutines.mockResolvedValueOnce({ place: SEED, pending: [], routines: { ...ROUTINES, drafts: true } });
    const { user } = await open(api);
    const reads = api.reviews.mock.calls.length;
    await user.click(within(screen.getByRole("region", { name: "루틴 설정" })).getByRole("switch", { name: "답글 초안 미리 쓰기" }));
    expect(api.setRoutines).toHaveBeenCalledWith("s1", { drafts: true });
    await waitFor(() => expect(api.reviews.mock.calls.length).toBeGreaterThan(reads));
    expect(screen.getByRole("switch", { name: "답글 초안 미리 쓰기" })).toHaveAttribute("aria-checked", "true");
  });
});

describe("리뷰 정기 보고 (#98)", () => {
  const ON = { enabled: true, schedule: "매일 09:00", next_run: "2026-11-08T09:00", text: "매일 09:00 리뷰 요약 받는 중" };
  const REPORT = {
    id: "report-1",
    role: "agent" as const,
    text: "리뷰 정기 보고 (2026-11-07 ~ 2026-11-08)\n리뷰 3건\n답글 안 단 불만 리뷰 1건이 있어요.",
    next_actions: [{ action_id: "review.draft_reply", label: "리뷰 답글 초안", args: { review_id: "r09" } }],
  };

  test("홈 루틴 설정에 보고 한 줄이 보이고 [지금 한 번 받아 보기]는 보고를 대화에 한 번 넣는다", async () => {
    const api = fakeApi([], { place: SEED, pending: [], review_report: ON });
    api.runReviewReport.mockResolvedValueOnce({ place: SEED, pending: [], review_report: { ...ON, enabled: false, schedule: null, next_run: null, text: "정기 보고 꺼짐" }, messages: [REPORT] });
    const user = userEvent.setup();
    render(<App api={api} reportPollMs={0} />);
    const panel = within(await screen.findByRole("region", { name: "루틴 설정" }));
    expect(await panel.findByText("정기 보고 꺼짐")).toBeInTheDocument();
    await user.click(panel.getByRole("button", { name: "지금 한 번 받아 보기" }));
    expect(api.runReviewReport).toHaveBeenCalledWith("s1");
    expect(await chatArea().findByText(/답글 안 단 불만 리뷰 1건/)).toBeInTheDocument();
    expect(chatArea().getByRole("button", { name: "리뷰 답글 초안" })).toBeInTheDocument();
    expect(api.act).not.toHaveBeenCalled();
  });

  test("켜진 대화는 세션 조회를 다시 읽다가 정해진 때의 보고를 말풍선 하나로 받는다", async () => {
    const api = fakeApi([], { place: SEED, pending: [], review_report: ON });
    api.view.mockResolvedValueOnce({ place: SEED, pending: [], review_report: ON, messages: [REPORT] });
    render(<App api={api} reportPollMs={20} />);
    expect(await chatArea().findByText(/리뷰 정기 보고/, {}, { timeout: 2000 })).toBeInTheDocument();
    await new Promise((r) => setTimeout(r, 100)); // 다시 읽어도 같은 보고는 오지 않는다(서버가 한 번만 준다)
    expect(chatArea().getAllByText(/리뷰 정기 보고/)).toHaveLength(1);
    expect(await screen.findByText("매일 09:00 리뷰 요약 받는 중")).toBeInTheDocument();
  });

  // #112: 보고 설정은 가게 정보가 아니다. 저장 전후 review_report가 바뀌면 결과 줄에 남긴다.
  const OFF = { enabled: false, schedule: null, next_run: null, text: "정기 보고 꺼짐" };
  const DAILY = { enabled: true, schedule: "매일 14:47", next_run: "2026-11-08T14:47", text: "매일 14:47 리뷰 요약 받는 중" };
  const SETTING = { task_id: "t9", version: 1, feature: "review", after: SEED, changes: {} };
  const CHIPS = [
    { action_id: "task.approve", label: "모두 저장", args: { task_id: "t9", version: 1 } },
    { action_id: "task.cancel", label: "모두 취소", args: { task_id: "t9" } },
  ];
  test.each([
    ["켜기", OFF, DAILY, "리뷰 정기 보고: 매일 14:47"],
    ["바꾸기", ON, DAILY, "리뷰 정기 보고: 매일 14:47"],
    ["끄기", ON, OFF, "리뷰 정기 보고 끔"],
  ])("보고 설정 %s를 [모두 저장]하면 결과 줄이 저장 완료다", async (_name, before, after, line) => {
    const api = fakeApi(
      [
        { reply: "리뷰 정기 보고를 바꿀까요?", next_actions: CHIPS, place: SEED, pending: [SETTING], review_report: before },
        { reply: "저장했어요.", place: SEED, pending: [], review_report: after },
      ],
      { place: SEED, pending: [], review_report: before },
    );
    const { user } = await open(api);
    await say(user, "매일 리뷰 요약해서 보내 줘");
    // 카드가 하나면 저장·취소는 카드에만 있다(말풍선 칩은 중복이라 숨김).
    const card = within(await screen.findByRole("region", { name: "저장 전 확인" }));
    await user.click(card.getByRole("button", { name: "저장" }));
    const result = (await chatArea().findByText("저장 완료")).closest(".result") as HTMLElement;
    expect(within(result).getByText(line)).toBeInTheDocument();
    expect(chatArea().queryByText(/저장 안 함/)).not.toBeInTheDocument();
  });

  test("[모두 취소]면 보고 설정이 그대로라 지금처럼 저장 안 함이다", async () => {
    const api = fakeApi(
      [
        { reply: "리뷰 정기 보고를 바꿀까요?", next_actions: CHIPS, place: SEED, pending: [SETTING], review_report: OFF },
        { reply: "취소했어요.", place: SEED, pending: [], review_report: OFF },
      ],
      { place: SEED, pending: [], review_report: OFF },
    );
    const { user } = await open(api);
    await say(user, "매일 리뷰 요약해서 보내 줘");
    const card = within(await screen.findByRole("region", { name: "저장 전 확인" }));
    await user.click(card.getByRole("button", { name: "취소" }));
    const result = (await chatArea().findByText(/저장 안 함/)).closest(".result") as HTMLElement;
    expect(within(result).queryByText(/리뷰 정기 보고/)).not.toBeInTheDocument();
  });
});

describe("대표키워드 변경", () => {
  const BEFORE = { ...SEED, keywords: ["러시안 베이커리", "이태원 케이크"] };
  const AFTER = { ...BEFORE, keywords: [...BEFORE.keywords, "메도빅"] };
  const KEYWORD_PENDING = {
    task_id: "t7",
    version: 1,
    feature: "search_keywords",
    after: AFTER,
    changes: { keywords: { added: ["메도빅"], updated: [], removed: [] } },
  };

  test("확인 카드에 추가되는 대표키워드가 보이고 저장 뒤 결과는 저장 완료다", async () => {
    const api = fakeApi([
      { reply: "‘메도빅’을 대표키워드에 추가할까요?", place: BEFORE, pending: [KEYWORD_PENDING] },
      { reply: "저장했어요.", place: AFTER, pending: [] },
    ]);
    const { user } = await open(api);
    await say(user, "메도빅 대표키워드에 추가해 줘");
    const card = await screen.findByRole("region", { name: "저장 전 확인" });
    expect(within(card).getByText("대표키워드 바꾸기")).toBeInTheDocument();
    expect(within(card).getByText("대표키워드 추가 메도빅")).toBeInTheDocument();
    expect(within(card).queryByText(/정보를 바꿔요/)).not.toBeInTheDocument();
    await user.click(within(card).getByRole("button", { name: "저장" }));
    expect(api.act).toHaveBeenLastCalledWith("s1", expect.objectContaining({ action_id: "task.approve" }));
    expect(await chatArea().findByText("저장했어요.")).toBeInTheDocument();
    expect(chatArea().getByText("저장 완료")).toBeInTheDocument();
    expect(chatArea().queryByText("저장 안 함")).not.toBeInTheDocument();
  });

  test("[홈]에 대표키워드가 보이고 추가 예정 키워드는 강조된다", async () => {
    const api = fakeApi([{ reply: "추가할까요?", place: BEFORE, pending: [KEYWORD_PENDING] }]);
    const { user } = await open(api);
    await say(user, "메도빅 대표키워드에 추가해 줘");
    const shop = screen.getByRole("tabpanel", { name: "홈" });
    const list = await within(shop).findByRole("list", { name: "대표키워드" });
    expect(within(list).getByText("러시안 베이커리")).toBeInTheDocument();
    expect(within(list).getByText("메도빅").closest("[data-mark]")).toHaveAttribute("data-mark", "added");
  });

  test("쿠폰 확인 카드는 이름과 따로 할인·기간을 보인다(이름에 할인 문구가 있어도)", async () => {
    const coupon = {
      id: "coupon-1",
      title: "평일 오후 메도빅 1,000원 할인",
      discount_type: "amount" as const,
      discount_value: 1000,
      start_date: "2026-11-10",
      end_date: "2026-11-30",
    };
    const pending = {
      task_id: "t8",
      version: 1,
      feature: "coupon",
      after: { ...SEED, coupon: [coupon] },
      changes: { coupon: { added: ["coupon-1"], updated: [], removed: [] } },
    };
    const api = fakeApi([{ reply: "만들까요?", place: SEED, pending: [pending] }]);
    const { user } = await open(api);
    await say(user, "쿠폰 만들어 줘");
    const card = await screen.findByRole("region", { name: "저장 전 확인" });
    expect(within(card).getByText("새 쿠폰: 평일 오후 메도빅 1,000원 할인")).toBeInTheDocument();
    expect(within(card).getByText("할인: 1,000원")).toBeInTheDocument();
    expect(within(card).getByText("기간: 11/10 ~ 11/30")).toBeInTheDocument();
  });

  test("최소 주문 금액이 있는 쿠폰은 확인 카드와 홈에 조건이 보이고, 없거나 0이면 조건 문구가 없다", async () => {
    const coupon = {
      id: "coupon-1",
      title: "전화 주문 1,000원 할인",
      discount_type: "amount" as const,
      discount_value: 1000,
      min_order: 10000,
      start_date: "2026-11-02",
      end_date: "2026-12-31",
    };
    const plain = { ...coupon, id: "coupon-2", title: "첫 방문", min_order: 0 };
    const old = { id: "coupon-0", title: "옛 쿠폰", discount_type: "percent" as const, discount_value: 5, start_date: "2026-11-01", end_date: "2026-11-30" };
    const pending = {
      task_id: "t10",
      version: 1,
      feature: "coupon",
      after: { ...SEED, coupon: [old, coupon, plain] },
      changes: { coupon: { added: ["coupon-1", "coupon-2"], updated: [], removed: [] } },
    };
    const api = fakeApi([{ reply: "만들까요?", place: { ...SEED, coupon: [old] }, pending: [pending] }]);
    const { user } = await open(api);
    await say(user, "쿠폰 만들어 줘");
    const card = await screen.findByRole("region", { name: "저장 전 확인" });
    expect(within(card).getByText("새 쿠폰: 전화 주문 1,000원 할인")).toBeInTheDocument();
    expect(within(card).getByText("새 쿠폰: 첫 방문")).toBeInTheDocument();
    // 조건 줄은 최소 주문 금액이 있는 쿠폰에만 붙는다(쿠폰 두 개 중 하나)
    expect(within(card).getAllByText("조건: 10,000원 이상 주문 시")).toHaveLength(1);
    const home = screen.getByRole("tabpanel", { name: "홈" });
    const row = (title: string) => within(home).getByText(title).closest("li") as HTMLElement;
    expect(within(row("전화 주문 1,000원 할인")).getByText("10,000원 이상 주문 시")).toBeInTheDocument();
    expect(within(row("첫 방문")).queryByText(/이상 주문 시/)).not.toBeInTheDocument();
    expect(within(row("옛 쿠폰")).queryByText(/이상 주문 시/)).not.toBeInTheDocument();
  });
});

describe("톡톡·주문·예약 같은 기록 변경", () => {
  const TALK_BEFORE = {
    id: "talk-3",
    label: { message: "주차 가능한가요?", status: "unanswered" },
    fields: { reply_text: "" },
  };
  const TALK_AFTER = {
    ...TALK_BEFORE,
    label: { ...TALK_BEFORE.label, status: "saved_not_sent" },
    fields: { reply_text: "근처 공영주차장을 이용해 주세요" },
  };
  const OTHER_TALK = { id: "talk-1", label: { message: "케이크 예약 되나요?", status: "unanswered" }, fields: { reply_text: "" } };
  const BEFORE = { ...SEED, talktalk: [OTHER_TALK, TALK_BEFORE] };
  const HOURS = { ...BEFORE.business_hours, sun: { closed: false, open_time: "10:00", close_time: "18:00" } };
  const TALK_PENDING = {
    task_id: "t2",
    version: 1,
    feature: "talktalk",
    after: { ...BEFORE, talktalk: [OTHER_TALK, TALK_AFTER] },
    changes: { talktalk: { added: [], updated: ["talk-3"], removed: [] } },
  };
  const HOURS_PENDING = {
    task_id: "t1",
    version: 1,
    feature: "business_hours",
    after: { ...BEFORE, business_hours: HOURS },
    changes: { business_hours: { added: [], updated: ["sun"], removed: [] } },
  };
  const SAVED = { ...BEFORE, business_hours: HOURS, talktalk: [OTHER_TALK, TALK_AFTER] };
  const BUNDLE_REPLY =
    '영업시간·휴무 변경: 아래처럼 바꿀까요? (더미 저장소, 실제 네이버 반영 없음)\n{"sun": {"closed": false}}\n네 / 취소 / 고칠 내용 말하기\n' +
    '톡톡 문의 답장: 아래처럼 바꿀까요? (더미 저장소, 실제 네이버 반영 없음)\n{"id": "talk-3"}\n네 / 취소 / 고칠 내용 말하기';
  const CHIPS = [
    { action_id: "task.approve", label: "모두 저장", args: { task_id: "t1", version: 1 } },
    { action_id: "task.cancel", label: "모두 취소", args: { task_id: "t1" } },
  ];

  test("톡톡 답장 카드에 문의 원문과 답장 문구가 보이고 저장 뒤 결과 줄에도 남는다", async () => {
    const api = fakeApi([
      { reply: "톡톡 문의 답장: 아래처럼 바꿀까요? (더미 저장소, 실제 네이버 반영 없음)\n{}\n네 / 취소", place: BEFORE, pending: [TALK_PENDING] },
      { reply: "저장했어요.", place: { ...BEFORE, talktalk: [OTHER_TALK, TALK_AFTER] }, pending: [] },
    ]);
    const { user } = await open(api);
    await say(user, "주차 톡톡에 답해 줘");
    const card = await screen.findByRole("region", { name: "저장 전 확인" });
    expect(within(card).getByText("톡톡 답장 바꾸기")).toBeInTheDocument();
    expect(within(card).getByText("톡톡 문의 · 주차 가능한가요?")).toBeInTheDocument();
    expect(within(card).getByText("답장: 근처 공영주차장을 이용해 주세요")).toBeInTheDocument();
    expect(within(card).getByText("상태: 답장 전 → 답장 저장(아직 안 보냄)")).toBeInTheDocument();
    expect(within(card).queryByText(/케이크 예약/)).not.toBeInTheDocument();
    expect(within(card).queryByText(/정보를 바꿔요/)).not.toBeInTheDocument();
    // 말풍선은 질문만: 변경 줄은 카드에만 있다(중복 없음).
    const bubble = chatArea().getByText("톡톡 문의 답장: 아래처럼 바꿀까요?").closest(".bubble")!;
    expect(within(bubble as HTMLElement).queryByText("답장: 근처 공영주차장을 이용해 주세요")).not.toBeInTheDocument();
    // 기록은 [홈] 탭을 깨뜨리지 않는다.
    expect(screen.getByRole("tabpanel", { name: "홈" })).toBeInTheDocument();
    await user.click(within(card).getByRole("button", { name: "저장" }));
    const result = (await chatArea().findByText("저장 완료")).closest(".result")!;
    expect(within(result as HTMLElement).getByText("톡톡 문의 · 주차 가능한가요?")).toBeInTheDocument();
    expect(within(result as HTMLElement).getByText("답장: 근처 공영주차장을 이용해 주세요")).toBeInTheDocument();
  });

  test("주문 상태 카드는 주문 구분과 바뀌는 상태를 보인다", async () => {
    const order = { id: "order-1", label: { items: "메도빅 2조각", type: "pickup", status: "new" }, fields: {} };
    const changed = { ...order, label: { ...order.label, status: "rejected" }, fields: { reason: "재료 소진" } };
    const pending = {
      task_id: "t3",
      version: 1,
      feature: "order",
      after: { ...SEED, order: [changed] },
      changes: { order: { added: [], updated: ["order-1"], removed: [] } },
    };
    const api = fakeApi([{ reply: "바꿀까요?", place: { ...SEED, order: [order] }, pending: [pending] }]);
    const { user } = await open(api);
    await say(user, "주문 거절해 줘");
    const card = await screen.findByRole("region", { name: "저장 전 확인" });
    expect(within(card).getByText("주문 · 메도빅 2조각 · 포장")).toBeInTheDocument();
    expect(within(card).getByText("상태: 새 주문 → 거절")).toBeInTheDocument();
    expect(within(card).getByText("사유: 재료 소진")).toBeInTheDocument();
  });

  test("한 번에 저장되는 카드 여럿이면 카드 버튼 없이 말풍선의 [모두 저장]만 둔다", async () => {
    const api = fakeApi([
      { reply: BUNDLE_REPLY, next_actions: CHIPS, place: BEFORE, pending: [HOURS_PENDING, TALK_PENDING] },
      { reply: "저장했어요.", place: SAVED, pending: [] },
    ]);
    const { user } = await open(api);
    await say(user, "일요일도 열고 주차 톡톡에 답해 줘");
    const cards = await screen.findAllByRole("region", { name: "저장 전 확인" });
    expect(cards).toHaveLength(2);
    for (const card of cards) {
      expect(within(card).queryByRole("button", { name: "저장" })).not.toBeInTheDocument();
      expect(within(card).queryByRole("button", { name: "취소" })).not.toBeInTheDocument();
      expect(within(card).getByText(/모두 저장/)).toBeInTheDocument();
    }
    // 말풍선: 질문과 [모두 저장]만. 변경 줄은 카드에만 있다.
    const bubble = chatArea().getByText(/영업시간·휴무 변경: 아래처럼 바꿀까요\?/).closest(".bubble") as HTMLElement;
    expect(within(bubble).queryByText("일요일: 쉬는 날 → 10:00 ~ 18:00")).not.toBeInTheDocument();
    expect(within(bubble).queryByText("답장: 근처 공영주차장을 이용해 주세요")).not.toBeInTheDocument();
    await user.click(within(bubble).getByRole("button", { name: "모두 저장" }));
    expect(api.act).toHaveBeenLastCalledWith("s1", expect.objectContaining({ action_id: "task.approve" }));
    const result = (await chatArea().findByText("저장 완료")).closest(".result") as HTMLElement;
    expect(within(result).getByText("일요일: 쉬는 날 → 10:00 ~ 18:00")).toBeInTheDocument();
    expect(within(result).getByText("답장: 근처 공영주차장을 이용해 주세요")).toBeInTheDocument();
  });

  test("카드가 하나면 지금처럼 카드에 [저장]·[취소]가 있다", async () => {
    const api = fakeApi([
      { reply: BUNDLE_REPLY.split("\n").slice(0, 3).join("\n"), next_actions: CHIPS, place: BEFORE, pending: [HOURS_PENDING] },
      { reply: "저장했어요.", place: { ...BEFORE, business_hours: HOURS }, pending: [] },
    ]);
    const { user } = await open(api);
    await say(user, "일요일도 열어 줘");
    const card = await screen.findByRole("region", { name: "저장 전 확인" });
    expect(within(card).getByRole("button", { name: "저장" })).toBeInTheDocument();
    expect(within(card).getByRole("button", { name: "취소" })).toBeInTheDocument();
    // 말풍선에는 같은 저장·취소 칩도, 변경 줄도 다시 나오지 않는다.
    const bubble = chatArea().getByText("영업시간·휴무 변경: 아래처럼 바꿀까요?").closest(".bubble") as HTMLElement;
    expect(within(bubble).queryByRole("button")).not.toBeInTheDocument();
    expect(within(bubble).queryByText("일요일: 쉬는 날 → 10:00 ~ 18:00")).not.toBeInTheDocument();
    // 저장하고 카드가 닫혀도 지난 말풍선에 저장·취소 칩이 되살아나지 않는다.
    await user.click(within(card).getByRole("button", { name: "저장" }));
    await chatArea().findByText("저장 완료");
    expect(within(bubble).queryByRole("button")).not.toBeInTheDocument();
  });
});

describe("통계 탭 › AI 리뷰 통계", () => {
  test("좋아요·아쉬워요 TOP3와 테마를 보여 주고 기간을 바꾼다", async () => {
    const { user, api } = await open();
    await user.click(screen.getByRole("tab", { name: "통계" }));
    const panel = within(await screen.findByRole("tabpanel", { name: "통계" }));
    await user.click(panel.getByRole("tab", { name: "AI 리뷰 통계" }));
    const liked = within(await panel.findByRole("list", { name: "좋아요 키워드 TOP 3" }));
    expect(liked.getAllByRole("listitem")).toHaveLength(3);
    expect(liked.getByText("맛")).toBeInTheDocument();
    expect(liked.getByText("음식")).toBeInTheDocument();
    expect(liked.getByText("지난 기간보다 3 늘었어요")).toBeInTheDocument();
    const disliked = within(panel.getByRole("list", { name: "아쉬워요 키워드 TOP 3" }));
    expect(disliked.getByText("분위기", { selector: "strong" })).toBeInTheDocument();
    expect(api.reviewStats).toHaveBeenLastCalledWith("s1", "week");
    await user.click(panel.getByRole("radio", { name: "월간" }));
    await waitFor(() => expect(api.reviewStats).toHaveBeenLastCalledWith("s1", "month"));
    // 방문·유입으로 돌아가면 기존 통계가 그대로 보인다
    await user.click(panel.getByRole("tab", { name: "방문·유입" }));
    expect(await panel.findByText("손님이 들어온 곳")).toBeInTheDocument();
  });

  test("아쉬운 리뷰 정리는 비서에게 말로 맡긴다", async () => {
    const { user, api } = await open();
    await user.click(screen.getByRole("tab", { name: "통계" }));
    const panel = within(await screen.findByRole("tabpanel", { name: "통계" }));
    await user.click(panel.getByRole("tab", { name: "AI 리뷰 통계" }));
    await user.click(await panel.findByRole("button", { name: "아쉬운 리뷰 비서에게 정리 맡기기" }));
    expect(api.say).toHaveBeenLastCalledWith("s1", "요즘 아쉬운 리뷰 정리해 줘");
  });
});

describe("왼쪽 사이드바 접기", () => {
  test("버튼과 ⌘B로 접고 펴며, 접혀도 아이콘 버튼으로 쓸 수 있고 다음에도 기억한다", async () => {
    const { user, api } = await open();
    const nav = () => screen.getByRole("navigation", { name: "대화 목록" });
    expect(nav()).toHaveAttribute("data-collapsed", "false");
    // 위에는 로고만 있고, 접고 펴는 건 가장자리 인덱스 손잡이가 한다
    expect(within(nav()).getByLabelText("AgentHan")).toBeInTheDocument();
    expect(within(nav()).getByText("Agent")).toBeInTheDocument();
    expect(within(nav()).queryByRole("button", { name: /사이드바/ })).not.toBeInTheDocument();
    const handle = screen.getByRole("button", { name: "사이드바 접기" });
    expect(handle).toHaveAttribute("aria-expanded", "true");
    await user.click(handle);
    expect(nav()).toHaveAttribute("data-collapsed", "true");
    expect(within(nav()).getByLabelText("AgentHan")).toBeInTheDocument();
    expect(within(nav()).queryByText("Agent")).not.toBeInTheDocument();
    expect(screen.getByRole("button", { name: "사이드바 펼치기" })).toHaveAttribute("aria-expanded", "false");
    expect(within(nav()).queryByText("최근 대화")).not.toBeInTheDocument();
    await user.click(within(nav()).getByRole("button", { name: "요즘 리뷰" }));
    await waitFor(() => expect(api.act).toHaveBeenLastCalledWith("s1", { action_id: "review.summary", args: {}, label: "요즘 리뷰" }));
    expect(localStorage.getItem("agenthan.nav.collapsed")).toBe("1");
    fireEvent.keyDown(window, { key: "b", metaKey: true });
    expect(nav()).toHaveAttribute("data-collapsed", "false");
    fireEvent.keyDown(window, { key: "b", ctrlKey: true });
    expect(nav()).toHaveAttribute("data-collapsed", "true");
    cleanup();
    render(<App api={fakeApi()} />); // 다시 열어도(지난 대화가 열려도) 접힌 채로
    expect(await screen.findByRole("navigation", { name: "대화 목록" })).toHaveAttribute("data-collapsed", "true");
  });
});

describe("비서 제안", () => {
  test("통계로 고른 카드를 보여 주고, [맡기기]는 비서에게 말로 보낸다", async () => {
    const { user, api } = await open();
    const entry = await screen.findByRole("button", { name: /비서 제안/ });
    await waitFor(() => expect(entry).toHaveAccessibleName("비서 제안 (새 제안 2개)"));
    await user.click(entry);
    const page = within(screen.getByRole("region", { name: "비서 제안" }));
    expect(page.getByText("대표키워드 설정")).toBeInTheDocument();
    expect(page.getByText("‘꿀 케이크’로 52번 들어왔어요.")).toBeInTheDocument();
    expect(screen.queryByRole("main", { name: "비서와 대화" })).not.toBeInTheDocument();
    await user.click(page.getByRole("button", { name: "대표키워드에 추가하기" }));
    expect(api.say).toHaveBeenLastCalledWith("s1", "‘꿀 케이크’를 대표키워드에 추가해 줘");
    expect(await screen.findByRole("main", { name: "비서와 대화" })).toBeInTheDocument();
  });

  test("탭을 여는 카드는 그 사이드카 탭을 보이고, [나중에]는 카드를 치운다", async () => {
    const { user, api } = await open();
    await user.click(await screen.findByRole("button", { name: /비서 제안/ }));
    const page = within(screen.getByRole("region", { name: "비서 제안" }));
    await user.click(page.getByRole("button", { name: "답글 초안 받기" }));
    expect(screen.getByRole("tab", { name: "리뷰" })).toHaveAttribute("aria-selected", "true");
    expect(api.say).not.toHaveBeenCalled();
    const card = page.getByText("대표키워드 설정").closest("article") as HTMLElement;
    await user.click(within(card).getByRole("button", { name: "나중에" }));
    expect(page.queryByText("대표키워드 설정")).not.toBeInTheDocument();
    expect(screen.getByRole("button", { name: /비서 제안/ })).toHaveAccessibleName("비서 제안 (새 제안 1개)");
  });
});

describe("가게 기본 정보 입력 블록", () => {
  const INFO = { name: "한양케이크 HANYANG CAKE", address: "서울시 용산구 가상로 0", phone: "02-000-0000", introduction: "작은 러시안 베이커리예요." };
  const PLACE = { ...SEED, business_info: INFO };

  /** 새 대화가 이 가게 정보로 시작하는 가짜 서버. */
  function bizApi(script: Parameters<typeof fakeApi>[0] = [], pending: Pending[] = []) {
    const api = fakeApi(script, { place: PLACE, pending });
    api.start.mockResolvedValue({ session_id: "s1", place: PLACE, pending: pending as never[] });
    return api;
  }

  async function openForm(api = bizApi()) {
    const opened = await open(api);
    await opened.user.click(screen.getByRole("button", { name: "AgentHan 도구함" }));
    await opened.user.click(within(screen.getByRole("dialog")).getByRole("button", { name: "가게 기본 정보 수정" }));
    const form = chatArea().getByRole("form", { name: "가게 기본 정보" });
    return { ...opened, form: within(form) };
  }

  test("도구함에서 누르면 서버에 묻지 않고 지금 값으로 채운 상호·주소·전화번호·소개글 칸이 대화에 열린다", async () => {
    const { api, form } = await openForm();
    expect(form.getByLabelText("상호")).toHaveValue(INFO.name);
    expect(form.getByLabelText("주소")).toHaveValue(INFO.address);
    expect(form.getByLabelText("전화번호")).toHaveValue(INFO.phone);
    expect(form.getByLabelText("소개글")).toHaveValue(INFO.introduction);
    expect(form.getByRole("button", { name: "미리 보기" })).toBeDisabled(); // 고친 칸이 없다
    expect(api.act).not.toHaveBeenCalled();
    expect(api.say).not.toHaveBeenCalled();
  });

  test("[추천 글 받기]는 소개글 칸만 채우고, [미리 보기]는 고친 칸만 business_info.edit로 보낸다", async () => {
    const pending = { task_id: "t3", version: 1, feature: "business_info", after: { ...PLACE, business_info: { ...INFO, introduction: "x" } }, changes: {} };
    const { user, api, form } = await openForm(bizApi([{ reply: "아래처럼 바꿀까요?", place: PLACE, pending: [pending] }]));
    await user.click(form.getByRole("button", { name: "추천 글 받기" }));
    expect(api.draftIntroduction).toHaveBeenCalledWith("s1");
    await waitFor(() => expect(form.getByLabelText("소개글")).toHaveValue("이태원 골목에서 매일 아침 메도빅을 굽는 러시안 베이커리예요."));
    expect(form.getByText("추천 글을 넣었어요. 마음대로 고쳐 쓰세요.")).toBeInTheDocument();
    expect(api.act).not.toHaveBeenCalled(); // 추천은 저장하지 않는다
    await user.clear(form.getByLabelText("전화번호"));
    await user.type(form.getByLabelText("전화번호"), "02-111-2222");
    await user.click(form.getByRole("button", { name: "미리 보기" }));
    expect(api.act).toHaveBeenCalledWith("s1", {
      action_id: "business_info.edit",
      label: "가게 정보 바꾸기: 전화번호·소개글",
      args: { phone: "02-111-2222", introduction: "이태원 골목에서 매일 아침 메도빅을 굽는 러시안 베이커리예요." },
    });
    expect(chatArea().queryByRole("form", { name: "가게 기본 정보" })).not.toBeInTheDocument();
  });

  test("추천이 실패하면 칸은 그대로 두고 다시 누르라고 알린다. 상호를 비우면 막힌다", async () => {
    const api = bizApi();
    api.draftIntroduction.mockRejectedValueOnce(new Error("502"));
    const { user, form } = await openForm(api);
    await user.click(form.getByRole("button", { name: "추천 글 받기" }));
    expect(await form.findByText("소개글 추천을 만들지 못했어요. 잠시 뒤 다시 눌러 주세요.")).toBeInTheDocument();
    expect(form.getByLabelText("소개글")).toHaveValue(INFO.introduction);
    await user.clear(form.getByLabelText("상호"));
    expect(form.getByText("상호는 비울 수 없어요.")).toBeInTheDocument();
    expect(form.getByRole("button", { name: "미리 보기" })).toBeDisabled();
  });

  test("승인 카드에 바뀌는 가게 정보가 보인다", async () => {
    const after = { ...PLACE, business_info: { ...INFO, phone: "02-111-2222", introduction: "새로 쓴 소개글이에요." } };
    const pending = { task_id: "t3", version: 1, feature: "business_info", after, changes: {} };
    await open(bizApi([], [pending]));
    const card = within(await screen.findByRole("region", { name: "저장 전 확인" }));
    expect(card.getByText("전화번호: 02-000-0000 → 02-111-2222")).toBeInTheDocument();
    expect(card.getByText("새 소개글: 새로 쓴 소개글이에요.")).toBeInTheDocument();
    expect(card.queryByText(/정보를 바꿔요/)).not.toBeInTheDocument();
  });
});

describe("영업시간 시간표 블록", () => {
  const HOURS = {
    mon: { closed: false, open_time: "09:00", close_time: "21:00" },
    tue: { closed: false, open_time: "09:00", close_time: "21:00" },
    wed: { closed: false, open_time: "09:00", close_time: "21:00" },
    thu: { closed: false, open_time: "09:00", close_time: "21:00" },
    fri: { closed: false, open_time: "09:00", close_time: "22:00" },
    sat: { closed: false, open_time: "10:00", close_time: "22:00" },
    sun: { closed: true },
  };
  const PLACE = { ...SEED, business_hours: HOURS };
  function hoursApi(script: Parameters<typeof fakeApi>[0] = [], pending: Pending[] = []) {
    const api = fakeApi(script, { place: PLACE, pending });
    api.start.mockResolvedValue({ session_id: "s1", place: PLACE, pending: pending as never[] });
    return api;
  }
  async function openTable(api = hoursApi()) {
    const opened = await open(api);
    await opened.user.click(screen.getByRole("button", { name: "AgentHan 도구함" }));
    await opened.user.click(within(screen.getByRole("dialog")).getByRole("button", { name: "영업시간·휴무 변경" }));
    const form = chatArea().getByRole("form", { name: "영업시간" });
    return { ...opened, form: within(form) };
  }
  const setTime = (input: HTMLElement, value: string) => fireEvent.change(input, { target: { value } });

  test("도구함에서 누르면 서버에 묻지 않고 지금 일주일 시간표가 대화에 열린다", async () => {
    const { api, form } = await openTable();
    expect(form.getByLabelText("토요일 여는 시간")).toHaveValue("10:00");
    expect(form.getByLabelText("토요일 닫는 시간")).toHaveValue("22:00");
    expect(form.getByRole("checkbox", { name: "일요일 쉬는 날" })).toBeChecked();
    expect(form.queryByLabelText("일요일 여는 시간")).not.toBeInTheDocument();
    expect(form.getByRole("button", { name: "미리 보기" })).toBeDisabled();
    expect(api.act).not.toHaveBeenCalled();
    expect(api.say).not.toHaveBeenCalled();
  });

  test("고친 요일만 hours.edit로 보낸다: 시간 바꾸기·쉬는 날 풀기·쉬는 날 만들기", async () => {
    const after = { ...PLACE, business_hours: { ...HOURS, sat: { closed: false, open_time: "11:00", close_time: "22:00" } } };
    const pending = { task_id: "t4", version: 1, feature: "business_hours", after, changes: {} };
    const { user, api, form } = await openTable(hoursApi([{ reply: "영업시간·휴무 변경: 아래처럼 바꿀까요?", place: PLACE, pending: [pending] }]));
    setTime(form.getByLabelText("토요일 여는 시간"), "11:00");
    await user.click(form.getByRole("checkbox", { name: "일요일 쉬는 날" }));
    expect(form.getByText("일요일 여는·닫는 시간을 넣어 주세요.")).toBeInTheDocument();
    expect(form.getByRole("button", { name: "미리 보기" })).toBeDisabled();
    setTime(form.getByLabelText("일요일 여는 시간"), "10:00");
    setTime(form.getByLabelText("일요일 닫는 시간"), "18:00");
    await user.click(form.getByRole("checkbox", { name: "월요일 쉬는 날" }));
    await user.click(form.getByRole("button", { name: "미리 보기" }));
    expect(api.act).toHaveBeenCalledWith("s1", {
      action_id: "hours.edit",
      label: "영업시간 바꾸기: 월·토·일",
      args: {
        entries: [
          { days: ["mon"], closed: true },
          { days: ["sat"], closed: false, open_time: "11:00", close_time: "22:00" },
          { days: ["sun"], closed: false, open_time: "10:00", close_time: "18:00" },
        ],
      },
    });
    expect(chatArea().queryByRole("form", { name: "영업시간" })).not.toBeInTheDocument();
    const card = within(await screen.findByRole("region", { name: "저장 전 확인" }));
    expect(card.getByText("토요일: 10:00 ~ 22:00 → 11:00 ~ 22:00")).toBeInTheDocument();
  });

  test("서버가 승인 카드 없이 돌려주면 고친 시간표 그대로 다시 연다", async () => {
    const { user, form } = await openTable(hoursApi([{ reply: "값을 다시 확인해 주세요.", place: PLACE, pending: [] }]));
    setTime(form.getByLabelText("금요일 닫는 시간"), "23:00");
    await user.click(form.getByRole("button", { name: "미리 보기" }));
    const again = within(await chatArea().findByRole("form", { name: "영업시간" }));
    expect(again.getByLabelText("금요일 닫는 시간")).toHaveValue("23:00");
  });
});

describe("새로 오픈 소식·매장 QR결제 확인 카드", () => {
  test.each([
    ["new_opening", "새로 오픈 소식 바꾸기", { new_opening: "2026-12-01", onsite_payment: false }, "오픈 날짜: 12월 1일"],
    ["onsite_payment", "매장 QR결제 바꾸기", { new_opening: null, onsite_payment: true }, "매장 QR결제: 꺼짐 → 켜짐"],
  ])("%s 카드는 저장할 값을 보인다", async (feature, title, status, line) => {
    const before = { ...SEED, shop_status: { new_opening: null, onsite_payment: false } };
    const pending = { task_id: "t5", version: 1, feature, after: { ...before, shop_status: status }, changes: {} };
    const api = fakeApi([], { place: before, pending: [pending] });
    api.start.mockResolvedValue({ session_id: "s1", place: before, pending: [pending] as never[] });
    await open(api);
    const card = within(await screen.findByRole("region", { name: "저장 전 확인" }));
    expect(card.getByText(title)).toBeInTheDocument();
    expect(card.getByText(line)).toBeInTheDocument();
    expect(card.queryByText(/정보를 바꿔요/)).not.toBeInTheDocument();
  });
});

describe("새 대화의 메뉴", () => {
  test("새 대화는 메뉴가 빈 가게로 시작한다(메뉴판 사진으로 채운다)", async () => {
    const { api } = await open();
    expect(api.start).toHaveBeenCalledWith({ emptyMenu: true });
  });

  test("?menu=full이면 파일럿 가게의 기본 메뉴로 시작한다", async () => {
    window.history.pushState({}, "", "/?menu=full");
    try {
      const { api } = await open();
      expect(api.start).toHaveBeenCalledWith(undefined);
    } finally {
      window.history.pushState({}, "", "/");
    }
  });
});
