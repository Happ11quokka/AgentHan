import { useRef, useState } from "react";
import { httpApi, type AssistantApi } from "./api";
import { ChatPane } from "./components/ChatPane";
import { LeftNav, type Quick } from "./components/LeftNav";
import { Sidecar, type Tab } from "./components/Sidecar";
import type { Period } from "./types";
import { useConversation } from "./useConversation";
import { useLoad } from "./useLoad";

export default function App({ api = httpApi }: { api?: AssistantApi }) {
  const chat = useConversation(api);
  const [tab, setTab] = useState<Tab>("shop");
  const [period, setPeriod] = useState<Period>("week");
  const [navOpen, setNavOpen] = useState(false);
  const [sideOpen, setSideOpen] = useState(false);
  const photoInput = useRef<HTMLInputElement>(null);

  // 리뷰·통계는 그 탭이 열려 있을 때만 읽고, 대화가 바뀌거나 비서 답이 오면 다시 읽는다.
  const id = chat.sessionId;
  const turn = `${id}:${chat.messages.length}`;
  const reviews = useLoad(tab === "reviews" && id ? () => api.reviews(id) : null, `${turn}:${tab}`);
  const report = useLoad(tab === "stats" && id ? () => api.report(id, period) : null, `${turn}:${tab}:${period}`);

  const quick = (q: Quick) => {
    setNavOpen(false);
    setTab(q.tab);
    setSideOpen(true); // 좁은 화면에서는 접힌 사이드카를 펼쳐 보여 준다
    if (q.action_id) void chat.act({ action_id: q.action_id, args: {}, label: q.label });
    else if (q.ask) void chat.send(q.ask);
    else photoInput.current?.click();
  };

  return (
    <div className="app" data-nav={navOpen ? "open" : "closed"} data-side={sideOpen ? "open" : "closed"}>
      <LeftNav
        recent={chat.recent}
        current={chat.sessionId}
        busy={chat.busy}
        onNew={() => {
          setNavOpen(false);
          void chat.newChat();
        }}
        onOpen={(target) => {
          setNavOpen(false);
          void chat.open(target);
        }}
        onQuick={quick}
      />
      <ChatPane
        chat={chat}
        photoInput={photoInput}
        sideOpen={sideOpen}
        onToggleNav={() => setNavOpen((o) => !o)}
        onToggleSide={() => setSideOpen((o) => !o)}
      />
      <Sidecar
        tab={tab}
        onTab={setTab}
        place={chat.place}
        pending={chat.pending}
        log={chat.log}
        online={chat.online}
        busy={chat.busy}
        reviews={reviews}
        report={report}
        period={period}
        onPeriod={setPeriod}
        onAction={(action) => void chat.act(action)}
        onClose={() => setSideOpen(false)}
      />
      <button
        type="button"
        className="scrim"
        aria-label="닫기"
        tabIndex={-1}
        onClick={() => {
          setNavOpen(false);
          setSideOpen(false);
        }}
      />
    </div>
  );
}
