import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, test, vi } from "vitest";
import { draft, photoPending, SEED } from "../test/fakeApi";
import type { DraftRow } from "../types";
import { ApprovalCard } from "./ApprovalCard";

/** 음식 사진 초안: 이름은 비어 있고 AI 추측은 자리표시자로만 온다(#93). */
const food = (over: Partial<DraftRow> = {}) =>
  draft({ name_ko: "", is_food: true, name_guess: "밀푀유 케이크", notes: ["food_guess", "price_missing"], ...over });

function show(drafts: DraftRow[]) {
  const onAnswer = vi.fn();
  render(<ApprovalCard place={SEED} pending={photoPending(drafts)} onAnswer={onAnswer} onAction={vi.fn()} />);
  return { onAnswer, card: within(screen.getByRole("region", { name: "저장 전 확인" })), user: userEvent.setup() };
}

describe("음식 사진 승인 카드", () => {
  test("메뉴 이름은 사장님이 입력하고 AI 추측은 자리표시자로만 보인다", async () => {
    const { onAnswer, card, user } = show([food()]);
    const input = card.getByRole("textbox", { name: "메뉴 이름" });
    expect(input).toHaveValue("");
    expect(input).toHaveAttribute("placeholder", expect.stringContaining("밀푀유 케이크"));
    expect(card.queryByText("밀푀유 케이크")).not.toBeInTheDocument();
    expect(card.getByRole("button", { name: "저장" })).toBeDisabled();
    const put = card.getByRole("button", { name: "이름 넣기" });
    expect(put).toBeDisabled();
    await user.type(input, "  메도빅 ");
    await user.click(put);
    expect(onAnswer).toHaveBeenCalledWith("메뉴 이름: 메도빅");
  });

  test("Enter로도 이름을 보낸다", async () => {
    const { onAnswer, card, user } = show([food()]);
    await user.type(card.getByRole("textbox", { name: "메뉴 이름" }), "체리 파이{Enter}");
    expect(onAnswer).toHaveBeenCalledWith("메뉴 이름: 체리 파이");
  });

  test("입력한 이름이 기존 메뉴와 맞으면 붙일지 새 메뉴로 올릴지 고른다", async () => {
    const attach = food({ name_ko: "메도빅", change: "photo_attach", previous_price: 7500, menu_name: "메도빅", notes: [] });
    const { onAnswer, card, user } = show([attach]);
    expect(card.getByText("기존 메뉴 메도빅에 이 사진을 붙일까요?")).toBeInTheDocument();
    await user.click(card.getByRole("button", { name: "메도빅에 붙이기" }));
    expect(onAnswer).toHaveBeenLastCalledWith("메도빅 사진 바꿔");
    await user.click(card.getByRole("button", { name: "새 메뉴로 올리기" }));
    expect(onAnswer).toHaveBeenLastCalledWith("새 메뉴로 올려 줘");
  });

  test("이름을 넣은 뒤에도 고칠 수 있다", async () => {
    const { onAnswer, card, user } = show([food({ name_ko: "메도빅", notes: ["price_missing"] })]);
    const input = card.getByRole("textbox", { name: "메뉴 이름" });
    expect(input).toHaveValue("메도빅");
    expect(card.getByRole("button", { name: "이름 넣기" })).toBeDisabled(); // 그대로면 보낼 것이 없다
    await user.clear(input);
    await user.type(input, "꿀케이크");
    await user.click(card.getByRole("button", { name: "이름 넣기" }));
    expect(onAnswer).toHaveBeenCalledWith("메뉴 이름: 꿀케이크");
  });

  test("메뉴판 사진 초안에는 이름 입력칸이 없다", () => {
    const { card } = show([draft({ name_ko: "메도빅", price: 8000, change: "price_change", previous_price: 7500, notes: [] })]);
    expect(card.queryByRole("textbox", { name: "메뉴 이름" })).not.toBeInTheDocument();
    expect(card.queryByRole("button", { name: "새 메뉴로 올리기" })).not.toBeInTheDocument();
    expect(card.getByText("메도빅")).toBeInTheDocument();
  });
});
