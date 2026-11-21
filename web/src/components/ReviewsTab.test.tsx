import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { useState } from "react";
import { describe, expect, test, vi } from "vitest";
import { INBOX, inboxWith } from "../test/fakeApi";
import type { ReplyError, ReplyOp, ReviewRow, ReviewsView } from "../types";
import { ReviewsTab } from "./ReviewsTab";

type Reply = (review: ReviewRow, op: ReplyOp) => ReviewRow | ReplyError | Promise<ReviewRow | ReplyError>;

/** 서버 대신 reply가 돌려준 리뷰로 카드 하나만 바꾼다(App의 onReply와 같은 모양). */
function Harness({ initial, reply }: { initial: ReviewsView; reply: Reply }) {
  const [data, setData] = useState(initial);
  const onReply = async (review: ReviewRow, op: ReplyOp) => {
    const out = await reply(review, op);
    if ("reason" in out) return out;
    setData((d) => ({ ...d, reviews: d.reviews.map((r) => (r.id === out.id ? out : r)) }));
    return null;
  };
  return <ReviewsTab loaded={{ data, failed: false, reload: () => {}, update: () => {} }} busy={false} onReply={onReply} />;
}

function setup(reply: Reply = (r) => r, initial: ReviewsView = INBOX) {
  const spy = vi.fn(reply);
  const user = userEvent.setup();
  render(<Harness initial={initial} reply={spy} />);
  const card = (text: string) => within(screen.getByText(text).closest("li") as HTMLElement);
  return { user, spy, card };
}
const box = (c: ReturnType<typeof within>) => c.getByRole("textbox", { name: "답글 고치기" });

describe("리뷰 카드 초안은 바로 고칠 수 있다", () => {
  test("초안이 처음부터 입력칸에 보이고 [고치기]·[취소] 버튼은 없다", () => {
    const { card } = setup();
    const c = card("커피가 맛있어요.");
    expect(box(c)).toHaveValue("다음에도 찾아 주세요.");
    expect(c.queryByRole("button", { name: "고치기" })).not.toBeInTheDocument();
    expect(c.queryByRole("button", { name: "취소" })).not.toBeInTheDocument();
    expect(c.getByRole("button", { name: "올리기" })).toBeEnabled();
  });

  test("고치지 않으면 초안 그대로(text 없이) 올린다", async () => {
    const { user, spy, card } = setup();
    await user.click(card("커피가 맛있어요.").getByRole("button", { name: "올리기" }));
    expect(spy).toHaveBeenLastCalledWith(expect.objectContaining({ id: "r18" }), { op: "post" });
  });

  test("고친 글(앞뒤 공백 뺀 것)이 그대로 올라가고, 다 지우면 올리기가 막힌다", async () => {
    const { user, spy, card } = setup();
    const c = card("커피가 맛있어요.");
    await user.clear(box(c));
    expect(c.getByRole("button", { name: "올리기" })).toBeDisabled();
    await user.type(box(c), "  커피 칭찬 고맙습니다!  ");
    await user.click(c.getByRole("button", { name: "올리기" }));
    expect(spy).toHaveBeenLastCalledWith(expect.objectContaining({ id: "r18" }), { op: "post", text: "커피 칭찬 고맙습니다!" });
  });

  test("확인 필요 초안은 이유를 보이고 그대로는 못 올리지만, 고치면 올릴 수 있다", async () => {
    const { user, spy, card } = setup();
    const c = card("케이크가 너무 달아요.");
    expect(c.getByText("확인이 필요해요: 전화번호 등 개인정보")).toBeInTheDocument();
    expect(c.getByRole("button", { name: "올리기" })).toBeDisabled();
    await user.clear(box(c));
    await user.type(box(c), "불편 드려 죄송합니다.");
    await user.click(c.getByRole("button", { name: "올리기" }));
    expect(spy).toHaveBeenLastCalledWith(expect.objectContaining({ id: "r19" }), { op: "post", text: "불편 드려 죄송합니다." });
  });

  test("다시 쓰기로 새 초안이 오면 입력칸도 새 초안으로 바뀐다(고치던 중이어도)", async () => {
    const redraft: Reply = (r, op) => (op.op === "redraft" ? { ...r, draft: { text: "또 오세요.", status: "ready", issues: [] } } : r);
    const { user, spy, card } = setup(redraft);
    const c = card("커피가 맛있어요.");
    await user.type(box(c), " 고치던 중");
    await user.click(c.getByRole("button", { name: "더 짧게" }));
    expect(spy).toHaveBeenLastCalledWith(expect.objectContaining({ id: "r18" }), { op: "redraft", hint: "shorter" });
    await waitFor(() => expect(box(c)).toHaveValue("또 오세요."));
    await user.click(c.getByRole("button", { name: "올리기" }));
    expect(spy).toHaveBeenLastCalledWith(expect.objectContaining({ id: "r18" }), { op: "post" });
  });

  test("올리기가 실패하면 고친 글은 입력칸에 그대로 남는다", async () => {
    const { user, card } = setup((r, op) => (op.op === "post" ? { reason: "checks", message: "검사", issues: ["전화번호 등 개인정보"] } : r));
    const c = card("커피가 맛있어요.");
    await user.clear(box(c));
    await user.type(box(c), "010-1234-5678");
    await user.click(c.getByRole("button", { name: "올리기" }));
    expect(await c.findByRole("alert")).toHaveTextContent("확인이 필요해요: 전화번호 등 개인정보");
    expect(box(c)).toHaveValue("010-1234-5678");
  });

  test("[건너뛰기]는 그대로 있다", async () => {
    const { user, spy, card } = setup();
    await user.click(card("커피가 맛있어요.").getByRole("button", { name: "건너뛰기" }));
    expect(spy).toHaveBeenLastCalledWith(expect.objectContaining({ id: "r18" }), { op: "skip" });
  });
});

describe("[답글 초안 받기]는 카드 안에서 초안을 받는다", () => {
  const noDraft = inboxWith("r18", { draft: null }, { waiting: 2 });

  test("대기함 경로(op: draft)로 받고, 쓰는 동안 카드에만 '초안 쓰는 중…'을 보인 뒤 입력칸에 초안을 띄운다", async () => {
    let finish!: (r: ReviewRow) => void;
    const { user, spy, card } = setup(() => new Promise<ReviewRow>((resolve) => (finish = resolve)), noDraft);
    const c = card("커피가 맛있어요.");
    expect(c.queryByRole("textbox", { name: "답글 고치기" })).not.toBeInTheDocument();
    await user.click(c.getByRole("button", { name: "답글 초안 받기" }));
    expect(spy).toHaveBeenLastCalledWith(expect.objectContaining({ id: "r18" }), { op: "draft" });
    expect(c.getByRole("status")).toHaveTextContent("초안 쓰는 중…");
    expect(c.queryByRole("button", { name: "답글 초안 받기" })).not.toBeInTheDocument();
    // 다른 카드는 그대로
    expect(card("메도빅 최고예요.").queryByText("초안 쓰는 중…")).not.toBeInTheDocument();
    finish({ ...noDraft.reviews.find((r) => r.id === "r18")!, draft: { text: "커피 칭찬 고맙습니다.", status: "ready", issues: [] } });
    await waitFor(() => expect(box(c)).toHaveValue("커피 칭찬 고맙습니다."));
    expect(c.queryByText("초안 쓰는 중…")).not.toBeInTheDocument();
    await user.click(c.getByRole("button", { name: "올리기" }));
    expect(spy).toHaveBeenLastCalledWith(expect.objectContaining({ id: "r18" }), { op: "post" });
  });

  test("초안을 못 쓰면 카드에 이유를 보이고 다시 누를 수 있다", async () => {
    const { user, spy, card } = setup(() => ({ reason: "draft_failed", message: "초안을 쓰지 못했어요.", issues: [] }), noDraft);
    const c = card("커피가 맛있어요.");
    await user.click(c.getByRole("button", { name: "답글 초안 받기" }));
    expect(await c.findByRole("alert")).toHaveTextContent("초안을 쓰지 못했어요.");
    await user.click(c.getByRole("button", { name: "답글 초안 받기" }));
    expect(spy).toHaveBeenCalledTimes(2);
  });
});

describe("리뷰 정기 보고 줄 (#98)", () => {
  const loaded = { data: INBOX, failed: false, reload: () => {}, update: () => {} };
  const ON = { enabled: true, schedule: "매주 월요일 09:00", next_run: "2026-11-09T09:00", text: "매주 월요일 09:00 리뷰 요약 받는 중" };
  const none = async () => null;

  test("켜져 있으면 설정 한 줄만, 꺼져 있으면 켜는 말을 함께 보인다", () => {
    const { rerender } = render(<ReviewsTab loaded={loaded} busy={false} onReply={none} report={ON} onRunReport={none} />);
    const line = () => within(screen.getByRole("region", { name: "리뷰 정기 보고" }));
    expect(line().getByText("매주 월요일 09:00 리뷰 요약 받는 중")).toBeInTheDocument();
    expect(line().queryByText(/라고 하면 켤 수 있어요/)).not.toBeInTheDocument();
    rerender(<ReviewsTab loaded={loaded} busy={false} onReply={none} report={null} onRunReport={none} />);
    expect(line().getByText("정기 보고 꺼짐")).toBeInTheDocument();
    expect(line().getByText(/라고 하면 켤 수 있어요/)).toBeInTheDocument();
  });

  test("[지금 받아 보기]는 한 번만 보내고 실패하면 이유를 보인다", async () => {
    let finish: (v: string | null) => void = () => {};
    const run = vi.fn(() => new Promise<string | null>((r) => (finish = r)));
    const user = userEvent.setup();
    render(<ReviewsTab loaded={loaded} busy={false} onReply={none} report={ON} onRunReport={run} />);
    await user.click(screen.getByRole("button", { name: "지금 받아 보기" }));
    expect(screen.getByRole("button", { name: "보고 만드는 중…" })).toBeDisabled();
    finish("리뷰 보고를 받지 못했어요. 다시 시도해 주세요.");
    expect(await screen.findByRole("alert")).toHaveTextContent("리뷰 보고를 받지 못했어요");
    expect(run).toHaveBeenCalledTimes(1);
  });

  test("[지금 받아 보기]가 없으면 정기 보고 줄도 없다", () => {
    render(<ReviewsTab loaded={loaded} busy={false} onReply={none} />);
    expect(screen.queryByRole("region", { name: "리뷰 정기 보고" })).not.toBeInTheDocument();
  });
});
