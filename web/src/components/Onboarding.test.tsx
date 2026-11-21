import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, test, vi } from "vitest";
import App from "../App";
import { fakeApi, SEED } from "../test/fakeApi";
import type { OnboardingInput, View } from "../types";
import { Onboarding } from "./Onboarding";

const BLANK_SHOP = { name: "", category: "" };
const FILLED_SHOP = { name: "샘플 러시안 베이커리", category: "베이커리·카페" };

function setup(initial = BLANK_SHOP) {
  const user = userEvent.setup();
  const onSubmit = vi.fn(async (_values: OnboardingInput): Promise<string | null> => null);
  render(<Onboarding initial={initial} onSubmit={onSubmit} />);
  const form = screen.getByRole("form", { name: "가게 시작하기" });
  return { user, onSubmit, form: within(form) };
}

describe("온보딩 화면", () => {
  test("가게 이름·업종 선택지·대표 메뉴 3칸과 메뉴 사진 안내가 보인다", () => {
    const { form } = setup();
    expect(form.getByRole("textbox", { name: "가게 이름" })).toBeInTheDocument();
    for (const option of ["카페", "디저트·베이커리", "한식", "분식", "직접 입력"]) expect(form.getByRole("radio", { name: option })).toBeInTheDocument();
    for (const n of [1, 2, 3]) {
      expect(form.getByRole("textbox", { name: `메뉴 ${n} 이름` })).toBeInTheDocument();
      expect(form.getByRole("textbox", { name: `메뉴 ${n} 가격(원)` })).toBeInTheDocument();
    }
    expect(form.getByText(/메뉴 사진 올리기/)).toBeInTheDocument();
  });

  test("이름·업종·메뉴를 넣고 시작하면 넣은 값만 넘긴다", async () => {
    const { user, onSubmit, form } = setup();
    await user.type(form.getByRole("textbox", { name: "가게 이름" }), "달콤 디저트");
    await user.click(form.getByRole("radio", { name: "디저트·베이커리" }));
    await user.type(form.getByRole("textbox", { name: "메뉴 1 이름" }), "마라 떡볶이");
    await user.type(form.getByRole("textbox", { name: "메뉴 1 가격(원)" }), "9,000");
    await user.click(form.getByRole("button", { name: "시작하기" }));
    expect(onSubmit).toHaveBeenCalledWith({ name: "달콤 디저트", category: "디저트·베이커리", menus: [{ name: "마라 떡볶이", price: 9000 }] });
  });

  test("업종은 직접 입력할 수 있다", async () => {
    const { user, onSubmit, form } = setup();
    await user.type(form.getByRole("textbox", { name: "가게 이름" }), "한강 마라");
    await user.click(form.getByRole("radio", { name: "직접 입력" }));
    await user.type(form.getByRole("textbox", { name: "업종 직접 입력" }), "마라탕 전문점");
    await user.click(form.getByRole("button", { name: "메뉴는 나중에" }));
    expect(onSubmit).toHaveBeenCalledWith({ name: "한강 마라", category: "마라탕 전문점", menus: [] });
  });

  test("[메뉴는 나중에]는 적어 둔 메뉴도 보내지 않는다", async () => {
    const { user, onSubmit, form } = setup();
    await user.type(form.getByRole("textbox", { name: "가게 이름" }), "달콤");
    await user.click(form.getByRole("radio", { name: "카페" }));
    await user.type(form.getByRole("textbox", { name: "메뉴 1 이름" }), "라떼");
    await user.click(form.getByRole("button", { name: "메뉴는 나중에" }));
    expect(onSubmit).toHaveBeenCalledWith({ name: "달콤", category: "카페", menus: [] });
  });

  test("이름이나 업종이 비면 시작하지 않고 무엇이 빠졌는지 알린다", async () => {
    const { user, onSubmit, form } = setup();
    await user.click(form.getByRole("button", { name: "시작하기" }));
    expect(form.getByText("가게 이름을 넣어 주세요.")).toBeInTheDocument();
    await user.type(form.getByRole("textbox", { name: "가게 이름" }), "달콤");
    await user.click(form.getByRole("button", { name: "시작하기" }));
    expect(form.getByText("업종을 골라 주세요.")).toBeInTheDocument();
    expect(onSubmit).not.toHaveBeenCalled();
  });

  test("메뉴 이름만 있거나 가격이 숫자가 아니면 시작하지 않는다", async () => {
    const { user, onSubmit, form } = setup();
    await user.type(form.getByRole("textbox", { name: "가게 이름" }), "달콤");
    await user.click(form.getByRole("radio", { name: "카페" }));
    await user.type(form.getByRole("textbox", { name: "메뉴 2 이름" }), "라떼");
    await user.click(form.getByRole("button", { name: "시작하기" }));
    expect(form.getByText("메뉴 2의 이름과 가격(원)을 함께 넣어 주세요.")).toBeInTheDocument();
    await user.type(form.getByRole("textbox", { name: "메뉴 2 가격(원)" }), "오천");
    await user.click(form.getByRole("button", { name: "시작하기" }));
    expect(form.getByText("메뉴 2의 이름과 가격(원)을 함께 넣어 주세요.")).toBeInTheDocument();
    expect(onSubmit).not.toHaveBeenCalled();
  });

  test("이미 있는 이름·업종은 채워져 있고, 업종이 선택지에 없으면 직접 입력에 들어간다", () => {
    const { form } = setup(FILLED_SHOP);
    expect(form.getByRole("textbox", { name: "가게 이름" })).toHaveValue("샘플 러시안 베이커리");
    expect(form.getByRole("radio", { name: "직접 입력" })).toBeChecked();
    expect(form.getByRole("textbox", { name: "업종 직접 입력" })).toHaveValue("베이커리·카페");
  });

  test("저장에 실패하면 알리고 값은 그대로 둔다", async () => {
    const user = userEvent.setup();
    const onSubmit = vi.fn(async () => "저장하지 못했어요. 다시 시도해 주세요.");
    render(<Onboarding initial={BLANK_SHOP} onSubmit={onSubmit} />);
    await user.type(screen.getByRole("textbox", { name: "가게 이름" }), "달콤");
    await user.click(screen.getByRole("radio", { name: "카페" }));
    await user.click(screen.getByRole("button", { name: "시작하기" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("저장하지 못했어요. 다시 시도해 주세요.");
    expect(screen.getByRole("textbox", { name: "가게 이름" })).toHaveValue("달콤");
  });
});

function apiWith(shop: View["shop"]) {
  const api = fakeApi();
  api.start.mockImplementation(async () => ({ session_id: "s1", place: SEED, pending: [], shop }));
  return api;
}

describe("앱에서 온보딩", () => {
  afterEach(() => window.history.replaceState(null, "", "/"));

  test("가게 이름이나 업종이 빈 대화는 온보딩부터 뜨고, 끝내면 저장하고 세 칸 화면으로 간다", async () => {
    const api = apiWith({ name: "달콤", category: "" });
    const saved = { name: "달콤", category: "카페" };
    api.onboard.mockResolvedValue({ place: { ...SEED, menu: [] }, pending: [], shop: saved });
    const user = userEvent.setup();
    render(<App api={api} />);
    const form = within(await screen.findByRole("form", { name: "가게 시작하기" }));
    expect(screen.queryByRole("main", { name: "비서와 대화" })).not.toBeInTheDocument();
    await user.click(form.getByRole("radio", { name: "카페" }));
    await user.click(form.getByRole("button", { name: "메뉴는 나중에" }));
    expect(api.onboard).toHaveBeenCalledWith("s1", { name: "달콤", category: "카페", menus: [] });
    expect(await screen.findByRole("main", { name: "비서와 대화" })).toBeInTheDocument();
    expect(screen.queryByRole("form", { name: "가게 시작하기" })).not.toBeInTheDocument();
  });

  test("가게 정보가 있으면 온보딩 없이 세 칸 화면으로 시작한다", async () => {
    render(<App api={apiWith(FILLED_SHOP)} />);
    expect(await screen.findByRole("main", { name: "비서와 대화" })).toBeInTheDocument();
    expect(screen.queryByRole("form", { name: "가게 시작하기" })).not.toBeInTheDocument();
  });

  test("데모: 주소에 ?onboarding=1이면 데이터가 있어도 온보딩으로 시작한다", async () => {
    window.history.replaceState(null, "", "/?onboarding=1");
    const api = apiWith(FILLED_SHOP);
    api.onboard.mockResolvedValue({ place: SEED, pending: [], shop: { name: "달콤", category: "카페" } });
    const user = userEvent.setup();
    render(<App api={api} />);
    const form = within(await screen.findByRole("form", { name: "가게 시작하기" }));
    const name = form.getByRole("textbox", { name: "가게 이름" });
    await user.clear(name);
    await user.type(name, "달콤");
    await user.click(form.getByRole("radio", { name: "카페" }));
    await user.click(form.getByRole("button", { name: "시작하기" }));
    expect(await screen.findByRole("main", { name: "비서와 대화" })).toBeInTheDocument();
  });

  test("저장 요청이 실패하면 온보딩에 남아 알린다", async () => {
    const api = apiWith(BLANK_SHOP);
    api.onboard.mockRejectedValue(new Error("boom"));
    const user = userEvent.setup();
    render(<App api={api} />);
    const form = within(await screen.findByRole("form", { name: "가게 시작하기" }));
    await user.type(form.getByRole("textbox", { name: "가게 이름" }), "달콤");
    await user.click(form.getByRole("radio", { name: "카페" }));
    await user.click(form.getByRole("button", { name: "시작하기" }));
    expect(await form.findByRole("alert")).toHaveTextContent("저장하지 못했어요");
    expect(screen.queryByRole("main", { name: "비서와 대화" })).not.toBeInTheDocument();
  });
});
