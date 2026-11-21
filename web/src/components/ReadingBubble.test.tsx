import { render, screen } from "@testing-library/react";
import { describe, expect, test } from "vitest";
import { ReadingBubble } from "./ReadingBubble";

describe("대화창의 메뉴판 읽기 진행 표시", () => {
  test("걸린 초, 진행 막대, 단계 문구를 보인다", () => {
    render(<ReadingBubble since={Date.now() - 9000} />);
    const status = screen.getByRole("status");
    expect(status).toHaveTextContent("메뉴판 사진을 읽고 있어요");
    expect(status).toHaveTextContent(/9\.\d초/);
    expect(status).toHaveTextContent("거의 다 됐어요");
    const percent = Number(screen.getByRole("progressbar").getAttribute("aria-valuenow"));
    expect(percent).toBeGreaterThan(70);
    expect(percent).toBeLessThanOrEqual(95);
  });
});
