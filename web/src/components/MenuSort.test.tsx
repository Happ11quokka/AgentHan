import { act, fireEvent, render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, test, vi } from "vitest";
import { sortMenu } from "../menuSort";
import { SEED } from "../test/fakeApi";
import type { MenuRow, Place } from "../types";
import { MenuTab } from "./ShopTab";

const row = (id: string, name: string, price: number | null, photo: boolean, more: Partial<MenuRow> = {}): MenuRow => ({
  id,
  name,
  price,
  description: "",
  photo_url: photo ? `/p/${id}` : null,
  illustration_url: "/illustrations/default_cake.svg",
  ai: [],
  filling: [],
  ...more,
});

// 저장한 순서(등록순): 메도빅 · 아메리카노 · 나폴레옹 · 블린(가격 없음) · 콤포트
const MENU = [
  row("m01", "메도빅", 7500, true),
  row("m03", "아메리카노", 4500, false),
  row("m02", "나폴레옹", 7000, true),
  row("m04", "블린", null, false),
  row("m05", "콤포트", 4500, true, { filling: ["photo"] }), // 흰 배경 사진을 만드는 중: 아직 사진 없음
];
const place: Place = { ...SEED, menu: MENU };

const names = (rows: MenuRow[]) => rows.map((m) => m.name);
const SORT_KEY = "agenthan.menuSort.v1";

describe("보기 정렬 순서", () => {
  test("등록순은 저장한 순서 그대로다", () => {
    expect(sortMenu(MENU, "saved")).toBe(MENU);
  });

  test("이름순은 가나다순", () => {
    expect(names(sortMenu(MENU, "name"))).toEqual(["나폴레옹", "메도빅", "블린", "아메리카노", "콤포트"]);
  });

  test("가격 낮은순·높은순: 같은 가격은 등록순, 가격 없는 메뉴는 맨 뒤", () => {
    expect(names(sortMenu(MENU, "price-asc"))).toEqual(["아메리카노", "콤포트", "나폴레옹", "메도빅", "블린"]);
    expect(names(sortMenu(MENU, "price-desc"))).toEqual(["메도빅", "나폴레옹", "아메리카노", "콤포트", "블린"]);
  });

  test("사진 없는 것 먼저: 대표 그림과 만드는 중인 사진은 사진이 없는 것으로 친다", () => {
    expect(names(sortMenu(MENU, "no-photo"))).toEqual(["아메리카노", "블린", "콤포트", "메도빅", "나폴레옹"]);
  });

  test("저장한 메뉴 목록은 건드리지 않는다", () => {
    const before = names(MENU);
    sortMenu(MENU, "name");
    expect(names(MENU)).toEqual(before);
  });
});

function setup(menuPlace: Place = place) {
  const onReorder = vi.fn(async (_ids: string[]) => null as string | null);
  const user = userEvent.setup();
  const view = render(<MenuTab place={menuPlace} pending={[]} onEdit={vi.fn(async () => null)} onReorder={onReorder} />);
  const shown = () =>
    within(screen.getAllByRole("list")[0])
      .getAllByRole("listitem")
      .map((li) => li.querySelector("strong")?.firstChild?.textContent);
  return { user, onReorder, view, shown };
}

describe("메뉴 탭: 보기 정렬", () => {
  afterEach(() => vi.restoreAllMocks());

  test("고른 정렬로 보이고, 저장 요청은 보내지 않는다", async () => {
    const { user, onReorder, shown } = setup();
    const select = screen.getByRole("combobox", { name: "정렬" });
    expect(select).toHaveValue("saved");
    expect(within(select).getAllByRole("option").map((o) => o.textContent)).toEqual(["등록순", "이름순", "가격 낮은순", "가격 높은순", "사진 없는 것 먼저"]);
    expect(shown()).toEqual(["메도빅", "아메리카노", "나폴레옹", "블린", "콤포트"]);
    await user.selectOptions(select, "가격 높은순");
    expect(shown()).toEqual(["메도빅", "나폴레옹", "아메리카노", "콤포트", "블린"]);
    expect(onReorder).not.toHaveBeenCalled();
  });

  test("고른 정렬은 이 브라우저에 남아 다시 열어도 그대로다", async () => {
    const { user, view, shown } = setup();
    await user.selectOptions(screen.getByRole("combobox", { name: "정렬" }), "이름순");
    expect(localStorage.getItem(SORT_KEY)).toBe(JSON.stringify("name"));
    view.unmount();
    setup();
    expect(screen.getByRole("combobox", { name: "정렬" })).toHaveValue("name");
    expect(shown()[0]).toBe("나폴레옹");
  });

  test("저장소가 막힌 브라우저에서도 등록순으로 열리고 정렬은 된다", async () => {
    vi.spyOn(Storage.prototype, "getItem").mockImplementation(() => {
      throw new Error("blocked");
    });
    vi.spyOn(Storage.prototype, "setItem").mockImplementation(() => {
      throw new Error("blocked");
    });
    const { user, shown } = setup();
    expect(screen.getByRole("combobox", { name: "정렬" })).toHaveValue("saved");
    await user.selectOptions(screen.getByRole("combobox", { name: "정렬" }), "가격 낮은순");
    expect(shown()[0]).toBe("아메리카노");
  });

  test("모르는 값이 남아 있으면 등록순", () => {
    localStorage.setItem(SORT_KEY, JSON.stringify("random"));
    setup();
    expect(screen.getByRole("combobox", { name: "정렬" })).toHaveValue("saved");
  });
});

describe("메뉴 탭: 순서 바꾸기", () => {
  test("위·아래 버튼으로 옮기고 [순서 저장]을 누르면 그 순서를 저장한다", async () => {
    const { user, onReorder, shown } = setup();
    await user.click(screen.getByRole("button", { name: "순서 바꾸기" }));
    const list = within(screen.getByRole("list", { name: "메뉴 순서" }));
    expect(list.getByRole("button", { name: "메도빅 위로" })).toBeDisabled();
    expect(list.getByRole("button", { name: "콤포트 아래로" })).toBeDisabled();

    await user.click(list.getByRole("button", { name: "나폴레옹 위로" }));
    await user.click(list.getByRole("button", { name: "나폴레옹 위로" }));
    await user.click(list.getByRole("button", { name: "메도빅 아래로" }));
    expect(screen.getByText("메도빅, 5개 중 3번째")).toBeInTheDocument(); // 화면 낭독기 알림
    expect(onReorder).not.toHaveBeenCalled();

    await user.click(screen.getByRole("button", { name: "순서 저장" }));
    expect(onReorder).toHaveBeenCalledWith(["m02", "m03", "m01", "m04", "m05"]);
    expect(screen.queryByRole("list", { name: "메뉴 순서" })).toBeNull(); // 저장하면 보통 화면으로
    expect(shown()[0]).toBe("메도빅"); // 화면은 서버가 돌려준 가게로 다시 그린다(여기서는 그대로)
  });

  test("키보드로: 옮긴 뒤에도 같은 메뉴의 버튼에 머물러 계속 옮길 수 있다", async () => {
    const { user, onReorder } = setup();
    await user.click(screen.getByRole("button", { name: "순서 바꾸기" }));
    screen.getByRole("button", { name: "콤포트 위로" }).focus();
    await user.keyboard("{Enter}{Enter}{Enter}{Enter}");
    // 맨 위에 닿으면 위로 버튼이 꺼지고 아래로 버튼에 머문다
    expect(screen.getByRole("button", { name: "콤포트 아래로" })).toHaveFocus();
    await user.click(screen.getByRole("button", { name: "순서 저장" }));
    expect(onReorder).toHaveBeenCalledWith(["m05", "m01", "m03", "m02", "m04"]);
  });

  test("취소하거나 그대로 저장하면 저장 요청을 보내지 않는다", async () => {
    const { user, onReorder } = setup();
    await user.click(screen.getByRole("button", { name: "순서 바꾸기" }));
    await user.click(screen.getByRole("button", { name: "블린 위로" }));
    await user.click(screen.getByRole("button", { name: "취소" }));
    await user.click(screen.getByRole("button", { name: "순서 바꾸기" }));
    expect(screen.getAllByRole("listitem")[3]).toHaveTextContent("블린"); // 취소한 이동은 남지 않는다
    await user.click(screen.getByRole("button", { name: "순서 저장" }));
    expect(onReorder).not.toHaveBeenCalled();
  });

  test("저장이 실패하면 고르던 순서를 그대로 두고 이유를 보인다", async () => {
    const { user, onReorder } = setup();
    onReorder.mockResolvedValueOnce("순서를 저장하지 못했어요.");
    await user.click(screen.getByRole("button", { name: "순서 바꾸기" }));
    await user.click(screen.getByRole("button", { name: "블린 위로" }));
    await user.click(screen.getByRole("button", { name: "순서 저장" }));
    expect(screen.getByRole("alert")).toHaveTextContent("순서를 저장하지 못했어요.");
    const list = within(screen.getByRole("list", { name: "메뉴 순서" }));
    expect(list.getAllByRole("listitem")[2]).toHaveTextContent("블린");
  });

  test("등록순이 아니면 순서 바꾸기를 끄고 짧은 안내를 보인다", async () => {
    const { user } = setup();
    expect(screen.queryByText("순서 바꾸기는 등록순에서만 할 수 있어요.")).toBeNull();
    await user.selectOptions(screen.getByRole("combobox", { name: "정렬" }), "이름순");
    const button = screen.getByRole("button", { name: "순서 바꾸기" });
    expect(button).toBeDisabled();
    expect(button).toHaveAccessibleDescription("순서 바꾸기는 등록순에서만 할 수 있어요.");
    await user.selectOptions(screen.getByRole("combobox", { name: "정렬" }), "등록순");
    expect(screen.getByRole("button", { name: "순서 바꾸기" })).toBeEnabled();
  });

  test("저장 함수가 없거나 메뉴가 하나면 순서 바꾸기가 없다", () => {
    render(<MenuTab place={place} pending={[]} />);
    expect(screen.getByRole("combobox", { name: "정렬" })).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "순서 바꾸기" })).toBeNull();
    setup({ ...SEED, menu: [MENU[0]] });
    expect(screen.queryAllByRole("button", { name: "순서 바꾸기" })).toHaveLength(0);
  });

  test("손잡이를 끌어서 옮긴다", async () => {
    const { onReorder, user } = setup();
    await user.click(screen.getByRole("button", { name: "순서 바꾸기" }));
    const items = within(screen.getByRole("list", { name: "메뉴 순서" })).getAllByRole("listitem");
    // jsdom은 자리를 계산하지 않는다: 줄마다 높이 50px로 둔다
    items.forEach((li, i) => {
      li.getBoundingClientRect = () => ({ top: i * 50, height: 50, bottom: i * 50 + 50, left: 0, right: 300, width: 300, x: 0, y: i * 50, toJSON: () => ({}) });
    });
    const grip = items[0].querySelector(".menu-grip") as HTMLElement;
    fireEvent.pointerDown(grip, { pointerType: "touch", button: 0, clientY: 25 });
    await act(async () => {
      window.dispatchEvent(new MouseEvent("pointermove", { clientY: 130 }));
    });
    await act(async () => {
      window.dispatchEvent(new MouseEvent("pointerup", { clientY: 130 }));
    });
    expect(screen.getByText("메도빅, 5개 중 3번째")).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "순서 저장" }));
    expect(onReorder).toHaveBeenCalledWith(["m03", "m02", "m01", "m04", "m05"]);
  });
});
