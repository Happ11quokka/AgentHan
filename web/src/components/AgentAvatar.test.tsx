import { act, cleanup, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, test, vi } from "vitest";
import { AVATAR_STATES } from "../avatarState";
import { AgentAvatar } from "./AgentAvatar";

function motionPreference(initial: boolean) {
  const media = Object.assign(new EventTarget(), { matches: initial });
  vi.stubGlobal("matchMedia", vi.fn(() => media));
  return (reduce: boolean) => act(() => {
    media.matches = reduce;
    media.dispatchEvent(new Event("change"));
  });
}

afterEach(() => {
  cleanup();
  vi.useRealTimers();
  vi.unstubAllGlobals();
});

describe("사이드카 영상 아바타", () => {
  test.each(AVATAR_STATES)("%s 영상을 webm → mp4 순서와 해당 poster로 표시한다", (state) => {
    motionPreference(false);
    const { container } = render(<AgentAvatar state={state} />);
    expect(screen.getByRole("img", { name: "비서 프로필" })).toHaveAttribute("data-state", state);
    const video = container.querySelector("video")!;
    expect(video.muted && video.loop && video.playsInline && video.autoplay).toBe(true);
    expect(video).toHaveAttribute("poster", `/avatar/${state}.png`);
    expect([...video.querySelectorAll("source")].map((source) => [source.getAttribute("src"), source.type])).toEqual([
      [`/avatar/${state}.webm`, "video/webm"],
      [`/avatar/${state}.mp4`, "video/mp4"],
    ]);
  });

  test.each(AVATAR_STATES)("모션 줄이기에서는 %s poster만 마운트하고 영상을 만들지 않는다", (state) => {
    motionPreference(true);
    const { container } = render(<AgentAvatar state={state} />);
    expect(container.querySelector("video")).toBeNull();
    expect(container.querySelector("img")).toHaveAttribute("src", `/avatar/${state}.png`);
  });

  test("실행 중 모션 설정을 바꿔도 재생 영상을 제거하고 현재 상태 poster로 바꾼다", () => {
    const changeMotion = motionPreference(false);
    const { container, rerender } = render(<AgentAvatar state="speaking" />);
    changeMotion(true);
    expect(container.querySelector("video")).toBeNull();
    expect(container.querySelector("img")).toHaveAttribute("src", "/avatar/speaking.png");
    rerender(<AgentAvatar state="attention" />);
    expect(container.querySelector("img")).toHaveAttribute("src", "/avatar/attention.png");
    changeMotion(false);
    expect(container.querySelectorAll("video")).toHaveLength(1);
    expect(container.querySelector("video")).toHaveAttribute("poster", "/avatar/attention.png");
  });

  test("두 레이어를 교차 전환한 뒤 450ms 후 이전 재생 영상을 제거한다", () => {
    motionPreference(false);
    vi.useFakeTimers();
    const { container, rerender } = render(<AgentAvatar state="idle" />);
    rerender(<AgentAvatar state="listening" />);
    expect(container.querySelectorAll("video")).toHaveLength(2);
    expect(container.querySelector('.agent-desk-layer[data-active="true"]')).toHaveAttribute("data-state", "idle");
    act(() => vi.advanceTimersByTime(40));
    expect(container.querySelector('.agent-desk-layer[data-active="true"]')).toHaveAttribute("data-state", "listening");
    act(() => vi.advanceTimersByTime(450));
    expect(container.querySelectorAll("video")).toHaveLength(1);
    expect(container.querySelector("video")).toHaveAttribute("poster", "/avatar/listening.png");
  });

  test("빠르게 상태가 겹쳐 바뀌어도 두 레이어를 넘지 않고 마지막 상태를 유지한다", () => {
    motionPreference(false);
    vi.useFakeTimers();
    const { container, rerender } = render(<AgentAvatar state="idle" />);
    rerender(<AgentAvatar state="thinking" />);
    act(() => vi.advanceTimersByTime(40));
    rerender(<AgentAvatar state="speaking" />);
    rerender(<AgentAvatar state="attention" />);
    expect(container.querySelectorAll("video")).toHaveLength(2);
    act(() => vi.advanceTimersByTime(40));
    act(() => vi.advanceTimersByTime(450));
    expect(container.querySelectorAll("video")).toHaveLength(1);
    expect(container.querySelector("video")).toHaveAttribute("poster", "/avatar/attention.png");
  });
});
