import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, test, vi } from "vitest";
import type { ReviewReport, Routines } from "../types";
import { RoutineSettings } from "./RoutineSettings";

const OFF: ReviewReport = { enabled: false, schedule: null, next_run: null, text: "정기 보고 꺼짐" };
const ON: ReviewReport = { enabled: true, schedule: "매일 09:00", next_run: "2026-11-22T09:00", text: "매일 09:00 리뷰 요약 받는 중" };
const routines = (report: Partial<Routines["report"]> = {}, drafts = true): Routines => ({
  drafts,
  report: { enabled: false, frequency: null, weekday: "mon", time: "09:00", ...report },
});

function setup(report: ReviewReport | null, value: Routines | null, change = vi.fn(async () => null as string | null)) {
  const run = vi.fn(async () => null as string | null);
  const user = userEvent.setup();
  const utils = render(<RoutineSettings report={report} routines={value} onChange={change} onRunReport={run} />);
  const region = () => within(screen.getByRole("region", { name: "루틴 설정" }));
  return { user, change, run, region, ...utils };
}

describe("루틴 설정 (#33)", () => {
  test("꺼진 정기 보고는 스위치만, 켜면 매일로 저장을 요청한다", async () => {
    const { user, change, region } = setup(OFF, routines());
    const report = region().getByRole("switch", { name: "리뷰 정기 보고" });
    expect(report).toHaveAttribute("aria-checked", "false");
    expect(report).toHaveTextContent("요약 받지 말기");
    expect(region().queryByRole("radiogroup", { name: "얼마나 자주" })).not.toBeInTheDocument();
    await user.click(report);
    expect(change).toHaveBeenCalledWith({ report: { enabled: true } });
  });

  test("켜져 있으면 일정이 보이고 주기·요일·시각을 바로 바꾼다", async () => {
    const { user, change, region, rerender, run } = setup(ON, routines({ enabled: true, frequency: "daily" }));
    expect(region().getByText("매일 09:00에 리뷰 요약을 보내요")).toBeInTheDocument();
    expect(region().getByRole("switch", { name: "리뷰 정기 보고" })).toHaveTextContent("요약 받아두기");
    expect(region().queryByRole("radiogroup", { name: "무슨 요일" })).not.toBeInTheDocument(); // 매일엔 요일이 없다

    await user.click(region().getByRole("radio", { name: "매주" }));
    expect(change).toHaveBeenLastCalledWith({ report: { frequency: "weekly" } });

    const weekly = routines({ enabled: true, frequency: "weekly", weekday: "mon" });
    rerender(<RoutineSettings report={{ ...ON, schedule: "매주 월요일 09:00" }} routines={weekly} onChange={change} onRunReport={run} />);
    expect(region().getByRole("radio", { name: "매주" })).toHaveAttribute("aria-checked", "true");
    await user.click(region().getByRole("radio", { name: "금요일" }));
    expect(change).toHaveBeenLastCalledWith({ report: { weekday: "fri" } });

    const time = region().getByLabelText("몇 시에");
    await user.clear(time);
    await user.type(time, "18:30");
    expect(change).toHaveBeenLastCalledWith({ report: { time: "18:30" } });
  });

  test("답글 초안 스위치는 받아두기·받지 말기를 바꾼다", async () => {
    const { user, change, region } = setup(OFF, routines({}, true));
    const drafts = region().getByRole("switch", { name: "답글 초안 미리 쓰기" });
    expect(drafts).toHaveAttribute("aria-checked", "true");
    expect(drafts).toHaveTextContent("초안 받아두기");
    await user.click(drafts);
    expect(change).toHaveBeenCalledWith({ drafts: false });
  });

  test("실패하면 이유를 보이고, 저장하는 동안 다시 누를 수 없다", async () => {
    let finish: (v: string | null) => void = () => {};
    const change = vi.fn(() => new Promise<string | null>((r) => (finish = r)));
    const { user, region } = setup(OFF, routines(), change);
    await user.click(region().getByRole("switch", { name: "리뷰 정기 보고" }));
    expect(region().getByRole("switch", { name: "리뷰 정기 보고" })).toBeDisabled();
    finish("설정을 바꾸지 못했어요. 다시 시도해 주세요.");
    expect(await region().findByRole("alert")).toHaveTextContent("설정을 바꾸지 못했어요");
    expect(region().getByRole("switch", { name: "리뷰 정기 보고" })).toBeEnabled();
  });

  test("[지금 한 번 받아 보기]는 설정과 상관없이 보고를 받는다", async () => {
    const { user, run, region } = setup(OFF, routines());
    await user.click(region().getByRole("button", { name: "지금 한 번 받아 보기" }));
    expect(run).toHaveBeenCalledTimes(1);
  });

  test("루틴을 모르는 예전 서버면 스위치 없이 보고 한 줄만 보인다", () => {
    const { region } = setup(OFF, null);
    expect(region().queryByRole("switch")).not.toBeInTheDocument();
    expect(region().getByText("정기 보고 꺼짐")).toBeInTheDocument();
  });
});
