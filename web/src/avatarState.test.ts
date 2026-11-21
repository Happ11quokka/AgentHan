import { describe, expect, test } from "vitest";
import { avatarState } from "./avatarState";

describe("사이드카 아바타 상태 우선순위", () => {
  test.each([
    { hasInput: false, busy: false, hasPendingApproval: false, expected: "idle" },
    { hasInput: true, busy: false, hasPendingApproval: false, expected: "listening" },
    { hasInput: false, busy: false, hasPendingApproval: true, expected: "attention" },
    { hasInput: true, busy: false, hasPendingApproval: true, expected: "attention" },
    { hasInput: false, busy: true, hasPendingApproval: false, expected: "speaking" },
    { hasInput: true, busy: true, hasPendingApproval: false, expected: "speaking" },
    { hasInput: false, busy: true, hasPendingApproval: true, expected: "speaking" },
    { hasInput: true, busy: true, hasPendingApproval: true, expected: "speaking" },
  ])("$hasInput / busy=$busy / pending=$hasPendingApproval → $expected", ({ expected, ...signals }) => {
    expect(avatarState(signals)).toBe(expected);
  });

  test("응답 단계 신호가 없는 busy는 thinking을 추측하지 않는다", () => {
    expect(avatarState({ hasInput: false, busy: true, hasPendingApproval: false })).toBe("speaking");
  });
});
