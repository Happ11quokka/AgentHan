import { act, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, test, vi } from "vitest";
import { NAVER_LOOKUP_MS, NaverImportButton } from "./NaverImport";

describe("네이버 플레이스 불러오기 (시연 컨셉)", () => {
  beforeEach(() => vi.useFakeTimers());
  afterEach(() => vi.useRealTimers());

  test("2.5초 뒤 승인 창이 뜨고, 메뉴는 가져올 항목에 없다", () => {
    const onImported = vi.fn();
    render(<NaverImportButton onImported={onImported} />);
    fireEvent.click(screen.getByRole("button", { name: /네이버 플레이스 불러오기/ }));
    expect(screen.getByRole("button", { name: /찾는 중/ })).toBeDisabled();
    act(() => vi.advanceTimersByTime(NAVER_LOOKUP_MS - 1));
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
    act(() => vi.advanceTimersByTime(1));
    const dialog = screen.getByRole("dialog", { name: "네이버 플레이스에서 정보를 불러올까요?" });
    expect(dialog.querySelector("ul")).not.toHaveTextContent("메뉴");
    fireEvent.click(screen.getByRole("button", { name: "승인" }));
    expect(onImported).toHaveBeenCalledOnce();
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
  });

  test("거절하면 아무것도 불러오지 않는다", () => {
    const onImported = vi.fn();
    render(<NaverImportButton onImported={onImported} />);
    fireEvent.click(screen.getByRole("button", { name: /네이버 플레이스 불러오기/ }));
    act(() => vi.advanceTimersByTime(NAVER_LOOKUP_MS));
    fireEvent.click(screen.getByRole("button", { name: "거절" }));
    expect(onImported).not.toHaveBeenCalled();
    expect(screen.getByRole("button", { name: /네이버 플레이스 불러오기/ })).toBeEnabled();
  });
});
