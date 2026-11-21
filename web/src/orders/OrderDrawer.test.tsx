// 비서 화면의 전화 주문: 왼쪽 [전화 주문] → 서랍, 대기·확인 숫자, 전표 버튼, 비서 말풍선, 대화창 알림 줄.
import { act, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, test, vi } from "vitest";
import App from "../App";
import type { OrderCard, OrderCardLine, OwnerAction } from "../screens/owner/ownerState";
import { fakeApi } from "../test/fakeApi";
import type { OrdersApi } from "./useOrders";

const KEY = "owner-key";

function line(name: string, quantity: number, over: Partial<OrderCardLine> = {}): OrderCardLine {
  return { menu_id: name, name_ko: name, name_owner: name, quantity, options: [], options_owner: [], request: "", request_owner: "", badges: [], subtotal: 0, ...over };
}

function card(over: Partial<OrderCard> = {}): OrderCard {
  const now = Date.now();
  return {
    id: "call-1",
    status: "new",
    received_at: new Date(now - 3 * 60_000).toISOString(),
    received_label: "16:05",
    pickup_time: new Date(now + 60 * 60_000).toISOString(),
    pickup_label: "18:30",
    lines: [line("떡볶이", 2), line("순대", 1, { request: "땅콩 빼 주세요", badges: ["allergy"] })],
    total: 23000,
    total_label: "23,000₩",
    request: 1,
    stage: "after_call",
    requested_at: new Date(now - 60_000).toISOString(),
    deadline_at: new Date(now + 4 * 60_000).toISOString(),
    ...over,
  };
}

function fakeOrders() {
  let dispatch: (a: OwnerAction) => void = () => {};
  const api: OrdersApi = {
    connect: vi.fn((_key, d) => {
      dispatch = d;
      return () => {};
    }),
    accept: vi.fn(async () => true),
    reject: vi.fn(async () => true),
    undoSoldOut: vi.fn(async () => true),
  };
  const snapshot = (orders: OrderCard[]) =>
    act(() => dispatch({ type: "snapshot", data: { orders, calls: [] }, receivedAt: Date.now() }));
  const update = (order: OrderCard) => act(() => dispatch({ type: "order.update", data: order }));
  return { api, snapshot, update };
}

async function open(withKey = true) {
  if (withKey) localStorage.setItem("agenthan.ownerKey.v1", KEY);
  const orders = fakeOrders();
  const user = userEvent.setup();
  render(<App api={fakeApi()} ordersApi={orders.api} />);
  await screen.findByRole("heading", { level: 1 });
  return { user, orders };
}

describe("전화 주문 서랍", () => {
  test("왼쪽 [전화 주문]은 자주 쓰는 일 맨 위에 있고 확인 안 한 주문 수를 배지로 보인다", async () => {
    const { orders } = await open();
    expect(orders.api.connect).toHaveBeenCalledWith(KEY, expect.any(Function));
    const first = screen.getAllByRole("list")[0].querySelector("li");
    expect(first).toHaveTextContent("전화 주문");
    await orders.snapshot([card(), card({ id: "call-2" }), card({ id: "call-3", status: "accepted" })]);
    expect(screen.getByRole("button", { name: "전화 주문 (확인 안 한 주문 2건)" })).toHaveTextContent("2");
  });

  test("누르면 페이지를 바꾸지 않고 도구함처럼 옆에 열리고, 대기·확인 건수와 전표를 보인다", async () => {
    const { user, orders } = await open();
    await orders.snapshot([card(), card({ id: "call-3", status: "accepted", pickup_label: "17:00" })]);
    await user.click(screen.getByRole("button", { name: /^전화 주문/ }));
    const drawer = screen.getByRole("dialog", { name: "전화 주문" });
    expect(drawer).toHaveTextContent("대기 1건");
    expect(drawer).toHaveTextContent("확인 1건");
    const ticket = within(drawer).getByRole("article", { name: "18:30 픽업 주문" });
    expect(ticket).toHaveTextContent("23,000원");
    expect(ticket).toHaveTextContent("16:05 주문 (3분 전)");
    expect(ticket).toHaveTextContent("떡볶이");
    expect(ticket).toHaveTextContent("알레르기땅콩 빼 주세요");
    expect(ticket).toHaveTextContent(/안에 확인해 주세요/);
    // 확인한 주문은 접혀 있다
    expect(within(drawer).getByText("확인한 주문 1건")).toBeInTheDocument();
    expect(screen.getByRole("heading", { level: 1 })).toBeInTheDocument(); // 대화 화면 그대로
  });

  test("[확인]·[15분 늦춰 확인]은 사장님 화면과 같은 확인 요청을 보낸다", async () => {
    const { user, orders } = await open();
    await orders.snapshot([card()]);
    await user.click(screen.getByRole("button", { name: /^전화 주문/ }));
    const ticket = screen.getByRole("article", { name: "18:30 픽업 주문" });
    await user.click(within(ticket).getByRole("button", { name: "15분 늦춰 확인" }));
    expect(orders.api.accept).toHaveBeenLastCalledWith(KEY, "call-1", 15);
    await user.click(within(ticket).getByRole("button", { name: "확인" }));
    expect(orders.api.accept).toHaveBeenLastCalledWith(KEY, "call-1", 0);
    await orders.update(card({ status: "accepted" }));
    const drawer = screen.getByRole("dialog", { name: "전화 주문" });
    expect(drawer).toHaveTextContent("대기 0건");
    expect(drawer).toHaveTextContent("확인 1건");
    expect(drawer).toHaveTextContent("확인할 주문이 없어요");
  });

  test("서랍은 Esc로 닫힌다", async () => {
    const { user, orders } = await open();
    await orders.snapshot([]);
    await user.click(screen.getByRole("button", { name: /^전화 주문/ }));
    expect(screen.getByRole("dialog", { name: "전화 주문" })).toBeInTheDocument();
    await user.keyboard("{Escape}");
    expect(screen.queryByRole("dialog", { name: "전화 주문" })).not.toBeInTheDocument();
  });

  test("사장님 화면 키가 없으면 연결하지 않고, 서랍에서 그 까닭을 알린다", async () => {
    const { user, orders } = await open(false);
    expect(orders.api.connect).not.toHaveBeenCalled();
    await user.click(screen.getByRole("button", { name: /^전화 주문/ }));
    expect(screen.getByRole("dialog", { name: "전화 주문" })).toHaveTextContent("아직 연결하지 않았어요");
  });
});

describe("비서 말풍선", () => {
  test("새 주문이 들어오면 오른쪽 비서가 말풍선으로 알리고, [주문 보기]로 서랍을 연다", async () => {
    const { user, orders } = await open();
    await orders.snapshot([]);
    await orders.update(card());
    const nudge = screen.getByRole("status", { name: "비서 알림" });
    expect(nudge).toHaveTextContent("전화 주문이 들어왔어요. 18:30 픽업, 떡볶이 2, 순대 1");
    expect(screen.getByRole("complementary", { name: "비서와 가게" })).toContainElement(nudge);
    // 새 주문은 대화창에 남기지 않는다
    expect(screen.getAllByRole("button", { name: "주문 보기" })).toHaveLength(1);
    await user.click(within(nudge).getByRole("button", { name: "주문 보기" }));
    expect(screen.getByRole("dialog", { name: "전화 주문" })).toBeInTheDocument();
    expect(screen.queryByRole("status", { name: "비서 알림" })).not.toBeInTheDocument();
  });

  test("확인 마감이 1분 안으로 들어오면 말풍선과 함께 대화창에도 알림 줄을 남긴다", async () => {
    const { user, orders } = await open();
    await orders.snapshot([card({ deadline_at: new Date(Date.now() + 40_000).toISOString() })]);
    const nudge = screen.getByRole("status", { name: "비서 알림" });
    expect(nudge).toHaveTextContent(/확인 안 한 주문이 1건 있어요. \d+초 안에 확인하지 않으면 자동으로 취소돼요./);
    const chatLine = screen.getAllByRole("status").find((el) => el.classList.contains("notice-orders"));
    expect(chatLine).toBeDefined();
    await user.click(within(nudge).getByRole("button", { name: "알림 닫기" }));
    expect(screen.queryByRole("status", { name: "비서 알림" })).not.toBeInTheDocument();
    await user.click(within(chatLine!).getByRole("button", { name: "주문 보기" }));
    await waitFor(() => expect(screen.getByRole("dialog", { name: "전화 주문" })).toBeInTheDocument());
  });

  test("새 주문 말풍선이 마감 알림을 잠시 덮어도, 닫으면 마감 알림이 다시 보인다", async () => {
    const { user, orders } = await open();
    await orders.snapshot([card({ deadline_at: new Date(Date.now() + 40_000).toISOString() })]);
    await orders.update(card({ id: "call-2", pickup_label: "19:00" }));
    const nudge = screen.getByRole("status", { name: "비서 알림" });
    expect(nudge).toHaveTextContent("19:00 픽업");
    await user.click(within(nudge).getByRole("button", { name: "알림 닫기" }));
    expect(screen.getByRole("status", { name: "비서 알림" })).toHaveTextContent("확인 안 한 주문이");
  });

  test("오른쪽 칸이 접히는 좁은 화면에서는 말풍선이 화면 구석에 뜬다", async () => {
    vi.stubGlobal("matchMedia", vi.fn(() => ({ matches: true, addEventListener: () => {}, removeEventListener: () => {} })));
    try {
      const { orders } = await open();
      await orders.snapshot([]);
      await orders.update(card());
      const nudge = screen.getByRole("status", { name: "비서 알림" });
      expect(nudge).toHaveAttribute("data-floating", "true");
      expect(screen.getByRole("complementary", { name: "비서와 가게" })).not.toContainElement(nudge);
    } finally {
      vi.unstubAllGlobals();
    }
  });
});
