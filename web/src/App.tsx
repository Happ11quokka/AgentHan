import { useRef, useState } from "react";
import { httpApi, type AssistantApi } from "./api";
import { ChatPane } from "./components/ChatPane";
import { LeftNav, type Quick } from "./components/LeftNav";
import { Sidecar, type Tab } from "./components/Sidecar";
import { useConversation } from "./useConversation";

export default function App({ api = httpApi }: { api?: AssistantApi }) {
  const chat = useConversation(api);
  const [tab, setTab] = useState<Tab>("shop");
  const [navOpen, setNavOpen] = useState(false);
  const [sideOpen, setSideOpen] = useState(false);
  const photoInput = useRef<HTMLInputElement>(null);

  const quick = (q: Quick) => {
    setNavOpen(false);
    if (q.tab) {
      setTab(q.tab);
      setSideOpen(true); // 좁은 화면에서는 접힌 사이드카를 펼쳐 보여 준다
    }
    if (q.ask) void chat.send(q.ask);
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
        onOpen={(id) => {
          setNavOpen(false);
          void chat.open(id);
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
