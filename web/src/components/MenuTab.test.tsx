import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, test, vi } from "vitest";
import { SEED } from "../test/fakeApi";
import type { Place } from "../types";
import { MenuTab } from "./ShopTab";

const filling: Place = {
  ...SEED,
  menu: [
    { id: "m01", name: "메도빅", price: 8000, description: "꿀 케이크", photo_url: "/p/m01?v=1", ai: ["price", "photo"], filling: [] },
    { id: "m09", name: "Сырники", price: 8500, description: null, photo_url: null, ai: [], filling: ["name", "description", "photo"] },
  ],
};

function setup(place: Place = filling) {
  const onEdit = vi.fn(async () => null as string | null);
  const onDelete = vi.fn(async () => null as string | null);
  const onPolish = vi.fn(async () => "메도빅 사진을 보정하고 있어요. 몇 초면 메뉴 탭 사진이 바뀌어요.");
  const user = userEvent.setup();
  render(<MenuTab place={place} pending={[]} onEdit={onEdit} onDelete={onDelete} onPolish={onPolish} />);
  const row = (name: string) => within(screen.getByText(name, { exact: false }).closest("li") as HTMLElement);
  return { user, onEdit, onDelete, onPolish, row };
}

describe("메뉴 탭: 메뉴판 사진으로 바로 올린 메뉴", () => {
  test("채우는 중인 칸은 흐린 자리로, AI가 채운 칸은 배지로 보인다", () => {
    const { row } = setup();
    expect(screen.getByRole("status")).toHaveTextContent("설명과 흰 배경 사진을 채우고 있어요");
    expect(screen.getByRole("status")).toHaveTextContent("메뉴 0/1개 완료");
    expect(screen.getByRole("progressbar")).toHaveAttribute("aria-valuenow", "4"); // 아직 0칸: 최소 4%
    expect(row("Сырники").getByLabelText("설명 쓰는 중")).toBeInTheDocument();
    expect(row("Сырники").getByLabelText("흰 배경 사진 만드는 중")).toBeInTheDocument();
    expect(row("Сырники").getByText(/한국어 이름 만드는 중/)).toBeInTheDocument();
    expect(row("메도빅").getByText("AI 추측")).toBeInTheDocument();
    expect(row("메도빅").getByText("메뉴판 사진")).toBeInTheDocument();
  });

  test("가격을 눌러 고치고 Enter로 바로 저장한다", async () => {
    const { user, onEdit, row } = setup();
    await user.click(row("메도빅").getByRole("button", { name: "가격: 눌러서 고치기" }));
    const box = row("메도빅").getByRole("textbox", { name: "가격" });
    await user.clear(box);
    await user.type(box, "9,000{Enter}");
    expect(onEdit).toHaveBeenCalledWith("m01", { price: 9000 });
  });

  test("잘못된 가격과 빈 이름은 보내지 않고 이유를 보인다", async () => {
    const { user, onEdit, row } = setup();
    await user.click(row("메도빅").getByRole("button", { name: "가격: 눌러서 고치기" }));
    await user.clear(row("메도빅").getByRole("textbox", { name: "가격" }));
    await user.type(row("메도빅").getByRole("textbox", { name: "가격" }), "0{Enter}");
    expect(screen.getByRole("alert")).toHaveTextContent("0보다 큰 숫자");
    await user.keyboard("{Escape}");
    const medovik = row("메도빅");
    await user.click(medovik.getByRole("button", { name: "메뉴 이름: 눌러서 고치기" }));
    await user.clear(medovik.getByRole("textbox", { name: "메뉴 이름" }));
    await user.keyboard("{Enter}");
    expect(screen.getByRole("alert")).toHaveTextContent("이름은 비울 수 없어요");
    expect(onEdit).not.toHaveBeenCalled();
  });

  test("서버가 거절하면 고치던 칸을 그대로 두고 이유를 보인다", async () => {
    const { user, onEdit, row } = setup();
    onEdit.mockResolvedValueOnce("같은 이름의 메뉴가 이미 있어요.");
    await user.click(row("메도빅").getByRole("button", { name: "메뉴 설명: 눌러서 고치기" }));
    await user.type(row("메도빅").getByRole("textbox", { name: "메뉴 설명" }), " 맛있어요{Enter}");
    expect(onEdit).toHaveBeenCalledWith("m01", { description: "꿀 케이크 맛있어요" });
    expect(screen.getByRole("alert")).toHaveTextContent("같은 이름");
    expect(row("메도빅").getByRole("textbox", { name: "메뉴 설명" })).toBeInTheDocument();
  });

  test("지우기는 한 번 더 묻고 지운다", async () => {
    const { user, onDelete, row } = setup();
    await user.click(row("메도빅").getByRole("button", { name: "메도빅 지우기" }));
    expect(onDelete).not.toHaveBeenCalled();
    await user.click(row("메도빅").getByRole("button", { name: "지우기" }));
    expect(onDelete).toHaveBeenCalledWith("m01");
  });

  test("사진이 있는 메뉴만 [사진 다듬기]가 있고, 누르면 안내 한 줄을 보인다", async () => {
    const { user, onPolish, row } = setup();
    expect(row("Сырники").queryByRole("button", { name: "사진 다듬기" })).toBeNull();
    const medovik = row("메도빅");
    await user.click(medovik.getByRole("button", { name: "사진 다듬기" }));
    expect(onPolish).toHaveBeenCalledWith("m01");
    expect(medovik.getByText(/몇 초면/)).toBeInTheDocument();
  });

  test("다듬는 중에는 사진에 표시하고 위 안내 줄은 띄우지 않는다", () => {
    const { row } = setup({ ...SEED, menu: [{ ...filling.menu[0], filling: ["polish"] }] });
    expect(row("메도빅").getByText("다듬는 중")).toBeInTheDocument();
    expect(screen.queryByRole("status")).toBeNull();
  });

  test("메뉴판 사진을 읽는 동안 걸린 시간과 진행 막대, 빈 메뉴 자리를 보인다", () => {
    render(<MenuTab place={{ ...SEED, menu: [] }} pending={[]} readingSince={Date.now() - 3000} />);
    const status = screen.getByRole("status");
    expect(status).toHaveTextContent("메뉴판 사진을 읽고 있어요");
    expect(status).toHaveTextContent(/[34]\.\d초/);
    expect(Number(screen.getByRole("progressbar").getAttribute("aria-valuenow"))).toBeGreaterThan(30);
    expect(screen.queryByText(/아직 메뉴가 없어요/)).toBeNull();
  });

  test("메뉴가 없으면 메뉴판 사진을 올리라고 안내한다", () => {
    setup({ ...SEED, menu: [] });
    expect(screen.getByText(/메뉴판 사진을 올리면 여기에 바로 채워져요/)).toBeInTheDocument();
  });
});
