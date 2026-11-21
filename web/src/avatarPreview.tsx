import { StrictMode, useState } from "react";
import { createRoot } from "react-dom/client";
import { AVATAR_STATES, type AvatarState } from "./avatarState";
import { AgentAvatar } from "./components/AgentAvatar";
import "./styles.css";

export function AvatarPreview() {
  const [state, setState] = useState<AvatarState>(() =>
    AVATAR_STATES.find((value) => value === new URLSearchParams(location.search).get("state")) ?? "idle",
  );
  return (
    <main className="avatar-preview">
      <h1>사이드카 아바타 · 112 × 112</h1>
      <section className="profile">
        <AgentAvatar state={state} />
        <div><strong>사장님 비서</strong><span>{state}</span></div>
      </section>
      <div className="avatar-controls" role="group" aria-label="아바타 상태">
        {AVATAR_STATES.map((value) => (
          <button key={value} type="button" data-avatar-state={value} aria-pressed={state === value} onClick={() => setState(value)}>
            {value}
          </button>
        ))}
      </div>
    </main>
  );
}

createRoot(document.getElementById("root")!).render(<StrictMode><AvatarPreview /></StrictMode>);
